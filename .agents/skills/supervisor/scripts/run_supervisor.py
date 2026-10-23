#!/usr/bin/env python3
"""Launcher script for SUPERVISOR skill execution."""

import sys
from pathlib import Path

# Ensure workspace root is in sys.path
workspace_root = Path(__file__).resolve().parents[3]
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from supervisor.cli import main

if __name__ == "__main__":
    sys.exit(main())

