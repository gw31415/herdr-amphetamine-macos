#!/usr/bin/env python3
"""Unit tests for per-session LaunchAgent naming."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import launchagent  # noqa: E402


class LaunchAgentTests(unittest.TestCase):
    def test_session_env_makes_distinct_safe_label(self):
        with mock.patch.dict(os.environ, {"HERDR_SESSION_NAME": "work/a"}, clear=False):
            self.assertTrue(launchagent.label().startswith("com.herdr.amphetamine.monitor.work-a."))

    def test_paths_are_session_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"HERDR_SESSION_NAME": "alpha"}, clear=False):
                with mock.patch.object(launchagent, "_discover_plugin_config_root", return_value=None):
                    paths = launchagent.paths(Path(d))
        self.assertIn("herdr-amphetamine/alpha.", str(paths["config_dir"]))
        self.assertIn("herdr-amphetamine/alpha.", str(paths["state_dir"]))
        self.assertIn("herdr-amphetamine/alpha.", str(paths["log_dir"]))
        self.assertEqual(paths["plist"].name, f"{paths['label']}.plist")

    def test_paths_use_herdr_plugin_dirs_when_set(self):
        """When herdr injects its plugin dirs, config/state root under them."""
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as st:
            env = {"HERDR_SESSION_NAME": "alpha",
                   "HERDR_PLUGIN_CONFIG_DIR": cfg,
                   "HERDR_PLUGIN_STATE_DIR": st}
            with mock.patch.dict(os.environ, env, clear=False):
                paths = launchagent.paths()
                slug = launchagent.session_slug()
        self.assertEqual(paths["config_dir"], Path(cfg) / slug)
        self.assertEqual(paths["state_dir"], Path(st) / slug)
        # Distinct roots keep config and state isolated.
        self.assertNotEqual(paths["config_dir"].parent, paths["state_dir"].parent)

    def test_paths_discover_herdr_plugin_dirs_for_standalone_runs(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            with mock.patch.dict(os.environ, {"HERDR_SESSION_NAME": "alpha"}, clear=True):
                with mock.patch.object(
                    launchagent,
                    "_discover_plugin_config_root",
                    return_value=home / ".config/herdr/plugins/config/amphetamine-macos",
                ):
                    paths = launchagent.paths(home)
                    slug = launchagent.session_slug()

        self.assertEqual(
            paths["config_dir"],
            home / ".config/herdr/plugins/config/amphetamine-macos" / slug,
        )
        self.assertEqual(
            paths["state_dir"],
            home / ".local/state/herdr/plugins/amphetamine-macos" / slug,
        )

    def test_ambiguous_running_sessions_require_env(self):
        proc = mock.Mock(returncode=0, stdout='{"sessions":[{"name":"a","running":true},{"name":"b","running":true}]}')
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(launchagent.subprocess, "run", return_value=proc):
                with self.assertRaises(launchagent.AmbiguousSessionError):
                    launchagent.session_name()


if __name__ == "__main__":
    unittest.main()
