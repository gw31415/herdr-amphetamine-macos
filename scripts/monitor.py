#!/usr/bin/env python3
"""herdr Amphetamine macOS sleep-prevention monitor.

Observes herdr agent status and tops up Amphetamine time only while at least one
agent is `working`. Uses hysteresis (start/stop grace periods) so status flicker
does not rapidly toggle Amphetamine. It never ends an Amphetamine session: when
agents go idle, the daemon simply stops extending and lets whatever session the
user configured expire naturally.

This process is launched by a session-scoped user LaunchAgent while that herdr
session has agents. All human control — arm/disarm (enable/pause), settings,
status — happens through the TUI (`scripts/tui.py`), which writes `config.json`;
this daemon re-reads it every poll. When `armed=false` the daemon performs no
Amphetamine side effects. SIGHUP forces an immediate config reload.

Modes:
  python3 monitor.py             # daemon loop (used by the LaunchAgent)
  python3 monitor.py --daemon    # same, explicit
  python3 monitor.py --status    # print observed agents + state, change nothing
  python3 monitor.py --once      # one iteration, then exit (manual testing)

Stdlib-only so it runs under /usr/bin/python3 from a LaunchAgent.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import amphetamine_ctl
import config
import launchagent

# Valid monitor states. Hysteresis lives in the pending_* states.
STATES = ("off", "pending_on", "on", "pending_off", "error")

# Built-in defaults; runtime values come from config.json (see config.py). These
# constants remain as fall-backs and for tests that construct Config directly.
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_START_GRACE_SECONDS = 5.0
DEFAULT_STOP_GRACE_SECONDS = 30.0
DEFAULT_SESSION_MINUTES = 1.0  # Amphetamine session length; 0 = infinite
DEFAULT_EXTEND_THRESHOLD_MINUTES = 2.0  # extend when time remaining is below this


class HerdrError(RuntimeError):
    """Raised when herdr cannot be queried."""


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O) - the unit-tested core of the state machine.
# --------------------------------------------------------------------------- #
def any_agent_working(agent_statuses: list) -> bool:
    """Return True if any status is exactly 'working'."""
    return any(s == "working" for s in agent_statuses)


def top_up_duration_minutes(remaining_seconds: int, add_minutes: float) -> float:
    """Return the target duration needed to add ``add_minutes``.

    Amphetamine starts a session for a target duration; it does not expose an
    additive "extend by N minutes" verb. To implement "add N minutes", request
    the current remaining time plus N minutes, rounded up to whole minutes
    because Amphetamine's scripting API uses minute granularity. ``0`` keeps the
    documented infinite-session meaning.
    """
    if add_minutes <= 0:
        return 0.0
    return float(math.ceil(max(0, remaining_seconds) / 60.0 + add_minutes))


def next_monitor_state(
    current_state: str,
    observed_working: bool,
    elapsed_seconds: float,
    start_grace: float,
    stop_grace: float,
) -> str:
    """Return the next monitor state given an observation and elapsed time.

    elapsed_seconds is the time spent in current_state (the daemon resets it to
    zero on every transition). Transitions:

      off        --working-->                 pending_on
      pending_on --not working-->             off            (flicker cancelled)
      pending_on --working, grace elapsed-->  on
      on         --not working-->             pending_off
      pending_off --working-->                on             (resume, keep session)
      pending_off --not working, grace-->     off
      error      --(reconciled by daemon)-->  off / on
    """
    cs = current_state
    if cs == "off":
        return "pending_on" if observed_working else "off"
    if cs == "pending_on":
        if not observed_working:
            return "off"
        return "on" if elapsed_seconds >= start_grace else "pending_on"
    if cs == "on":
        return "on" if observed_working else "pending_off"
    if cs == "pending_off":
        if observed_working:
            return "on"
        return "off" if elapsed_seconds >= stop_grace else "pending_off"
    if cs == "error":
        # The daemon reconciles out of error using ownership before re-entering
        # normal flow; this fallback keeps the function total.
        return "pending_on" if observed_working else "off"
    return "off"


def handle_transition(
    old: str,
    new: str,
    owned_session: bool,
    is_active_fn: Callable[[], bool],
    start_fn: Callable[[bool], None],
    prevent_closed_fn: Callable[[], None],
    log_fn: Callable[[str], None],
):
    """Perform Amphetamine side effects for a state transition.

    Returns (owned_session, ok). ok is False when an Amphetamine call failed;
    the caller should then enter the error state. Side effects happen only when
    entering 'on' (start/top-up). Pending and off transitions never touch
    Amphetamine.

    Safety rule: this monitor never calls ``end session``. ``owned_session`` is
    kept only as legacy state/UI metadata and must not grant permission to end a
    user-created session.
    """
    if new == "on":
        if old == "pending_off":
            # Resume: work came back during the stop grace, so the session is
            # still active. Keep metadata unchanged and do not call Amphetamine.
            return owned_session, True
        # old == "pending_on": actually start a session now.
        try:
            already_active = is_active_fn()
        except Exception as exc:  # AmphetamineError or similar
            log_fn(f"is_session_active failed while starting: {exc}")
            return owned_session, False
        if already_active:
            log_fn("Amphetamine session already active; not owning it (will not end it).")
            return False, True
        try:
            start_fn(False)
        except Exception as exc:
            log_fn(f"start_session failed: {exc}")
            return owned_session, False
        owned_session = True
        log_fn("Started owned Amphetamine session.")
        # Closed-display keep-awake is best-effort: a failure here must not undo
        # the session we just successfully started.
        try:
            prevent_closed_fn()
        except Exception as exc:
            log_fn(f"prevent_sleep_when_display_closed failed (non-fatal): {exc}")
        return owned_session, True

    if new == "off":
        if old == "pending_on":
            # Cancel: work did not survive the start grace, so nothing was
            # started and there is nothing to end.
            return owned_session, True
        # old == "pending_off": stop grace elapsed. Do not end anything; simply
        # stop extending while agents are idle. The existing session (manual or
        # plugin-created) will expire according to Amphetamine's own timer.
        if owned_session:
            log_fn("Agents idle; stopped extending Amphetamine session (left active session as-is).")
        else:
            log_fn("Agents idle; leaving Amphetamine as-is.")
        return False, True

    # off->pending_on and on->pending_off: no Amphetamine action.
    return owned_session, True


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #
def default_state() -> dict:
    return {
        "monitor_state": "off",
        "owned_session": False,
        "last_agent_working": False,
        "agent_count": -1,
        "last_transition_unix": 0,
        "last_error": None,
    }


def load_state(path: Path) -> dict:
    """Load monitor state, returning defaults if missing or corrupt."""
    state = default_state()
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                state.update(data)
    except (OSError, ValueError):
        # Corrupt or unreadable state: start fresh rather than crash.
        pass
    return state


def save_state(path: Path, state: dict) -> None:
    """Atomically write monitor state (write temp file then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


@dataclass
class MonitorCtx:
    monitor_state: str = "off"
    owned_session: bool = False
    last_transition: float = 0.0
    last_agent_working: bool = False
    agent_count: int = -1
    last_error: Optional[str] = None
    backoff: float = DEFAULT_POLL_SECONDS


def load_ctx(path: Path) -> MonitorCtx:
    s = load_state(path)
    try:
        last_t = float(s.get("last_transition_unix") or 0)
    except (TypeError, ValueError):
        last_t = 0.0
    try:
        agent_count = int(s.get("agent_count", -1))
    except (TypeError, ValueError):
        agent_count = -1
    return MonitorCtx(
        monitor_state=s.get("monitor_state", "off"),
        owned_session=bool(s.get("owned_session", False)),
        last_transition=last_t,
        last_agent_working=bool(s.get("last_agent_working", False)),
        agent_count=agent_count,
        last_error=s.get("last_error"),
    )


def save_ctx(path: Path, ctx: MonitorCtx) -> None:
    save_state(
        path,
        {
            "monitor_state": ctx.monitor_state,
            "owned_session": ctx.owned_session,
            "last_agent_working": ctx.last_agent_working,
            "agent_count": ctx.agent_count,
            "last_transition_unix": ctx.last_transition,
            "last_error": ctx.last_error,
        },
    )


# --------------------------------------------------------------------------- #
# herdr observation
# --------------------------------------------------------------------------- #
def _agent_statuses_from_response(data: dict) -> list:
    agents = (data.get("result") or {}).get("agents") or []
    return [a.get("agent_status", "unknown") for a in agents]


def _get_agent_statuses_from_socket(socket_path: str) -> list:
    req = {"id": "herdr-amphetamine:agent:list", "method": "agent.list", "params": {}}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(socket_path)
            sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
            chunks = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    if chunks:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except OSError as exc:
        raise HerdrError(f"herdr socket unavailable at {socket_path!r}: {exc}") from exc
    try:
        data = json.loads(b"".join(chunks).decode("utf-8").strip())
    except ValueError as exc:
        raise HerdrError(f"could not parse herdr socket response as JSON: {exc}") from exc
    if "error" in data:
        err = data.get("error") or {}
        raise HerdrError(f"herdr socket error: {err.get('message') or err}")
    return _agent_statuses_from_response(data)


def get_agent_statuses(herdr_bin: str) -> list:
    """Read herdr agent statuses from the session socket.

    The LaunchAgent is installed from inside herdr, which pins HERDR_SOCKET_PATH.
    If that env is absent this process is outside a herdr session and must not
    probe user-facing CLI paths that may trigger terminal notifications.
    Raises HerdrError if herdr cannot be run or its output cannot be parsed.
    """
    socket_path = os.environ.get("HERDR_SOCKET_PATH")
    if not socket_path:
        raise HerdrError("HERDR_SOCKET_PATH is not set; not running from a herdr session")
    return _get_agent_statuses_from_socket(socket_path)


# --------------------------------------------------------------------------- #
# Config (built from config.json + env overrides)
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    herdr_bin: str
    poll_seconds: float
    start_grace: float
    stop_grace: float
    state_path: Path
    top_up_minutes: float = DEFAULT_SESSION_MINUTES
    top_up_threshold_minutes: float = DEFAULT_EXTEND_THRESHOLD_MINUTES
    armed: bool = True
    prevent_closed_display_sleep: bool = True
    display_sleep_allowed: bool = False


def resolve_state_path() -> Path:
    """The runtime state file: <state_dir>/state.json."""
    return config.state_dir() / "state.json"


def load_config() -> Config:
    """Build the runtime Config from config.json (file -> env -> validate)."""
    raw = config.load_resolved()
    # Propagate the app path into the env so amphetamine_ctl picks it up.
    os.environ["AMPHETAMINE_APP_PATH"] = raw["amphetamine_app_path"]
    herdr_bin = raw["herdr_bin_path"] or shutil.which("herdr") or "herdr"
    return Config(
        herdr_bin=herdr_bin,
        poll_seconds=raw["poll_seconds"],
        start_grace=raw["start_grace_seconds"],
        stop_grace=raw["stop_grace_seconds"],
        state_path=resolve_state_path(),
        top_up_minutes=raw["top_up_minutes"],
        top_up_threshold_minutes=raw["top_up_threshold_minutes"],
        armed=raw["armed"],
        prevent_closed_display_sleep=raw["prevent_closed_display_sleep"],
        display_sleep_allowed=raw["display_sleep_allowed"],
    )


# --------------------------------------------------------------------------- #
# Logging (stdout -> LaunchAgent log file)
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def _auto_stop_if_empty(ctx: MonitorCtx) -> bool:
    """Stop only this session's LaunchAgent when no agents remain."""
    if os.environ.get("HERDR_AMPHETAMINE_AUTO_UNLOAD") != "1" or ctx.agent_count != 0:
        return False
    label_name = os.environ.get("HERDR_AMPHETAMINE_LABEL")
    if not label_name:
        return False
    log("No agents remain; stopping this session LaunchAgent.")
    launchagent.stop(label_name)
    return True


# --------------------------------------------------------------------------- #
# One iteration of the monitor (pure with respect to injected time)
# --------------------------------------------------------------------------- #
def iterate(cfg: Config, ctx: MonitorCtx, now: float) -> MonitorCtx:
    """Run one monitor iteration and return the updated context.

    `now` is injected so this is unit-testable; the daemon passes time.time().
    """
    state = ctx.monitor_state
    owned = ctx.owned_session
    last_t = ctx.last_transition if ctx.last_transition else now
    backoff = cfg.poll_seconds
    last_error: Optional[str] = None

    # Master switch: when disarmed via the TUI, do nothing.
    if not cfg.armed:
        return MonitorCtx(
            monitor_state="off",
            owned_session=False,
            last_transition=now,
            last_agent_working=ctx.last_agent_working,
            agent_count=ctx.agent_count,
            last_error=last_error,
            backoff=cfg.poll_seconds,
        )

    def _start(duration_minutes=None):
        amphetamine_ctl.start_session(
            cfg.display_sleep_allowed,
            cfg.top_up_minutes if duration_minutes is None else duration_minutes,
        )

    prevent_fn = (
        amphetamine_ctl.prevent_sleep_when_display_closed
        if cfg.prevent_closed_display_sleep
        else (lambda: None)
    )

    if not amphetamine_ctl.is_amphetamine_available():
        last_error = f"Amphetamine.app not found at {amphetamine_ctl.DEFAULT_APP_PATH}"
        log(last_error + "; waiting.")
        return MonitorCtx(
            monitor_state="error",
            owned_session=owned,
            last_transition=last_t,
            last_agent_working=ctx.last_agent_working,
            agent_count=ctx.agent_count,
            last_error=last_error,
            backoff=min(60.0, ctx.backoff * 2),
        )

    # Recover from error: probe reachability, reconcile ownership, resume.
    if state == "error":
        try:
            active = amphetamine_ctl.is_session_active()
        except amphetamine_ctl.AmphetamineError as exc:
            last_error = str(exc)
            log(f"Still cannot reach Amphetamine: {exc}")
            return MonitorCtx(
                monitor_state="error",
                owned_session=owned,
                last_transition=last_t,
                last_agent_working=ctx.last_agent_working,
                agent_count=ctx.agent_count,
                last_error=last_error,
                backoff=min(60.0, ctx.backoff * 2),
            )
        if owned and not active:
            owned = False
            log("Owned session is no longer active; cleared ownership.")
        state = "on" if (owned or active) else "off"
        last_t = now
        backoff = cfg.poll_seconds
        log(f"Recovered from error; resuming in state '{state}'.")

    # Observe herdr. herdr being down is NOT an error state - treat as idle.
    try:
        statuses = get_agent_statuses(cfg.herdr_bin)
        agent_count = len(statuses)
    except HerdrError as exc:
        log(f"herdr unavailable ({exc}); treating as no working agents.")
        statuses = []
        agent_count = ctx.agent_count
    working = any_agent_working(statuses)

    new_state = next_monitor_state(
        state, working, now - last_t, cfg.start_grace, cfg.stop_grace
    )

    if new_state != state:
        owned, ok = handle_transition(
            state,
            new_state,
            owned,
            amphetamine_ctl.is_session_active,
            lambda _disp=False: _start(),
            prevent_fn,
            log,
        )
        if ok:
            log(f"Transition: {state} -> {new_state} (working={working}).")
            state = new_state
            last_t = now
        else:
            log(f"Transition {state} -> {new_state} failed; entering error state.")
            state = "error"
            last_t = now
            last_error = "amphetamine control failure"
    elif state == "on":
        # Reconcile the on-state with reality using the session's remaining
        # time (one osascript tells us both "is there a session" and "how
        # long left"). -3 means no active session.
        try:
            remaining = amphetamine_ctl.session_time_remaining()
        except amphetamine_ctl.AmphetamineError as exc:
            log(f"Could not query session time remaining: {exc}")
            remaining = None

        if remaining is None:
            pass  # unreadable this cycle; try again next poll
        elif remaining == -3:
            # No active session.
            log("No active Amphetamine session while agents work; starting a short session.")
            try:
                _start()
                prevent_fn()
                owned = True
                last_t = now
            except amphetamine_ctl.AmphetamineError as exc:
                log(f"Start failed: {exc}; entering error state.")
                state = "error"
                last_t = now
                last_error = "session start failure"
        elif remaining == 0:
            pass  # infinite session; nothing to extend
        elif 0 < remaining < cfg.top_up_threshold_minutes * 60:
            # Finite session is close to expiry -> top it up by starting a new
            # short session. This applies to manual sessions too; we only add
            # time while agents are working and never end the session ourselves.
            duration_minutes = top_up_duration_minutes(remaining, cfg.top_up_minutes)
            log(f"Session has {remaining}s left (< {int(cfg.top_up_threshold_minutes)}m); "
                f"adding {int(cfg.top_up_minutes)}m "
                f"(setting duration to {int(duration_minutes)}m).")
            try:
                _start(duration_minutes)
                prevent_fn()
                owned = True
                last_t = now
            except amphetamine_ctl.AmphetamineError as exc:
                log(f"Extend failed: {exc}")

    return MonitorCtx(
        monitor_state=state,
        owned_session=owned,
        last_transition=last_t,
        last_agent_working=working,
        agent_count=agent_count,
        last_error=last_error,
        backoff=backoff,
    )


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def print_status(cfg: Config) -> None:
    """Print observed agents + Amphetamine state + persisted monitor state."""
    print(f"herdr bin:       {cfg.herdr_bin}")
    print(f"state file:      {cfg.state_path}")
    print(f"config file:     {config.config_path()}")
    print(f"armed:           {cfg.armed}")
    print(f"poll/grace:      {cfg.poll_seconds}s / start {cfg.start_grace}s / stop {cfg.stop_grace}s")
    print(f"top-up:          add {cfg.top_up_minutes:g}m{' (infinite)' if cfg.top_up_minutes == 0 else ''} "
          f"below {cfg.top_up_threshold_minutes:g}m")
    print(f"closed-display:  {'prevent sleep when closed' if cfg.prevent_closed_display_sleep else 'off'}")
    print(f"amphetamine app: {amphetamine_ctl.DEFAULT_APP_PATH} "
          f"({'present' if amphetamine_ctl.is_amphetamine_available() else 'MISSING'})")
    try:
        statuses = get_agent_statuses(cfg.herdr_bin)
        working = sum(1 for s in statuses if s == "working")
        print(f"herdr agents:    {len(statuses)} observed, {working} working")
        for s in statuses:
            print(f"    - {s}")
    except HerdrError as exc:
        print(f"herdr agents:    unavailable ({exc})")
    try:
        print(f"session active:  {amphetamine_ctl.is_session_active()}")
    except amphetamine_ctl.AmphetamineError as exc:
        print(f"session active:  unknown ({exc})")
    print("monitor state:")
    print(json.dumps(load_state(cfg.state_path), indent=2))


def run_once(cfg: Config) -> None:
    """Run a single iteration and exit. Does not end the session on exit."""
    ctx = load_ctx(cfg.state_path)
    ctx.backoff = cfg.poll_seconds
    ctx = iterate(cfg, ctx, time.time())
    save_ctx(cfg.state_path, ctx)
    print(json.dumps({
        "monitor_state": ctx.monitor_state,
        "owned_session": ctx.owned_session,
        "last_agent_working": ctx.last_agent_working,
        "last_error": ctx.last_error,
    }, indent=2))


def run_daemon(initial_cfg: Config) -> None:
    """Run the monitor loop until SIGTERM/SIGINT.

    Reloads config.json every cycle (so TUI edits take effect) and on SIGHUP
    (immediate). The session LaunchAgent is stopped when no agents remain.
    """
    cfg = initial_cfg
    ctx = load_ctx(cfg.state_path)
    ctx.backoff = cfg.poll_seconds

    # Startup ownership safety: never end a session that was already active.
    try:
        if amphetamine_ctl.is_session_active():
            if ctx.owned_session:
                log("Active session found at startup; clearing ownership to be safe.")
            ctx.owned_session = False
    except amphetamine_ctl.AmphetamineError as exc:
        log(f"Startup is_session_active failed: {exc}; will retry in loop.")

    stop = threading.Event()
    reload_now = threading.Event()

    def _stop_handler(signum, _frame):
        log(f"Received signal {signum}; stopping after current iteration.")
        stop.set()

    def _reload_handler(_signum, _frame):
        reload_now.set()

    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)
    try:
        signal.signal(signal.SIGHUP, _reload_handler)
    except (AttributeError, ValueError, OSError):
        pass  # SIGHUP unavailable in this environment; polling still reloads.

    log(f"Monitor started (poll={cfg.poll_seconds}s start_grace={cfg.start_grace}s "
        f"stop_grace={cfg.stop_grace}s top_up={cfg.top_up_minutes:g}m "
        f"top_up_below={cfg.top_up_threshold_minutes:g}m armed={cfg.armed} "
        f"state={cfg.state_path}).")
    try:
        while not stop.is_set():
            if reload_now.is_set():
                reload_now.clear()
                try:
                    cfg = load_config()
                    log("Config reloaded.")
                except Exception as exc:  # never let a reload kill the daemon
                    log(f"Config reload failed ({exc}); keeping previous config.")
            ctx = iterate(cfg, ctx, time.time())
            save_ctx(cfg.state_path, ctx)
            if _auto_stop_if_empty(ctx):
                return
            delay = ctx.backoff if ctx.monitor_state == "error" else cfg.poll_seconds
            stop.wait(max(1.0, delay))
    finally:
        ctx.owned_session = False
        # We are stopping, so we are no longer guarding: reflect that in state
        # so the TUI / --status do not show a stale "on".
        ctx.monitor_state = "off"
        ctx.last_error = None
        save_ctx(cfg.state_path, ctx)
        log("Monitor stopped.")


def main(argv=None) -> int:
    if "HERDR_AMPHETAMINE_STATE_DIR" not in os.environ:
        os.environ["HERDR_AMPHETAMINE_STATE_DIR"] = str(launchagent.paths()["state_dir"])
    parser = argparse.ArgumentParser(
        description="Amphetamine sleep-guard daemon for herdr."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true",
                      help="Print observed agents and monitor state; change nothing.")
    mode.add_argument("--once", action="store_true",
                      help="Run a single iteration and exit (manual testing).")
    mode.add_argument("--daemon", action="store_true",
                      help="Run the monitor loop forever (used by the LaunchAgent).")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.status:
        print_status(cfg)
    elif args.once:
        run_once(cfg)
    else:
        run_daemon(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
