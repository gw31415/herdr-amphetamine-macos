#!/usr/bin/env python3
"""Install (or refresh) the user LaunchAgent that runs the Amphetamine monitor.

Renders launchagents/com.herdr.amphetamine.monitor.plist.template with absolute
paths and writes it to ~/Library/LaunchAgents/, then (re)loads it with launchctl.

Idempotent: running twice replaces the plist and restarts the service. Safe to
run even if a previous service is loaded; bootout errors are ignored.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

LABEL = "com.herdr.amphetamine.monitor"
DOMAIN = f"gui/{os.getuid()}"


def plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def template_path() -> Path:
    return plugin_root() / "launchagents" / f"{LABEL}.plist.template"


def resolve_herdr_bin() -> str:
    """Find the absolute herdr path to bake into the LaunchAgent.

    A user LaunchAgent runs with a minimal PATH that does not include
    ~/.local/bin, so we resolve herdr now (HERDR_BIN_PATH or PATH) and write the
    absolute path into the plist's HERDR_BIN_PATH.
    """
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr")
    if not herdr:
        sys.exit(
            "Could not find herdr on PATH. Run `herdr plugin action invoke "
            "install-launchagent` from a shell that has herdr, or set "
            "HERDR_BIN_PATH to the absolute herdr binary before installing."
        )
    return str(Path(herdr).resolve())


def render(home_dir: Path) -> str:
    python = os.environ.get("HERDR_AMPHETAMINE_PYTHON", "/usr/bin/python3")
    text = template_path().read_text()
    return (
        text
        .replace("__PYTHON__", python)
        .replace("__PLUGIN_ROOT__", str(plugin_root()))
        .replace("__HOME__", str(home_dir))
        .replace("__HERDR_BIN__", resolve_herdr_bin())
    )


def run(cmd: list) -> tuple:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stderr or "").strip()


def main() -> int:
    home_dir = Path.home()
    launch_dir = home_dir / "Library" / "LaunchAgents"
    log_dir = home_dir / "Library" / "Logs" / "herdr-amphetamine"
    state_dir = home_dir / "Library" / "Application Support" / "herdr-amphetamine"
    plist_dest = launch_dir / f"{LABEL}.plist"

    launch_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Seed config.json with defaults if absent so the TUI has a file to edit and
    # the daemon starts armed with the documented tunables. Never overwrite an
    # existing config (the user may have customized it).
    if not config.config_path().exists():
        config.save_config_file(config.default_config())
        print(f"[install] wrote default config: {config.config_path()}")

    plist_dest.write_text(render(home_dir))
    print(f"[install] wrote plist: {plist_dest}")

    # Unload any previously-loaded agent; ignore errors if it was not loaded.
    code, err = run(["launchctl", "bootout", DOMAIN, str(plist_dest)])
    if code == 0:
        print("[install] unloaded previous LaunchAgent.")
    elif err:
        print(f"[install] bootout (ignored if not loaded): {err}")

    code, err = run(["launchctl", "bootstrap", DOMAIN, str(plist_dest)])
    if code != 0 and err:
        print(f"[install] bootstrap note: {err}", file=sys.stderr)

    run(["launchctl", "kickstart", "-k", f"{DOMAIN}/{LABEL}"])
    print(f"[install] started LaunchAgent: {LABEL}")

    print(f"[install] stdout log: {log_dir}/monitor.out.log")
    print(f"[install] stderr log: {log_dir}/monitor.err.log")
    print(f"[install] state file: {state_dir}/state.json")
    print("[install] verify with:")
    print("    launchctl print gui/$UID/com.herdr.amphetamine.monitor")
    print("    tail -n 50 ~/Library/Logs/herdr-amphetamine/monitor.out.log")
    print("    python3 scripts/monitor.py --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
