#!/usr/bin/env python3
"""Thin, auditable wrappers around Amphetamine's AppleScript interface.

Each public function maps to exactly one AppleScript verb from Amphetamine's
scripting dictionary (inspect with `sdef /Applications/Amphetamine.app`). All
calls go through `/usr/bin/osascript`; nothing drives the UI or uses
Accessibility automation.

Closed-display mode naming (IMPORTANT - do not "fix" without reading this):
  Amphetamine's "closed-display mode" is the feature that keeps the Mac awake
  while the lid/display is closed (clamshell mode). Per Amphetamine's sdef:
    * `enable closed display mode`  -> "allow closed-display mode"  -> the Mac
      WILL stay awake when the display is closed. This is what we want.
    * `disable closed display mode` -> "prevent closed-display mode" -> the Mac
      is allowed to sleep when the display is closed. This is the OPPOSITE.
  The original PLANS.md inverted this and called `disable`. That was wrong.
  prevent_sleep_when_display_closed() therefore calls `enable closed display
  mode`. Enabling this feature may surface a one-time warning prompt in
  Amphetamine; see docs/manual-test.md for the manual setup that silences it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

DEFAULT_APP_PATH = "/Applications/Amphetamine.app"

# Amphetamine silently no-ops `start new session` until its UI has been
# activated. On the target Mac, osascript exits 0 but no session is created
# (`session time remaining` returns -3) unless Amphetamine is activated first.
# Activating and giving it a moment to foreground makes start reliable, even
# from a headless LaunchAgent.
_ACTIVATE_SETTLE_SECONDS = 1.5


class AmphetamineError(RuntimeError):
    """Raised when an osascript call to Amphetamine fails."""


def _app_path() -> str:
    return os.environ.get("AMPHETAMINE_APP_PATH", DEFAULT_APP_PATH)


def _run_applescript(script: str) -> str:
    """Run a single AppleScript statement and return stripped stdout.

    Raises AmphetamineError on any non-zero exit so callers can react (the
    monitor treats this as a transient error and retries).
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise AmphetamineError(f"could not run osascript: {exc}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        low = stderr.lower()
        # macOS Automation denial: Apple event error -1743 / "not authorized".
        if "not authorized" in low or "-1743" in low or "automation" in low:
            raise AmphetamineError(
                "macOS denied Automation permission for Amphetamine. Grant it in "
                "System Settings -> Privacy & Security -> Automation (allow the "
                "launching process to control Amphetamine). Detail: " + stderr
            )
        raise AmphetamineError(
            f"osascript exited {proc.returncode} for script: {script!r}; stderr: {stderr}"
        )
    return proc.stdout.strip()


def is_amphetamine_available() -> bool:
    """Return True if the Amphetamine app bundle is present.

    This is a cheap, prompt-free existence check. We address Amphetamine by name
    in AppleScript (`tell application "Amphetamine"`), so the bundle location
    only needs to be a known install. Override the path with AMPHETAMINE_APP_PATH.
    """
    return Path(_app_path()).exists()


def is_session_active() -> bool:
    """Return Amphetamine's current session state (True/False)."""
    out = _run_applescript('tell application "Amphetamine" to session is active')
    return out.lower() == "true"


def _start_options(display_sleep_allowed: bool, duration_minutes) -> str:
    """Build the Amphetamine `start new session` options record.

    duration_minutes None/0 -> infinite (duration:0, interval:0, per sdef).
    duration_minutes > 0    -> a finite session of that many whole minutes.
    """
    flag = "true" if display_sleep_allowed else "false"
    if duration_minutes and duration_minutes > 0:
        return "{duration:%d, interval:minutes, displaySleepAllowed:%s}" % (
            int(duration_minutes), flag)
    return "{duration:0, interval:0, displaySleepAllowed:%s}" % flag


def start_session(display_sleep_allowed: bool = False, duration_minutes=None) -> None:
    """Start an Amphetamine session (finite minutes, or infinite when 0/None).

    displaySleepAllowed:false prevents display sleep during the session.
    Note: `start new session` ends any pre-existing session first, so callers
    must only invoke this when they intend to own the resulting session.

    Focus-preserving: we first try `start new session` WITHOUT activating
    Amphetamine, so the user's foreground app keeps focus. Amphetamine can
    silently no-op the start while idle / app-napped; if is_session_active()
    still reports false, we fall back to `activate` once and retry.
    """
    script = (
        "tell application \"Amphetamine\" to start new session with options "
        + _start_options(display_sleep_allowed, duration_minutes)
    )
    # First attempt: no activate (keeps the user's focus).
    try:
        _run_applescript(script)
        time.sleep(0.3)
        if is_session_active():
            return
    except AmphetamineError:
        pass  # fall through to the activate-and-retry fallback
    # Amphetamine ignored the start (idle / app-nap). Activate once and retry.
    try:
        _run_applescript('tell application "Amphetamine" to activate')
        time.sleep(_ACTIVATE_SETTLE_SECONDS)
    except AmphetamineError:
        pass
    _run_applescript(script)


def session_time_remaining() -> int:
    """Return seconds remaining in the current Amphetamine session.

    Per the sdef: >=0 = seconds left (0 means infinite duration), -1 = trigger
    session, -2 = app/date session, -3 = no active session. Returns -3 on any
    parse failure so callers can treat it as "no session".
    """
    out = _run_applescript('tell application "Amphetamine" to session time remaining')
    try:
        return int(out)
    except ValueError:
        return -3


def end_session() -> None:
    """End the current Amphetamine session."""
    _run_applescript('tell application "Amphetamine" to end session')


def prevent_sleep_when_display_closed() -> None:
    """Tell Amphetamine to keep the Mac awake when the display is closed.

    Calls `enable closed display mode` (see the module docstring: "closed-display
    mode" = keep-awake-while-closed, so we ENABLE it). This is best-effort: a
    failure here (e.g. an unpersisted one-time warning prompt) must not prevent
    the monitor from holding a normal session, so callers catch AmphetamineError.
    """
    _run_applescript('tell application "Amphetamine" to enable closed display mode')
