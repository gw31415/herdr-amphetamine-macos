#!/usr/bin/env python3
"""Uninstall the Amphetamine monitor LaunchAgent.

Bootouts the service (ignoring errors if it was not loaded), removes the plist,
and optionally removes logs/state when called with --cleanup. Never ends an
Amphetamine session here; the running monitor owns that and ends its own session
when it receives SIGTERM from launchctl bootout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.herdr.amphetamine.monitor"
DOMAIN = f"gui/{os.getuid()}"


def main() -> int:
    cleanup = "--cleanup" in sys.argv
    home_dir = Path.home()
    plist_dest = home_dir / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log_dir = home_dir / "Library" / "Logs" / "herdr-amphetamine"
    state_dir = home_dir / "Library" / "Application Support" / "herdr-amphetamine"

    proc = subprocess.run(
        ["launchctl", "bootout", DOMAIN, str(plist_dest)],
        capture_output=True,
        text=True,
    )
    msg = (proc.stderr or "").strip()
    if proc.returncode == 0:
        print(f"[uninstall] unloaded LaunchAgent: {LABEL}")
    elif msg:
        print(f"[uninstall] bootout (ignored if not loaded): {msg}")

    if plist_dest.exists():
        plist_dest.unlink()
        print(f"[uninstall] removed plist: {plist_dest}")
    else:
        print(f"[uninstall] no plist at {plist_dest}; nothing to remove.")

    if cleanup:
        for d in (log_dir, state_dir):
            if d.exists():
                shutil.rmtree(d)
                print(f"[uninstall] removed {d}")
        print("[uninstall] logs and state removed.")
    else:
        print(f"[uninstall] kept logs ({log_dir}) and state ({state_dir}).")
        print("[uninstall] pass --cleanup to remove them.")

    print("[uninstall] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
