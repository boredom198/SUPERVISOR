"""OpenAI Codex CLI Stop lifecycle hook handler."""

from __future__ import annotations

import os
import sys

from supervisor.adapters.codex import CodexAdapter


def handle_codex_stop_event():
    """Reads Codex Stop hook payload on stdin, processes through CodexAdapter, and outputs decision."""
    raw_input = sys.stdin.read()
    # Check if user/env requested imperative exit code 2 mode
    use_exit_code_2 = (
        os.environ.get("SUPERVISOR_CODEX_EXIT_CODE_BLOCK", "0") == "1"
        or "--exit-code-block" in sys.argv
    )
    adapter = CodexAdapter()
    exit_code, stdout_str, stderr_str = adapter.process_raw_input(
        raw_input, exit_code_on_block=use_exit_code_2
    )

    if stdout_str:
        print(stdout_str)
    if stderr_str:
        sys.stderr.write(stderr_str + "\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    handle_codex_stop_event()

