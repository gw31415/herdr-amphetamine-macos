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
                paths = launchagent.paths(Path(d))
        self.assertIn("herdr-amphetamine/alpha.", str(paths["state_dir"]))
        self.assertIn("herdr-amphetamine/alpha.", str(paths["log_dir"]))
        self.assertEqual(paths["plist"].name, f"{paths['label']}.plist")

    def test_ambiguous_running_sessions_require_env(self):
        proc = mock.Mock(returncode=0, stdout='{"sessions":[{"name":"a","running":true},{"name":"b","running":true}]}')
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(launchagent.subprocess, "run", return_value=proc):
                with self.assertRaises(launchagent.AmbiguousSessionError):
                    launchagent.session_name()


if __name__ == "__main__":
    unittest.main()
