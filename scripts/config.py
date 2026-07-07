#!/usr/bin/env python3
"""Persistent, TUI-editable configuration for the Amphetamine sleep guard.

Settings live in ``config.json`` under the config directory (see
:func:`config_dir`); runtime state lives in ``state.json`` under
:func:`state_dir`. Both resolve to per-session subdirectories of the
herdr-provided plugin dirs ``HERDR_PLUGIN_CONFIG_DIR`` / ``HERDR_PLUGIN_STATE_DIR``
(the LaunchAgent pins the resolved absolute paths as ``HERDR_AMPHETAMINE_CONFIG_DIR``
/ ``HERDR_AMPHETAMINE_STATE_DIR``). The daemon re-reads ``config.json`` every poll
cycle, so changes made in the TUI take effect within one cycle **without
reinstalling the LaunchAgent**.

Load precedence when the daemon builds its runtime config is::

    environment variable (if set) > config.json > built-in default

The LaunchAgent pins ``HERDR_BIN_PATH`` and the resolved config/state dirs as
bootstrap environment; every other tunable lives here and is editable from the
TUI. This module is stdlib-only and never imports the daemon, so the TUI can use
it without pulling in ``monitor``'s subprocess/Amphetamine side effects.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "armed": True,
    "poll_seconds": 5.0,
    "start_grace_seconds": 5.0,
    "stop_grace_seconds": 30.0,
    "top_up_minutes": 1.0,              # 0 = infinite Amphetamine session
    "top_up_threshold_minutes": 2.0,    # top up when time remaining < this
    "herdr_bin_path": None,             # None -> HERDR_BIN_PATH env -> `which herdr`
    "amphetamine_app_path": "/Applications/Amphetamine.app",
    "prevent_closed_display_sleep": True,
    "display_sleep_allowed": False,
}

# Environment variables that override the file value when set and non-empty.
_ENV_FLOAT = {
    "poll_seconds": "HERDR_AMPHETAMINE_POLL_SECONDS",
    "start_grace_seconds": "HERDR_AMPHETAMINE_START_GRACE_SECONDS",
    "stop_grace_seconds": "HERDR_AMPHETAMINE_STOP_GRACE_SECONDS",
    "top_up_minutes": "HERDR_AMPHETAMINE_TOP_UP_MINUTES",
    "top_up_threshold_minutes": "HERDR_AMPHETAMINE_TOP_UP_THRESHOLD_MINUTES",
}


def _legacy_dir() -> Path:
    """Pre-herdr-env fallback for standalone (non-plugin) runs."""
    return Path.home() / "Library" / "Application Support" / "herdr-amphetamine"


def config_dir() -> Path:
    """Where ``config.json`` (user-editable settings) lives.

    ``HERDR_AMPHETAMINE_CONFIG_DIR`` wins — the LaunchAgent pins this to an
    absolute, session-scoped path under herdr's ``HERDR_PLUGIN_CONFIG_DIR``.
    Without it (standalone/dev runs outside a plugin action), fall back to the
    legacy Application Support directory.
    """
    override = os.environ.get("HERDR_AMPHETAMINE_CONFIG_DIR")
    if override:
        return Path(override)
    return _legacy_dir()


def state_dir() -> Path:
    """Where ``state.json`` (runtime monitor state) lives.

    ``HERDR_AMPHETAMINE_STATE_DIR`` wins (the LaunchAgent pins this); otherwise
    the legacy Application Support directory.
    """
    override = os.environ.get("HERDR_AMPHETAMINE_STATE_DIR")
    if override:
        return Path(override)
    return _legacy_dir()


def config_path() -> Path:
    return config_dir() / "config.json"


def default_config() -> dict:
    """Return a fresh, independent copy of the defaults."""
    return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy of plain JSON data


def load_config_file() -> dict:
    """Load ``config.json`` merged over defaults. Never raises.

    Missing file -> defaults. Corrupt/unreadable -> defaults. Unknown keys are
    dropped so the schema stays forward-compatible. This does NOT rewrite a
    missing/corrupt file; the caller (TUI/installer) may re-save explicitly.
    """
    cfg = default_config()
    path = config_path()
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                for key in DEFAULT_CONFIG:
                    if key in data:
                        cfg[key] = data[key]
    except (OSError, ValueError):
        pass
    return cfg


def save_config_file(cfg: dict) -> None:
    """Atomically write ``config.json`` (only known keys, validated first).

    Safe to call with a partial dict; missing keys fall back to defaults.
    """
    normalized = validate({k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG})
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, indent=2))
    os.replace(tmp, path)


def _to_float(val, default):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _to_bool(val, default):
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "armed"):
        return True
    if s in ("0", "false", "no", "n", "off", "disarmed"):
        return False
    return default


def validate(cfg: dict) -> dict:
    """Coerce and clamp values into a sane config. Returns a new dict.

    Numerics are clamped non-negative (poll >= 1s); booleans accept bool or the
    common true/false/on/off strings; ``herdr_bin_path`` may be ``None``.
    """
    out = default_config()
    out.update(cfg)
    out["armed"] = _to_bool(out.get("armed"), True)
    out["prevent_closed_display_sleep"] = _to_bool(out.get("prevent_closed_display_sleep"), True)
    out["display_sleep_allowed"] = _to_bool(out.get("display_sleep_allowed"), False)
    out["poll_seconds"] = max(1.0, _to_float(out.get("poll_seconds"), 5.0))
    out["start_grace_seconds"] = max(0.0, _to_float(out.get("start_grace_seconds"), 5.0))
    out["stop_grace_seconds"] = max(0.0, _to_float(out.get("stop_grace_seconds"), 30.0))
    out["top_up_minutes"] = max(0.0, _to_float(out.get("top_up_minutes"), 1.0))
    out["top_up_threshold_minutes"] = max(0.0, _to_float(out.get("top_up_threshold_minutes"), 2.0))
    bin_path = out.get("herdr_bin_path")
    if bin_path is None:
        out["herdr_bin_path"] = None
    else:
        out["herdr_bin_path"] = str(bin_path).strip() or None
    app_path = str(out.get("amphetamine_app_path") or "").strip()
    out["amphetamine_app_path"] = app_path or DEFAULT_CONFIG["amphetamine_app_path"]
    return out


def apply_env_overrides(cfg: dict) -> dict:
    """Apply ``HERDR_AMPHETAMINE_*`` / ``HERDR_BIN_PATH`` / ``AMPHETAMINE_APP_PATH``
    env overrides on top of the config dict.

    Env is applied only when the variable is set and non-empty, so an unset env
    leaves the file value intact.
    """
    out = dict(cfg)
    for key, env in _ENV_FLOAT.items():
        raw = os.environ.get(env)
        if raw is not None and raw != "":
            out[key] = _to_float(raw, out.get(key))
    herdr = os.environ.get("HERDR_BIN_PATH")
    if herdr:
        out["herdr_bin_path"] = herdr
    app = os.environ.get("AMPHETAMINE_APP_PATH")
    if app:
        out["amphetamine_app_path"] = app
    return out


def load_resolved() -> dict:
    """Convenience: file -> env overrides -> validate. The canonical read path."""
    return validate(apply_env_overrides(load_config_file()))
