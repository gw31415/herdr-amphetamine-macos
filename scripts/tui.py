#!/usr/bin/env python3
"""Interactive (curses) TUI for the Amphetamine Sleep Guard.

This is the human control surface for the session LaunchAgent daemon. It:

  * shows live status (monitor state, working agents, Amphetamine session),
  * arms / disarms the guard (enable / pause) by flipping `armed` in config.json,
  * edits every setting (poll, grace, session, paths, closed-display, ...),
  * installs / uninstalls this session's LaunchAgent,
  * tails the daemon's recent log lines.

It NEVER starts or ends an Amphetamine session directly — session ownership stays
in the daemon. The TUI only writes `config.json` (intent) and reads `state.json`
+ Amphetamine read-only. The daemon re-reads `config.json` every poll and on
SIGHUP, so a change applies within seconds (the TUI sends SIGHUP best-effort to
make it immediate).

Stdlib-only (curses ships with macOS system Python). If the terminal does not
support curses or stdin/stdout are not a TTY, it falls back to printing the same
status block non-interactively (like `monitor.py --status`).

Run standalone (`python3 scripts/tui.py`) or as a herdr pane/action.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import amphetamine_ctl  # noqa: E402
import config  # noqa: E402
import launchagent  # noqa: E402
import monitor  # noqa: E402

if "HERDR_AMPHETAMINE_STATE_DIR" not in os.environ:
    os.environ["HERDR_AMPHETAMINE_STATE_DIR"] = str(launchagent.paths()["state_dir"])

try:  # curses is optional; the non-TTY path does not need it
    import curses
except ImportError:  # pragma: no cover - extremely rare on macOS
    curses = None

REFRESH_MS = 1000  # curses input timeout; one redraw/keystroke poll per second
LOG_TAIL = 8

# Mouse support: each interactive row is registered as a ClickRegion and
# looked up by y-coordinate on mouse click. The action bar at the bottom is
# split into segments.
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ClickRegion:
    y: int
    action: str           # key sent to handle_key: " ", "c", "p", "i", etc.
    x_start: int = 0
    x_end: int = -1       # inclusive; -1 = rest of line
    label: str = ""

@dataclass
class ClickLayout:
    rows: list = field(default_factory=list)     # ClickRegion[]
    action_segs: list = field(default_factory=list)  # ClickRegion[]

_LAYOUT = ClickLayout()

# Single-key shortcuts -> (config_key, label). Numerics are parsed with float.
NUMERIC_KEYS = {
    "p": ("poll_seconds", "poll seconds"),
    "g": ("start_grace_seconds", "start grace seconds"),
    "s": ("stop_grace_seconds", "stop grace seconds"),
    "m": ("top_up_minutes", "top-up minutes (0 = infinite)"),
    "e": ("top_up_threshold_minutes", "top-up threshold minutes"),
}
PATH_KEYS = {
    "b": ("herdr_bin_path", "herdr bin path (blank = env/PATH)"),
    "a": ("amphetamine_app_path", "Amphetamine app path"),
}
TOGGLE_KEYS = {
    "c": "prevent_closed_display_sleep",
    "d": "display_sleep_allowed",
}

# Map display tags (e.g. "[Space]") back to the key character handle_key expects.
_TAG_TO_KEY = {
    "[Space]": " ",
    "[c]": "c",
    "[d]": "d",
    "[p]": "p",
    "[g]": "g",
    "[s]": "s",
    "[m]": "m",
    "[e]": "e",
    "[b]": "b",
    "[a]": "a",
}

_COLORS_READY = False


# --------------------------------------------------------------------------- #
# Color helper (returns a plain attr; 0 until colors are initialized)
# --------------------------------------------------------------------------- #
def _attr(name: str) -> int:
    if not _COLORS_READY or curses is None:
        return 0
    pairs = {"green": 1, "yellow": 2, "red": 3, "cyan": 4}
    if name == "dim":
        return curses.A_DIM
    cp = pairs.get(name, 0)
    return curses.color_pair(cp) if cp else 0


# --------------------------------------------------------------------------- #
# Data gathering (all read-only; never raises into the render loop)
# --------------------------------------------------------------------------- #
def _state() -> dict:
    try:
        return monitor.load_state(monitor.resolve_state_path())
    except Exception:  # noqa: BLE001
        return monitor.default_state()


def _amph():
    """Return (available, active). active is None when the query failed."""
    try:
        available = amphetamine_ctl.is_amphetamine_available()
    except Exception:  # noqa: BLE001
        available = False
    try:
        active = amphetamine_ctl.is_session_active()
    except Exception:  # noqa: BLE001
        active = None
    return available, active


def _agents(herdr_bin):
    """Return (total, working, ok)."""
    try:
        statuses = monitor.get_agent_statuses(herdr_bin)
        return len(statuses), sum(1 for s in statuses if s == "working"), True
    except monitor.HerdrError:
        return 0, 0, False


def _tail(n=LOG_TAIL):
    log_path = launchagent.paths()["log_dir"] / "monitor.out.log"
    try:
        if not log_path.exists():
            return []
        return log_path.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _daemon_pid() -> Optional[int]:
    """Best-effort: return this session daemon's pid."""
    try:
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{launchagent.label()}"],
            capture_output=True, text=True,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                return int(line.removeprefix("pid = ").strip())
    except (OSError, subprocess.SubprocessError):
        pass
    except ValueError:
        pass
    return None


def _nudge_daemon() -> None:
    """Best-effort SIGHUP so the daemon reloads config.json immediately.

    If pgrep finds nothing or fails, do nothing — the daemon re-reads config
    every poll (default 5s) anyway.
    """
    try:
        pid = _daemon_pid()
        if pid is not None:
            os.kill(pid, signal.SIGHUP)
    except (OSError, subprocess.SubprocessError):
        pass


def collect():
    """Gather everything the render loop needs. Returns a dict; never raises."""
    cfg = monitor.load_config()
    available, active = _amph()
    total, working, herdr_ok = _agents(cfg.herdr_bin)
    return {
        "cfg": cfg,
        "cfg_dict": config.load_resolved(),
        "state": _state(),
        "available": available,
        "active": active,
        "total": total,
        "working": working,
        "herdr_ok": herdr_ok,
        "tail": _tail(),
        "daemon": _daemon_pid() is not None,
        "log_path": launchagent.paths()["log_dir"] / "monitor.out.log",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# --------------------------------------------------------------------------- #
# Mutations (write config.json only)
# --------------------------------------------------------------------------- #
def _save_and_nudge(updates: dict) -> str:
    """Merge `updates` into the current config.json, save atomically, nudge.

    Returns a short status string for the footer.
    """
    try:
        cfg = config.load_config_file()  # fresh read so we never clobber
        cfg.update(updates)
        config.save_config_file(cfg)
        _nudge_daemon()
        return "saved (daemon notified)"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        return f"save failed: {exc}"


def toggle_armed() -> str:
    cur = config.load_resolved().get("armed", True)
    return _save_and_nudge({"armed": not cur})


def set_value(key, value) -> str:
    return _save_and_nudge({key: value})


def run_script(module: str) -> str:
    """Run install/uninstall_launchagent.py and capture its last output line."""
    script = Path(__file__).resolve().parent / module
    py = sys.executable or "/usr/bin/python3"
    try:
        proc = subprocess.run(
            [py, str(script)], capture_output=True, text=True, timeout=30,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0:
            return out.splitlines()[-1] if out else "done"
        return f"exit {proc.returncode}: {(err or out).splitlines()[-1:]}"
    except Exception as exc:  # noqa: BLE001
        return f"failed: {exc}"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _state_badge(state, armed):
    """Return (text, attr) for the headline armed/state badge."""
    if not armed:
        return "DISARMED", _attr("cyan")
    if state == "on":
        return "ARMED  [ ON ]", _attr("green")
    if state == "pending_on":
        return "ARMED  [ ON · starting ]", _attr("yellow")
    if state == "pending_off":
        return "ARMED  [ ON · stopping ]", _attr("yellow")
    if state == "error":
        return "ARMED  [ ERROR ]", _attr("red")
    return "ARMED  [ OFF ]", _attr("dim")


def _row(stdscr, y, label, value, vattr=0):
    w = stdscr.getmaxyx()[1]
    text = f"  {label:<20}{value}"
    stdscr.addnstr(y, 0, text.ljust(w)[:w], w, vattr)


def _clickable_row(stdscr, y, tag, label, value, vattr=0):
    """Like _row but registers a ClickRegion for mouse clicks.

    The [tag] portion (e.g. "[Space]") is rendered bold so users see it as a
    clickable button; clicking anywhere on that row triggers the action.
    """
    w = stdscr.getmaxyx()[1]
    full = f"  {tag}  {label:<16}{value}"
    stdscr.addnstr(y, 0, full.ljust(w)[:w], w, vattr)
    # Overlay the tag as bold (if it fits)
    bold = curses.A_BOLD if curses is not None else 0
    tag_text = f"  {tag}"
    stdscr.addnstr(y, 0, tag_text, len(tag_text), bold | vattr)
    _LAYOUT.rows.append(ClickRegion(y=y, action=_TAG_TO_KEY.get(tag, ""),
                                     label=tag))


def _render_action_bar(stdscr, h, w, footer_msg):
    """Bottom action bar with clickable segments like herdr.

    Segments: [Space]ARMED  [i]INSTALL  [u]UNINSTALL  [r]REFRESH  [?]HELP  [q]QUIT
    """
    _LAYOUT.action_segs = []
    reverse = curses.A_REVERSE if curses is not None else 0
    segments = [
        (" [Space] ARM/PAUSE ", " "),
        (" [i] INSTALL ", "i"),
        (" [u] UNINSTALL ", "u"),
        (" [r] REFRESH ", "r"),
        (" [?] HELP ", "?"),
        (" [q] QUIT ", "q"),
    ]
    x = 0
    y = h - 1
    for text, action in segments:
        end = min(x + len(text), w)
        seg_w = end - x
        if seg_w <= 0:
            break
        stdscr.addnstr(y, x, text[:seg_w], seg_w, reverse)
        _LAYOUT.action_segs.append(ClickRegion(
            y=y, action=action, x_start=x, x_end=end - 1, label=text.strip()))
        x = end
    # Fill the rest of the line
    if x < w:
        stdscr.addnstr(y, x, " " * (w - x), w - x, reverse)
    if footer_msg:
        fm = footer_msg[:w]
        stdscr.addnstr(h - 2, 0, fm, len(fm), _attr("yellow"))


def render(stdscr, d, footer_msg):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    # Reset click layout at the start of each render
    _LAYOUT.rows = []
    cfg = d["cfg"]
    state = d["state"]
    mstate = state.get("monitor_state", "off")
    owned = state.get("owned_session", False)

    bold = curses.A_BOLD if curses is not None else 0

    badge_text, badge_attr = _state_badge(mstate, cfg.armed)
    bar = "=" * w
    dash = "-" * w
    y = 0
    stdscr.addnstr(y, 0, bar, w); y += 1
    stdscr.addnstr(y, 0, " Amphetamine Sleep Guard", w)
    stdscr.addnstr(y, max(0, w - len(badge_text) - 2), badge_text,
                   len(badge_text), badge_attr | bold)
    y += 1
    stdscr.addnstr(y, 0, bar, w); y += 1

    avail = "present" if d["available"] else "MISSING"
    if d["active"] is None:
        sa = "unknown"
    elif d["active"]:
        sa = "yes" + (" (managed/top-up)" if owned else "")
    else:
        sa = "no"
    sess = (f"add {cfg.top_up_minutes:g}m below {cfg.top_up_threshold_minutes:g}m"
            if cfg.top_up_minutes else "infinite")
    daemon = "running" if d["daemon"] else "not detected"

    _row(stdscr, y, "Monitor state:", mstate); y += 1
    _row(stdscr, y, "Daemon:", daemon,
         _attr("green") if d["daemon"] else _attr("dim")); y += 1
    agents = f"{d['working']} / {d['total']}" + ("" if d["herdr_ok"] else "  [herdr unreachable]")
    _row(stdscr, y, "Working agents:", agents); y += 1
    _row(stdscr, y, "Session active:", f"{sa}    length {sess}"); y += 1
    _row(stdscr, y, "Amphetamine app:", avail); y += 1
    _row(stdscr, y, "Last error:", str(state.get("last_error") or "-")); y += 1
    _row(stdscr, y, "Updated:", d["ts"]); y += 1

    if y < h:
        stdscr.addnstr(y, 0, dash, w); y += 1
    if y < h:
        stdscr.addnstr(y, 0, " Settings    (click a row or press a key to edit)",
                       w, bold); y += 1

    cd = d["cfg_dict"]

    def yn(b):
        return "ON" if b else "OFF"

    # Each tuple: (tag, label, value, attr)
    settings_rows = [
        ("[Space]", "armed:", yn(cd.get("armed", True)), badge_attr),
        ("[c]", "closed-display:", yn(cd.get("prevent_closed_display_sleep", True)), 0),
        ("[d]", "display sleep:", yn(cd.get("display_sleep_allowed", False)), 0),
        ("[p]", "poll (s):", f"{cd.get('poll_seconds'):g}", 0),
        ("[g]", "start grace (s):", f"{cd.get('start_grace_seconds'):g}", 0),
        ("[s]", "stop grace (s):", f"{cd.get('stop_grace_seconds'):g}", 0),
        ("[m]", "top-up (min):", f"{cd.get('top_up_minutes'):g}", 0),
        ("[e]", "top-up below (min):", f"{cd.get('top_up_threshold_minutes'):g}", 0),
        ("[b]", "herdr bin:", str(cd.get("herdr_bin_path") or "(env/PATH)"), 0),
        ("[a]", "Amphetamine:", str(cd.get("amphetamine_app_path")), 0),
    ]
    for tag, label, value, attr in settings_rows:
        if y >= h - 2:
            break
        _clickable_row(stdscr, y, tag, label, value, attr)
        y += 1

    if y < h:
        stdscr.addnstr(y, 0, dash, w); y += 1
    if y < h:
        stdscr.addnstr(y, 0, f" Recent  ({d['log_path']})", w, bold); y += 1
    if d["tail"]:
        for line in d["tail"]:
            if y >= h - 2:
                break
            _row(stdscr, y, "", line, _attr("dim") if _COLORS_READY else 0)
            y += 1
    elif y < h:
        dim = curses.A_DIM if curses is not None else 0
        stdscr.addnstr(y, 0, "  (no log yet — install the LaunchAgent to start the daemon)",
                       w, dim); y += 1

    if y < h:
        stdscr.addnstr(y, 0, bar, w); y += 1

    # Clickable action bar (bottom line)
    _render_action_bar(stdscr, h, w, footer_msg)
    stdscr.refresh()


def help_overlay(stdscr):
    h, w = stdscr.getmaxyx()
    lines = [
        "Amphetamine Sleep Guard — keymap",
        "",
        "  Space   arm / disarm the guard (enable / pause)",
        "  c       toggle closed-display keep-awake",
        "  d       toggle display-sleep-allowed",
        "  p g s m e   edit poll / start grace / stop grace / session / extend",
        "  b / a   edit herdr bin / Amphetamine app path",
        "  i / u   install / uninstall this session's LaunchAgent",
        "  r       refresh now",
        "  q       quit",
        "",
        "  Mouse: click any [tag] row or bottom-bar segment.",
        "",
        "Press any key to close.",
    ]
    stdscr.erase()
    for i, line in enumerate(lines):
        if i >= h:
            break
        stdscr.addnstr(i, 2, line, w - 2, curses.A_BOLD if i == 0 else 0)
    stdscr.refresh()
    stdscr.timeout(-1)       # block for the dismiss key
    stdscr.getch()
    stdscr.timeout(REFRESH_MS)


# --------------------------------------------------------------------------- #
# Bottom-line value editor
# --------------------------------------------------------------------------- #
def prompt_value(stdscr, label, initial=""):
    """Read one line at the bottom of the screen. Returns str or None on Esc."""
    h, w = stdscr.getmaxyx()
    buf = list(str(initial))
    prompt = f" {label}: "
    stdscr.timeout(-1)  # blocking input while editing
    try:
        while True:
            y = h - 1
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            text = prompt + "".join(buf)
            stdscr.addnstr(y, 0, text.ljust(w)[:w], w, curses.A_REVERSE)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (10, 13, curses.KEY_ENTER):  # Enter
                return "".join(buf)
            if ch == 27:  # Esc
                return None
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif 32 <= ch <= 126:
                buf.append(chr(ch))
    finally:
        stdscr.timeout(REFRESH_MS)


def edit_numeric(stdscr, cfg_key, label, cast=float):
    raw = prompt_value(stdscr, label, str(config.load_resolved().get(cfg_key, "")))
    if raw is None:
        return "cancelled"
    try:
        val = cast(raw)
    except ValueError:
        return f"invalid number: {raw!r}"
    return set_value(cfg_key, val)


def handle_key(stdscr, ch) -> str:
    """Return a footer status string (empty = nothing to say)."""
    if ch == ord(" "):
        return toggle_armed()
    if ch == ord("?"):
        help_overlay(stdscr)
        return ""
    if ch == ord("r"):
        return "refreshed"
    if ch == ord("i"):
        return run_script("install_launchagent.py")
    if ch == ord("u"):
        return run_script("uninstall_launchagent.py")
    c = chr(ch) if 0 <= ch < 0x110000 else ""
    if c in NUMERIC_KEYS:
        cfg_key, label = NUMERIC_KEYS[c]
        return edit_numeric(stdscr, cfg_key, label)
    if c in TOGGLE_KEYS:
        cur = config.load_resolved().get(TOGGLE_KEYS[c], True)
        return set_value(TOGGLE_KEYS[c], not cur)
    if c in PATH_KEYS:
        cfg_key, label = PATH_KEYS[c]
        raw = prompt_value(stdscr, label, str(config.load_resolved().get(cfg_key) or ""))
        if raw is None:
            return "cancelled"
        return set_value(cfg_key, raw.strip() or None)
    return ""


# --------------------------------------------------------------------------- #
# Mouse handling
# --------------------------------------------------------------------------- #
def _handle_mouse(stdscr) -> str:
    """Process a mouse event. Returns a footer status string (may be empty).

    Uses the ClickRegion entries registered during the last render(). A click
    on a registered row triggers its action (same as pressing the key).
    """
    if curses is None:
        return ""
    try:
        _, mx, my, _, bstate = curses.getmouse()
    except curses.error:
        return ""

    # Only handle button-1 (left click) presses
    if not (bstate & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED)):
        return ""

    # Check action-bar segments first (they live on the bottom line)
    for seg in _LAYOUT.action_segs:
        if seg.y != my:
            continue
        x_end = seg.x_end if seg.x_end >= 0 else 9999
        if seg.x_start <= mx <= x_end:
            if seg.action == "q":
                raise _QuitMouse()
            return handle_key(stdscr, ord(seg.action))

    # Check clickable rows
    for row in _LAYOUT.rows:
        if row.y == my and row.action:
            if row.action == " ":
                return handle_key(stdscr, ord(" "))
            return handle_key(stdscr, ord(row.action))

    return ""


class _QuitMouse(Exception):
    """Internal signal: mouse clicked the QUIT segment."""
    pass


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def _run(stdscr):
    global _COLORS_READY
    curses.curs_set(0)
    stdscr.timeout(REFRESH_MS)

    # Enable mouse support — all button events so clicks are captured
    if curses is not None:
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except (curses.error, AttributeError):
            pass  # mouse not supported; keyboard still works

    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        _COLORS_READY = True
    except Exception:  # noqa: BLE001 - colors are cosmetic
        _COLORS_READY = False

    footer = ""
    while True:
        try:
            d = collect()
        except Exception as exc:  # noqa: BLE001 - never crash the TUI
            d = {
                "cfg": monitor.load_config(), "cfg_dict": config.load_resolved(),
                "state": monitor.default_state(), "available": False, "active": None,
                "total": 0, "working": 0, "herdr_ok": False, "tail": [],
                "daemon": False, "log_path": launchagent.paths()["log_dir"] / "monitor.out.log",
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            footer = f"collect error: {exc}"
        try:
            render(stdscr, d, footer)
        except Exception:  # noqa: BLE001
            pass
        ch = stdscr.getch()
        if ch == ord("q"):
            break
        if curses is not None and ch == curses.KEY_MOUSE:
            try:
                footer = _handle_mouse(stdscr) or ""
            except _QuitMouse:
                break
            except Exception as exc:  # noqa: BLE001
                footer = f"error: {exc}"
            continue
        if ch != -1:
            try:
                footer = handle_key(stdscr, ch) or ""
            except Exception as exc:  # noqa: BLE001
                footer = f"error: {exc}"


def _fallback_status() -> int:
    """Non-interactive fallback when there is no TTY or curses is unavailable."""
    monitor.print_status(monitor.load_config())
    return 0


def main() -> int:
    if curses is None or not sys.stdout.isatty() or not sys.stdin.isatty():
        return _fallback_status()
    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
