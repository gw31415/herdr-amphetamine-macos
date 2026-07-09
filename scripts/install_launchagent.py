#!/usr/bin/env python3
"""Install (or refresh) this session's Amphetamine monitor LaunchAgent.

Renders launchagents/com.herdr.amphetamine.monitor.plist.template with absolute
paths, writes it to ~/Library/LaunchAgents/, and registers it with launchctl.

Idempotent for the current herdr session; other session LaunchAgents use
different labels and paths.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import launchagent  # noqa: E402

def plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def template_path() -> Path:
    return plugin_root() / "launchagents" / "com.herdr.amphetamine.monitor.plist.template"


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


def render(home_dir: Path, p: dict) -> str:
    python = os.environ.get("HERDR_AMPHETAMINE_PYTHON", "/usr/bin/python3")
    socket_path = os.environ.get("HERDR_SOCKET_PATH", "")
    text = template_path().read_text()
    return (
        text
        .replace("__LABEL__", p["label"])
        .replace("__PYTHON__", python)
        .replace("__PLUGIN_ROOT__", str(plugin_root()))
        .replace("__HOME__", str(home_dir))
        .replace("__CONFIG_DIR__", str(p["config_dir"]))
        .replace("__STATE_DIR__", str(p["state_dir"]))
        .replace("__LOG_DIR__", str(p["log_dir"]))
        .replace("__PLIST__", str(p["plist"]))
        .replace("__HERDR_BIN__", resolve_herdr_bin())
        .replace("__HERDR_SOCKET__", socket_path)
    )


def main() -> int:
    home_dir = Path.home()
    p = launchagent.paths(home_dir)
    launch_dir = home_dir / "Library" / "LaunchAgents"
    log_dir = p["log_dir"]
    config_dir = p["config_dir"]
    state_dir = p["state_dir"]
    plist_dest = p["plist"]

    launch_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    # Pin the resolved per-session dirs so config.config_path()/state_dir() land
    # in the right place for seeding here and for any subprocess we spawn.
    os.environ["HERDR_AMPHETAMINE_CONFIG_DIR"] = str(config_dir)
    os.environ["HERDR_AMPHETAMINE_STATE_DIR"] = str(state_dir)

    # Seed config.json with defaults if absent so the TUI has a file to edit and
    # the daemon starts armed with the documented tunables. Never overwrite an
    # existing config (the user may have customized it).
    if not config.config_path().exists():
        config.save_config_file(config.default_config())
        print(f"[install] wrote default config: {config.config_path()}")

    plist_dest.write_text(render(home_dir, p))
    print(f"[install] wrote plist: {plist_dest}")

    launchagent.run(["launchctl", "bootout", launchagent.domain(), str(plist_dest)])
    # A previous stop stores a persistent disabled override for this label.
    # On recent macOS, bootstrapping a disabled LaunchAgent can fail with a
    # generic "Input/output error". Temporarily enable before bootstrap, then
    # stop() below leaves the install action in its documented stopped state.
    launchagent.run(["launchctl", "enable", launchagent.service_target(p["label"])])
    code, err = launchagent.run(["launchctl", "bootstrap", launchagent.domain(), str(plist_dest)])
    if code != 0 and err:
        print(f"[install] bootstrap note (ignored if already loaded): {err}", file=sys.stderr)

    launchagent.stop(p["label"])
    print(f"[install] registered LaunchAgent: {p['label']} (stopped)")

    print(f"[install] stdout log: {log_dir}/monitor.out.log")
    print(f"[install] stderr log: {log_dir}/monitor.err.log")
    print(f"[install] config file: {config_dir}/config.json")
    print(f"[install] state file: {state_dir}/state.json")
    print("[install] verify with:")
    print(f"    launchctl print gui/$UID/{p['label']}")
    print(f"    tail -n 50 {log_dir}/monitor.out.log")
    print("    python3 scripts/monitor.py --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
