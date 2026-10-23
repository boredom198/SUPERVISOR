"""Antigravity Stop lifecycle hook handler (Stage 3 & Stage 6 automatic enforcement)."""

from __future__ import annotations

import sys
from pathlib import Path

from supervisor.adapters.antigravity import AntigravityAdapter


def handle_stop_event():
    """Reads Stop hook payload on stdin, processes through AntigravityAdapter, and outputs JSON decision."""
    raw_input = sys.stdin.read()
    adapter = AntigravityAdapter()
    output_json = adapter.process_raw_input(raw_input)
    print(output_json)


if __name__ == "__main__":
    handle_stop_event()
