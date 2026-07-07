#!/usr/bin/env python3
"""Per-herdr-session LaunchAgent naming helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

BASE_LABEL = "com.herdr.amphetamine.monitor"
SESSION_ENV_KEYS = ("HERDR_SESSION_NAME", "HERDR_SESSION")


class AmbiguousSessionError(RuntimeError):
    pass


def session_name() -> str:
    for key in SESSION_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            return val
    try:
        proc = subprocess.run(
            ["herdr", "session", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sessions = json.loads(proc.stdout).get("sessions", []) if proc.returncode == 0 else []
        running = [s for s in sessions if s.get("running")]
        if len(running) == 1:
            return str(running[0].get("name") or "default")
        if len(running) > 1:
            names = ", ".join(str(s.get("name") or "default") for s in running)
            raise AmbiguousSessionError(
                "could not infer current herdr session; set HERDR_SESSION_NAME. "
                f"running sessions: {names}"
            )
    except AmbiguousSessionError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return "default"


def session_slug(name: Optional[str] = None) -> str:
    name = name or session_name()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "default"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{slug}.{digest}"


def label() -> str:
    return f"{BASE_LABEL}.{session_slug()}"


def domain() -> str:
    return f"gui/{os.getuid()}"


def service_target(label_name: Optional[str] = None) -> str:
    return f"{domain()}/{label_name or label()}"


def run(cmd: list) -> tuple:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stderr or "").strip()


def start(label_name: Optional[str] = None) -> None:
    target = service_target(label_name)
    run(["launchctl", "enable", target])
    run(["launchctl", "kickstart", "-k", target])


def stop(label_name: Optional[str] = None) -> None:
    target = service_target(label_name)
    run(["launchctl", "disable", target])
    run(["launchctl", "kill", "TERM", target])


def _session_subdir(env_var: str, home: Path, slug: str) -> Path:
    """A per-session data subdir under a herdr-provided plugin directory.

    herdr injects ``HERDR_PLUGIN_CONFIG_DIR`` / ``HERDR_PLUGIN_STATE_DIR`` for
    plugin actions; those are plugin-scoped *roots*, so we append the session
    slug to keep concurrent herdr sessions isolated. When the herdr env is absent
    (standalone/dev runs, or the LaunchAgent which gets pinned absolute paths
    instead), fall back to the legacy ``~/Library/Application Support`` root.
    """
    root = os.environ.get(env_var)
    if root:
        return Path(root) / slug
    return home / "Library" / "Application Support" / "herdr-amphetamine" / slug


def paths(home_dir: Optional[Path] = None) -> dict:
    home = home_dir or Path.home()
    slug = session_slug()
    label_name = f"{BASE_LABEL}.{slug}"
    return {
        "label": label_name,
        "config_dir": _session_subdir("HERDR_PLUGIN_CONFIG_DIR", home, slug),
        "state_dir": _session_subdir("HERDR_PLUGIN_STATE_DIR", home, slug),
        "log_dir": home / "Library" / "Logs" / "herdr-amphetamine" / slug,
        "plist": home / "Library" / "LaunchAgents" / f"{label_name}.plist",
    }
