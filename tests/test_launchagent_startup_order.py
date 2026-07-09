#!/usr/bin/env python3
"""Regression tests for LaunchAgent startup after persisted disable overrides."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import install_launchagent  # noqa: E402
import sync_launchagent  # noqa: E402


class LaunchAgentStartupOrderTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_sync_enables_before_bootstrap_after_install_stops_service(self):
        paths = {
            "label": "com.herdr.amphetamine.monitor.default.7505d64a",
            "plist": Path("/tmp/com.herdr.amphetamine.monitor.default.7505d64a.plist"),
            "config_dir": Path("/tmp/config"),
            "state_dir": Path("/tmp/state"),
            "log_dir": Path("/tmp/log"),
        }
        cfg = SimpleNamespace(herdr_bin="/tmp/herdr")
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return 0, ""

        with mock.patch.object(sync_launchagent.launchagent, "paths", return_value=paths), \
             mock.patch.object(sync_launchagent.monitor, "load_config", return_value=cfg), \
             mock.patch.object(sync_launchagent.monitor, "get_agent_statuses", return_value=["working"]), \
             mock.patch.object(sync_launchagent.install_launchagent, "main", return_value=0), \
             mock.patch.object(sync_launchagent.launchagent, "run", side_effect=fake_run), \
             mock.patch.object(sync_launchagent.launchagent, "start") as start:
            self.assertEqual(sync_launchagent.main(), 0)

        enable_call = ["launchctl", "enable", "gui/%s/%s" % (os.getuid(), paths["label"])]
        bootstrap_call = ["launchctl", "bootstrap", "gui/%s" % os.getuid(), str(paths["plist"])]
        self.assertIn(enable_call, calls)
        self.assertIn(bootstrap_call, calls)
        self.assertLess(calls.index(enable_call), calls.index(bootstrap_call))
        start.assert_called_once_with(paths["label"])

    def test_install_enables_before_bootstrap_then_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            calls = []

            def fake_run(cmd):
                calls.append(cmd)
                return 0, ""

            env = {
                "HERDR_SESSION_NAME": "default",
                "HERDR_BIN_PATH": "/tmp/herdr",
                "HERDR_SOCKET_PATH": "/tmp/herdr.sock",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(install_launchagent.Path, "home", return_value=home), \
                 mock.patch.object(install_launchagent.launchagent, "_discover_plugin_config_root", return_value=None), \
                 mock.patch.object(install_launchagent.launchagent, "run", side_effect=fake_run):
                self.assertEqual(install_launchagent.main(), 0)

            label = "com.herdr.amphetamine.monitor.default.7505d64a"
            plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
            enable_call = ["launchctl", "enable", "gui/%s/%s" % (os.getuid(), label)]
            bootstrap_call = ["launchctl", "bootstrap", "gui/%s" % os.getuid(), str(plist)]
            disable_call = ["launchctl", "disable", "gui/%s/%s" % (os.getuid(), label)]
            self.assertIn(enable_call, calls)
            self.assertIn(bootstrap_call, calls)
            self.assertIn(disable_call, calls)
            self.assertLess(calls.index(enable_call), calls.index(bootstrap_call))
            self.assertLess(calls.index(bootstrap_call), calls.index(disable_call))


if __name__ == "__main__":
    unittest.main()
