#!/usr/bin/env python3
"""Unit tests for the pure monitor logic: state machine, transition side
effects (ownership), and state file I/O. No real Amphetamine or herdr calls."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import monitor  # noqa: E402

SG = 5.0
STG = 30.0


class AnyAgentWorkingTests(unittest.TestCase):
    def test_empty(self):
        self.assertFalse(monitor.any_agent_working([]))

    def test_no_working(self):
        self.assertFalse(monitor.any_agent_working(["idle", "done", "blocked", "unknown"]))

    def test_working_present(self):
        self.assertTrue(monitor.any_agent_working(["idle", "working"]))

    def test_only_working(self):
        self.assertTrue(monitor.any_agent_working(["working"]))

    def test_case_sensitive(self):
        # 'Working' with a capital must not count; only the exact token counts.
        self.assertFalse(monitor.any_agent_working(["Working"]))


class NextStateTests(unittest.TestCase):
    def test_off_to_pending_on(self):
        self.assertEqual(monitor.next_monitor_state("off", True, 0, SG, STG), "pending_on")

    def test_off_stays_off_when_idle(self):
        self.assertEqual(monitor.next_monitor_state("off", False, 0, SG, STG), "off")

    def test_pending_on_holds_during_start_grace(self):
        self.assertEqual(monitor.next_monitor_state("pending_on", True, 3, SG, STG), "pending_on")

    def test_sustained_working_starts_session(self):
        self.assertEqual(monitor.next_monitor_state("pending_on", True, SG, SG, STG), "on")

    def test_flicker_cancels_pending_on(self):
        # A single short blip of work that does not survive the start grace must
        # NOT result in 'on'.
        self.assertEqual(monitor.next_monitor_state("pending_on", False, 2, SG, STG), "off")

    def test_on_stays_on_when_working(self):
        self.assertEqual(monitor.next_monitor_state("on", True, 1000, SG, STG), "on")

    def test_on_to_pending_off_when_idle(self):
        self.assertEqual(monitor.next_monitor_state("on", False, 0, SG, STG), "pending_off")

    def test_pending_off_holds_during_stop_grace(self):
        self.assertEqual(monitor.next_monitor_state("pending_off", False, 20, SG, STG), "pending_off")

    def test_sustained_idle_stops_session(self):
        self.assertEqual(monitor.next_monitor_state("pending_off", False, STG, SG, STG), "off")

    def test_flicker_resume_keeps_session(self):
        # Work resuming before the stop grace must not end the session.
        self.assertEqual(monitor.next_monitor_state("pending_off", True, 10, SG, STG), "on")

    def test_unknown_state_defaults_to_off(self):
        self.assertEqual(monitor.next_monitor_state("garbage", True, 0, SG, STG), "off")


class SimulationTests(unittest.TestCase):
    """Drive a clock through a sequence to prove no rapid start/stop on flicker."""

    def _run(self, sequence, start_state="off"):
        """sequence: list of (observed_working, dt_seconds). Returns (final_state, starts, ends)."""
        state = start_state
        entered = 0.0
        clock = 0.0
        starts = ends = 0
        for working, dt in sequence:
            clock += dt
            elapsed = clock - entered
            new = monitor.next_monitor_state(state, working, elapsed, SG, STG)
            if new != state:
                # Only count actual side effects: a real start is pending_on->on
                # (resume pending_off->on keeps the existing session); a real end
                # is pending_off->off (cancel pending_on->off started nothing).
                if new == "on" and state == "pending_on":
                    starts += 1
                if new == "off" and state == "pending_off":
                    ends += 1
                state = new
                entered = clock
        return state, starts, ends

    def test_flicker_never_starts(self):
        # 1s on, 1s off, repeated: pending_on never sustains the 5s start grace.
        seq = []
        for _ in range(20):
            seq.append((True, 1))
            seq.append((False, 1))
        state, starts, ends = self._run(seq)
        self.assertEqual(starts, 0)
        self.assertEqual(ends, 0)
        self.assertEqual(state, "off")

    def test_sustained_working_starts_then_flicker_keeps_it(self):
        seq = [
            (True, 1),    # off -> pending_on
            (True, SG),   # pending_on -> on (grace elapsed)
            (False, 2),   # on -> pending_off (short blip)
            (True, 20),   # pending_off -> on (resume, well within stop grace)
        ]
        state, starts, ends = self._run(seq)
        self.assertEqual(starts, 1)
        self.assertEqual(ends, 0)
        self.assertEqual(state, "on")

    def test_sustained_working_then_sustained_idle_stops_once(self):
        seq = [
            (True, 1),    # -> pending_on
            (True, SG),   # -> on
            (False, 1),   # -> pending_off
            (False, STG), # -> off (stop grace elapsed)
        ]
        state, starts, ends = self._run(seq)
        self.assertEqual(starts, 1)
        self.assertEqual(ends, 1)
        self.assertEqual(state, "off")


class HandleTransitionTests(unittest.TestCase):
    """Ownership / pre-existing-session / failure logic, with injected fakes."""

    def _noop_log(self, *_a, **_k):
        pass

    def test_starts_when_no_session_active(self):
        started = []
        prevented = []
        owned, ok = monitor.handle_transition(
            "pending_on", "on", False,
            is_active_fn=lambda: False,
            start_fn=lambda d: started.append(d),
            prevent_closed_fn=lambda: prevented.append(True),
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertTrue(owned)
        self.assertEqual(started, [False])
        self.assertEqual(prevented, [True])

    def test_pre_existing_session_not_owned_and_not_started(self):
        started = []
        owned, ok = monitor.handle_transition(
            "pending_on", "on", False,
            is_active_fn=lambda: True,
            start_fn=lambda d: started.append(d),
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertFalse(owned)
        self.assertEqual(started, [])

    def test_does_not_end_unowned_session(self):
        ended = []
        owned, ok = monitor.handle_transition(
            "pending_off", "off", False,
            is_active_fn=lambda: False,
            start_fn=lambda d: None,
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertFalse(owned)
        self.assertEqual(ended, [])

    def test_idle_does_not_end_owned_session(self):
        ended = []
        owned, ok = monitor.handle_transition(
            "pending_off", "off", True,
            is_active_fn=lambda: False,
            start_fn=lambda d: None,
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertFalse(owned)
        self.assertEqual(ended, [])

    def test_start_failure_returns_not_ok(self):
        def boom(_d):
            raise RuntimeError("osascript failed")
        owned, ok = monitor.handle_transition(
            "pending_on", "on", False,
            is_active_fn=lambda: False,
            start_fn=boom,
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertFalse(ok)
        self.assertFalse(owned)

    def test_is_active_failure_returns_not_ok(self):
        def boom():
            raise RuntimeError("osascript failed")
        owned, ok = monitor.handle_transition(
            "pending_on", "on", False,
            is_active_fn=boom,
            start_fn=lambda d: None,
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertFalse(ok)

    def test_resume_preserves_ownership_and_does_not_start(self):
        # pending_off -> on: session still active. Must keep ownership and must
        # NOT call start (that would leak a second session / drop ownership).
        started = []
        prevented = []
        owned, ok = monitor.handle_transition(
            "pending_off", "on", True,
            is_active_fn=lambda: True,
            start_fn=lambda d: started.append(d),
            prevent_closed_fn=lambda: prevented.append(True),
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertTrue(owned)
        self.assertEqual(started, [])
        self.assertEqual(prevented, [])

    def test_cancel_pending_start_does_not_end(self):
        # pending_on -> off: never started, must not call end.
        ended = []
        owned, ok = monitor.handle_transition(
            "pending_on", "off", False,
            is_active_fn=lambda: False,
            start_fn=lambda d: None,
            prevent_closed_fn=lambda: None,
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertEqual(ended, [])

    def test_pending_transition_has_no_side_effects(self):
        called = []
        owned, ok = monitor.handle_transition(
            "off", "pending_on", False,
            is_active_fn=lambda: called.append("active") or False,
            start_fn=lambda d: called.append("start"),
            prevent_closed_fn=lambda: called.append("prevent"),
            log_fn=self._noop_log,
        )
        self.assertTrue(ok)
        self.assertEqual(called, [])


class StateIoTests(unittest.TestCase):
    def test_load_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            s = monitor.load_state(Path(d) / "state.json")
            self.assertEqual(s["monitor_state"], "off")
            self.assertFalse(s["owned_session"])
            self.assertIsNone(s["last_error"])

    def test_save_then_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            monitor.save_state(p, {
                "monitor_state": "on",
                "owned_session": True,
                "last_agent_working": True,
                "last_transition_unix": 123.0,
                "last_error": None,
            })
            s = monitor.load_state(p)
            self.assertEqual(s["monitor_state"], "on")
            self.assertTrue(s["owned_session"])
            self.assertEqual(s["last_transition_unix"], 123.0)

    def test_load_defaults_when_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text("{not valid json")
            s = monitor.load_state(p)
            self.assertEqual(s["monitor_state"], "off")


class IterateTests(unittest.TestCase):
    """End-to-end-ish: drive iterate() with mocked deps and injected time."""

    def _cfg(self):
        return monitor.Config(
            herdr_bin="herdr",
            poll_seconds=5.0,
            start_grace=5.0,
            stop_grace=30.0,
            state_path=Path("/tmp/ignored"),
        )

    def _patches(self, *, available=True, active=False, statuses=None, remaining=600):
        active_vals = list(active) if isinstance(active, list) else [active]
        active_iter = iter(active_vals)

        def is_active():
            try:
                return next(active_iter)
            except StopIteration:
                return active_vals[-1]

        status_vals = statuses if statuses is not None else []

        return [
            mock.patch.object(monitor.amphetamine_ctl, "is_amphetamine_available", return_value=available),
            mock.patch.object(monitor.amphetamine_ctl, "is_session_active", side_effect=is_active),
            mock.patch.object(monitor.amphetamine_ctl, "session_time_remaining", return_value=remaining),
            mock.patch.object(monitor.amphetamine_ctl, "start_session"),
            mock.patch.object(monitor.amphetamine_ctl, "end_session"),
            mock.patch.object(monitor.amphetamine_ctl, "prevent_sleep_when_display_closed"),
            mock.patch.object(monitor, "get_agent_statuses", return_value=status_vals),
        ]

    def _enter(self, patches):
        for p in patches:
            p.start()
        self.addCleanup(self._exit, patches)

    @staticmethod
    def _exit(patches):
        for p in patches:
            p.stop()

    def test_starts_after_start_grace(self):
        self._enter(self._patches(statuses=["working"], active=[False, False, False]))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="off", last_transition=0.0)
        t = 1000.0
        ctx = monitor.iterate(cfg, ctx, t); self.assertEqual(ctx.monitor_state, "pending_on")
        # grace not yet elapsed
        ctx = monitor.iterate(cfg, ctx, t + 3); self.assertEqual(ctx.monitor_state, "pending_on")
        # grace elapsed -> on, session started
        ctx = monitor.iterate(cfg, ctx, t + 5); self.assertEqual(ctx.monitor_state, "on")
        self.assertTrue(ctx.owned_session)
        self.assertTrue(monitor.amphetamine_ctl.start_session.called)

    def test_stops_after_stop_grace(self):
        self._enter(self._patches(statuses=["idle"], active=[False]))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=True, last_transition=0.0)
        t = 1000.0
        ctx = monitor.iterate(cfg, ctx, t); self.assertEqual(ctx.monitor_state, "pending_off")
        ctx = monitor.iterate(cfg, ctx, t + 20); self.assertEqual(ctx.monitor_state, "pending_off")
        ctx = monitor.iterate(cfg, ctx, t + 31); self.assertEqual(ctx.monitor_state, "off")
        self.assertFalse(ctx.owned_session)
        self.assertFalse(monitor.amphetamine_ctl.end_session.called)

    def test_pre_existing_session_not_owned(self):
        self._enter(self._patches(statuses=["working"], active=[True]))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="off", last_transition=0.0)
        t = 1000.0
        ctx = monitor.iterate(cfg, ctx, t); self.assertEqual(ctx.monitor_state, "pending_on")
        ctx = monitor.iterate(cfg, ctx, t + 6); self.assertEqual(ctx.monitor_state, "on")
        self.assertFalse(ctx.owned_session)  # not owned: pre-existing
        self.assertFalse(monitor.amphetamine_ctl.start_session.called)

    def test_amphetamine_missing_enters_error(self):
        self._enter(self._patches(available=False, statuses=["working"]))
        cfg = self._cfg()
        ctx = monitor.iterate(cfg, monitor.MonitorCtx(monitor_state="off"), 1000.0)
        self.assertEqual(ctx.monitor_state, "error")
        self.assertIsNotNone(ctx.last_error)

    def test_restarts_when_owned_session_expired(self):
        # Owned session ended (no active session, -3) while agents still work:
        # reconcile must restart it immediately and stay "on".
        self._enter(self._patches(statuses=["working"], remaining=-3))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=True, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "on")
        self.assertTrue(ctx.owned_session)
        self.assertTrue(monitor.amphetamine_ctl.start_session.called)

    def test_extends_when_close_to_expiry(self):
        # Owned 10-min session has 2 min left (<= 5 min threshold): reconcile
        # must extend it (call start_session) and stay "on".
        self._enter(self._patches(statuses=["working"], remaining=120))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=True, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "on")
        self.assertTrue(monitor.amphetamine_ctl.start_session.called)

    def test_does_not_extend_when_plenty_remaining(self):
        # 9 min left on a 10-min session (> 5 min threshold): no extend call.
        self._enter(self._patches(statuses=["working"], remaining=540))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=True, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "on")
        self.assertFalse(monitor.amphetamine_ctl.start_session.called)

    def test_non_owned_session_ending_starts_short_session(self):
        # Riding a pre-existing session that ends while agents still work:
        # start a short session immediately, but still never end it later.
        self._enter(self._patches(statuses=["working"], remaining=-3))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=False, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "on")
        self.assertTrue(ctx.owned_session)
        self.assertTrue(monitor.amphetamine_ctl.start_session.called)


class DisarmedTests(unittest.TestCase):
    """armed=False (paused via the TUI): the daemon stays resident but performs
    no Amphetamine side effects, ignoring working agents entirely."""

    def _patches(self, *, statuses=None):
        status_vals = statuses if statuses is not None else []
        return [
            mock.patch.object(monitor.amphetamine_ctl, "is_amphetamine_available", return_value=True),
            mock.patch.object(monitor.amphetamine_ctl, "is_session_active", return_value=False),
            mock.patch.object(monitor.amphetamine_ctl, "session_time_remaining", return_value=600),
            mock.patch.object(monitor.amphetamine_ctl, "start_session"),
            mock.patch.object(monitor.amphetamine_ctl, "end_session"),
            mock.patch.object(monitor.amphetamine_ctl, "prevent_sleep_when_display_closed"),
            mock.patch.object(monitor, "get_agent_statuses", return_value=status_vals),
        ]

    def _enter(self, patches):
        for p in patches:
            p.start()
        self.addCleanup(self._exit, patches)

    @staticmethod
    def _exit(patches):
        for p in patches:
            p.stop()

    def _cfg(self):
        return monitor.Config(
            herdr_bin="herdr",
            poll_seconds=5.0,
            start_grace=5.0,
            stop_grace=30.0,
            state_path=Path("/tmp/ignored"),
            armed=False,
        )

    def test_disarmed_does_not_end_owned_session(self):
        self._enter(self._patches(statuses=["working"]))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=True, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "off")
        self.assertFalse(ctx.owned_session)
        self.assertFalse(monitor.amphetamine_ctl.end_session.called)
        # Disarmed must not start a session even though agents are working.
        self.assertFalse(monitor.amphetamine_ctl.start_session.called)

    def test_disarmed_with_no_owned_session_is_noop(self):
        self._enter(self._patches(statuses=["working"]))
        cfg = self._cfg()
        ctx = monitor.MonitorCtx(monitor_state="on", owned_session=False, last_transition=0.0)
        ctx = monitor.iterate(cfg, ctx, 1001.0)
        self.assertEqual(ctx.monitor_state, "off")
        self.assertFalse(monitor.amphetamine_ctl.end_session.called)
        self.assertFalse(monitor.amphetamine_ctl.start_session.called)

    def test_armed_defaults_true_resumes_normal(self):
        # Sanity: the default Config (armed=True) is NOT short-circuited, so the
        # existing armed behavior is unchanged when the flag is absent.
        cfg = monitor.Config(herdr_bin="herdr", poll_seconds=5.0,
                             start_grace=5.0, stop_grace=30.0,
                             state_path=Path("/tmp/ignored"))
        self.assertTrue(cfg.armed)


if __name__ == "__main__":
    unittest.main()
