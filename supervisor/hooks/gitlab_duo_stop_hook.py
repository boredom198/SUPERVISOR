"""GitLab Duo CLI Stop lifecycle hook handler."""

from __future__ import annotations

import os
import sys

from supervisor.adapters.gitlab_duo import GitLabDuoAdapter


def handle_gitlab_duo_stop_event():
    """Reads GitLab Duo Stop hook payload on stdin, processes through GitLabDuoAdapter, and outputs decision."""
    raw_input = sys.stdin.read()
    # Default to exit code 2 on block for GitLab Duo CLI unless structured JSON stdout is explicitly requested
    use_exit_code_2 = (
        "--json-block" not in sys.argv
        and os.environ.get("SUPERVISOR_GITLAB_JSON_BLOCK", "0") != "1"
    )
    is_report_only = "--report-only" in sys.argv

    adapter = GitLabDuoAdapter()
    exit_code, stdout_str, stderr_str = adapter.process_raw_input(
        raw_input,
        exit_code_on_block=use_exit_code_2,
        report_only=is_report_only,
    )

    if stdout_str:
        print(stdout_str)
    if stderr_str:
        sys.stderr.write(stderr_str + "\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    handle_gitlab_duo_stop_event()
