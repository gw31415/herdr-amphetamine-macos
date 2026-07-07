#!/usr/bin/env python3
"""Unit tests for config.py: load/save/validate and env-over-file precedence.

No real Amphetamine or herdr calls. HERDR_AMPHETAMINE_CONFIG_DIR is redirected to
a fresh tempdir per test so config.json is isolated."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402


class _IsolatedStateDir(unittest.TestCase):
    def setUp(self):
        self._prev_cfg = os.environ.get("HERDR_AMPHETAMINE_CONFIG_DIR")
        self._prev_state = os.environ.get("HERDR_AMPHETAMINE_STATE_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HERDR_AMPHETAMINE_CONFIG_DIR"] = self._tmp.name
        os.environ["HERDR_AMPHETAMINE_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("HERDR_AMPHETAMINE_") or k in ("AMPHETAMINE_APP_PATH", "HERDR_BIN_PATH"):
                os.environ.pop(k, None)
        for key, prev in (("HERDR_AMPHETAMINE_CONFIG_DIR", self._prev_cfg),
                          ("HERDR_AMPHETAMINE_STATE_DIR", self._prev_state)):
            if prev is not None:
                os.environ[key] = prev
        self._tmp.cleanup()


class DefaultsTests(_IsolatedStateDir):
    def test_default_config_is_independent_copy(self):
        a = config.default_config()
        a["poll_seconds"] = 999
        b = config.default_config()
        self.assertEqual(b["poll_seconds"], 5.0)

    def test_default_config_has_all_keys(self):
        for key in ("armed", "poll_seconds", "start_grace_seconds", "stop_grace_seconds",
                    "top_up_minutes", "top_up_threshold_minutes", "herdr_bin_path",
                    "amphetamine_app_path", "prevent_closed_display_sleep",
                    "display_sleep_allowed"):
            self.assertIn(key, config.default_config())
        self.assertNotIn("session_minutes", config.default_config())
        self.assertNotIn("extend_threshold_minutes", config.default_config())


class LoadTests(_IsolatedStateDir):
    def test_load_defaults_when_missing(self):
        cfg = config.load_config_file()
        self.assertTrue(cfg["armed"])
        self.assertEqual(cfg["poll_seconds"], 5.0)
        self.assertEqual(cfg["top_up_minutes"], 1.0)
        self.assertEqual(cfg["top_up_threshold_minutes"], 2.0)
        self.assertEqual(cfg["amphetamine_app_path"], "/Applications/Amphetamine.app")

    def test_load_defaults_when_corrupt(self):
        config.config_path().write_text("{not valid json")
        cfg = config.load_config_file()
        # Corrupt file falls back to defaults; armed defaults True.
        self.assertTrue(cfg["armed"])

    def test_unknown_keys_dropped(self):
        config.config_path().write_text(json.dumps({
            "armed": False, "future_key": "ignored",
        }))
        cfg = config.load_config_file()
        self.assertNotIn("future_key", cfg)
        self.assertFalse(cfg["armed"])  # known key preserved


class SaveValidateTests(_IsolatedStateDir):
    def test_save_then_load_roundtrip(self):
        config.save_config_file({"armed": False, "poll_seconds": 7, "top_up_minutes": 20})
        cfg = config.load_config_file()
        self.assertFalse(cfg["armed"])
        self.assertEqual(cfg["poll_seconds"], 7.0)
        self.assertEqual(cfg["top_up_minutes"], 20.0)
        # Untouched keys fall back to defaults.
        self.assertEqual(cfg["stop_grace_seconds"], 30.0)

    def test_save_partial_dict_fills_defaults(self):
        config.save_config_file({"armed": True})  # only one key
        cfg = config.load_config_file()
        self.assertEqual(cfg["poll_seconds"], 5.0)
        self.assertEqual(cfg["amphetamine_app_path"], "/Applications/Amphetamine.app")

    def test_validate_clamps_negatives_and_bad_types(self):
        out = config.validate({"poll_seconds": -5, "stop_grace_seconds": "oops",
                               "top_up_minutes": -1})
        self.assertGreaterEqual(out["poll_seconds"], 1.0)   # poll clamped to >=1
        self.assertEqual(out["stop_grace_seconds"], 30.0)    # bad -> default
        self.assertEqual(out["top_up_minutes"], 0.0)         # clamped to >=0

    def test_validate_bool_strings(self):
        out = config.validate({"armed": "off", "display_sleep_allowed": "yes"})
        self.assertFalse(out["armed"])
        self.assertTrue(out["display_sleep_allowed"])

    def test_validate_normalizes_paths(self):
        out = config.validate({"herdr_bin_path": "  ", "amphetamine_app_path": ""})
        self.assertIsNone(out["herdr_bin_path"])
        self.assertEqual(out["amphetamine_app_path"], "/Applications/Amphetamine.app")


class EnvOverrideTests(_IsolatedStateDir):
    def test_env_overrides_file_when_set(self):
        config.save_config_file({"poll_seconds": 5, "top_up_minutes": 10})
        os.environ["HERDR_AMPHETAMINE_POLL_SECONDS"] = "99"
        os.environ["HERDR_AMPHETAMINE_TOP_UP_MINUTES"] = "0"
        os.environ["HERDR_AMPHETAMINE_TOP_UP_THRESHOLD_MINUTES"] = "4"
        os.environ["HERDR_BIN_PATH"] = "/custom/herdr"
        os.environ["AMPHETAMINE_APP_PATH"] = "/Apps/Amphetamine.app"
        resolved = config.load_resolved()
        self.assertEqual(resolved["poll_seconds"], 99.0)
        self.assertEqual(resolved["top_up_minutes"], 0.0)
        self.assertEqual(resolved["top_up_threshold_minutes"], 4.0)
        self.assertEqual(resolved["herdr_bin_path"], "/custom/herdr")
        self.assertEqual(resolved["amphetamine_app_path"], "/Apps/Amphetamine.app")

    def test_env_does_not_override_when_unset(self):
        config.save_config_file({"poll_seconds": 7})
        # No HERDR_AMPHETAMINE_POLL_SECONDS set.
        resolved = config.load_resolved()
        self.assertEqual(resolved["poll_seconds"], 7.0)

    def test_env_empty_string_is_ignored(self):
        config.save_config_file({"poll_seconds": 7})
        os.environ["HERDR_AMPHETAMINE_POLL_SECONDS"] = ""
        resolved = config.load_resolved()
        self.assertEqual(resolved["poll_seconds"], 7.0)


if __name__ == "__main__":
    unittest.main()
