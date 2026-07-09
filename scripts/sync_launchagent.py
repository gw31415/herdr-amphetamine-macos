#!/usr/bin/env python3
"""Start/stop this session's installed LaunchAgent to match current agent count."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_launchagent  # noqa: E402
import launchagent  # noqa: E402
import monitor  # noqa: E402


def main() -> int:
    p = launchagent.paths()
    os.environ["HERDR_AMPHETAMINE_CONFIG_DIR"] = str(p["config_dir"])
    os.environ["HERDR_AMPHETAMINE_STATE_DIR"] = str(p["state_dir"])
    cfg = monitor.load_config()
    try:
        count = len(monitor.get_agent_statuses(cfg.herdr_bin))
    except monitor.HerdrError as exc:
        print(f"[sync] herdr unavailable; leaving LaunchAgent unchanged: {exc}")
        return 0
    if count:
        print(f"[sync] {count} agents; ensuring LaunchAgent is installed and started.")
        install_launchagent.main()
        # install_launchagent deliberately stops/disables the service after
        # registration. Re-enable before bootstrap so sync can recover from a
        # persisted launchctl disabled override left by a previous zero-agent
        # stop or uninstall.
        launchagent.run(["launchctl", "enable", launchagent.service_target(p["label"])])
        launchagent.run(["launchctl", "bootstrap", launchagent.domain(), str(p["plist"])])
        launchagent.start(p["label"])
        return 0
    print("[sync] no agents; stopping LaunchAgent but keeping it installed.")
    launchagent.stop(p["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
