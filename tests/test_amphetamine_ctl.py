#!/usr/bin/env python3
"""Unit tests for amphetamine_ctl. subprocess.run is mocked so no real Amphetamine
session is ever started."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import amphetamine_ctl  # noqa: E402


def _mock_run(stdout="", stderr="", returncode=0):
    return mock.patch(
        "amphetamine_ctl.subprocess.run",
        return_value=mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr),
    )


def _mock_run_routed(session_active=True):
    """Mock subprocess.run routing by script content.

    `session is active` queries return the given boolean text; everything else
    (start / activate / end / closed-display) returns empty stdout with exit 0.
    """
    def fake_run(argv, *a, **kw):
        script = argv[2] if len(argv) > 2 else ""
        out = "true" if (session_active and "session is active" in script) else ""
        return mock.Mock(returncode=0, stdout=out, stderr="")
    return mock.patch("amphetamine_ctl.subprocess.run", side_effect=fake_run)


class IsSessionActiveTests(unittest.TestCase):
    def _script(self, run_mock):
        return run_mock.call_args[0][0][2]  # argv = ["osascript", "-e", script]

    def test_true(self):
        with _mock_run(stdout="true\n") as run:
            self.assertTrue(amphetamine_ctl.is_session_active())
        self.assertIn("session is active", self._script(run))

    def test_false(self):
        with _mock_run(stdout="false"):
            self.assertFalse(amphetamine_ctl.is_session_active())

    def test_failure_raises(self):
        with _mock_run(stdout="", stderr="boom", returncode=1):
            with self.assertRaises(amphetamine_ctl.AmphetamineError):
                amphetamine_ctl.is_session_active()

    def test_automation_denial_message(self):
        with _mock_run(stdout="", stderr="...not authorized to send Apple events...", returncode=1):
            try:
                amphetamine_ctl.is_session_active()
            except amphetamine_ctl.AmphetamineError as exc:
                self.assertIn("Automation permission", str(exc))
            else:
                self.fail("expected AmphetamineError")


class StartSessionTests(unittest.TestCase):
    def _scripts(self, run_mock):
        return [call.args[0][2] for call in run_mock.call_args_list]

    def _last_start(self, run_mock):
        starts = [s for s in self._scripts(run_mock) if "start new session" in s]
        self.assertTrue(starts, "no start command issued")
        return starts[-1]

    def test_default_is_infinite_and_blocks_display_sleep(self):
        with _mock_run_routed(session_active=True) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session()
        script = self._last_start(run)
        self.assertIn("start new session", script)
        self.assertIn("duration:0", script)
        self.assertIn("interval:0", script)
        self.assertIn("displaySleepAllowed:false", script)

    def test_display_sleep_allowed_flag(self):
        with _mock_run_routed(session_active=True) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session(display_sleep_allowed=True)
        self.assertIn("displaySleepAllowed:true", self._last_start(run))

    def test_duration_minutes_uses_finite_options(self):
        with _mock_run_routed(session_active=True) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session(duration_minutes=10)
        self.assertIn("duration:10, interval:minutes", self._last_start(run))

    def test_duration_zero_is_infinite(self):
        with _mock_run_routed(session_active=True) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session(duration_minutes=0)
        self.assertIn("duration:0, interval:0", self._last_start(run))

    def test_no_activate_when_start_succeeds(self):
        # Focus-preserving: when the activate-less start takes, Amphetamine is
        # never activated (which would steal focus).
        with _mock_run_routed(session_active=True) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session()
        scripts = self._scripts(run)
        self.assertIn("start new session", scripts[0])  # first call is start
        self.assertFalse(any("activate" in s for s in scripts))

    def test_falls_back_to_activate_when_start_ignored(self):
        # If the activate-less start does not take (Amphetamine idle/app-nap),
        # activate once and retry the start.
        with _mock_run_routed(session_active=False) as run, mock.patch("amphetamine_ctl.time.sleep"):
            amphetamine_ctl.start_session()
        scripts = self._scripts(run)
        self.assertTrue(any("activate" in s for s in scripts))
        self.assertEqual(sum(1 for s in scripts if "start new session" in s), 2)


class EndSessionTests(unittest.TestCase):
    def test_calls_end_session(self):
        with _mock_run() as run:
            amphetamine_ctl.end_session()
        self.assertIn("end session", run.call_args[0][0][2])


class SessionTimeRemainingTests(unittest.TestCase):
    def test_parses_seconds(self):
        with _mock_run(stdout="300"):
            self.assertEqual(amphetamine_ctl.session_time_remaining(), 300)

    def test_no_session_is_minus_three(self):
        with _mock_run(stdout="-3"):
            self.assertEqual(amphetamine_ctl.session_time_remaining(), -3)

    def test_infinite_is_zero(self):
        with _mock_run(stdout="0"):
            self.assertEqual(amphetamine_ctl.session_time_remaining(), 0)

    def test_unparseable_is_minus_three(self):
        with _mock_run(stdout="huh"):
            self.assertEqual(amphetamine_ctl.session_time_remaining(), -3)


class ClosedDisplayTests(unittest.TestCase):
    """Critical regression guard: keeping the Mac awake when closed requires
    ENABLE closed display mode, not DISABLE."""

    def _script(self, run_mock):
        return run_mock.call_args[0][0][2]

    def test_uses_enable_not_disable(self):
        with _mock_run() as run:
            amphetamine_ctl.prevent_sleep_when_display_closed()
        script = self._script(run)
        self.assertIn("enable closed display mode", script)
        self.assertNotIn("disable closed display mode", script)


if __name__ == "__main__":
    unittest.main()
