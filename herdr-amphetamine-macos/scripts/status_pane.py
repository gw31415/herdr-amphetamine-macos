#!/usr/bin/env python3
"""Deprecated shim.

The read-only status pane has been replaced by the interactive TUI
(`scripts/tui.py`). This file is kept only so any stale references still do
something sensible; it simply launches the TUI. Safe to delete once nothing
references it (the manifest no longer does).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tui  # noqa: E402

if __name__ == "__main__":
    sys.exit(tui.main())
