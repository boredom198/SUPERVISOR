"""Antigravity Stop lifecycle hook handler (Stage 3 & Stage 6 automatic enforcement)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from supervisor.models import LifecycleState
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.repair import RepairHandoff
from supervisor.state import StateManager


def handle_stop_event():
    """Reads Stop hook payload on stdin, checks state/findings, and returns JSON decision."""
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception as e:
        # If payload cannot be parsed, allow stopping gracefully
        print(json.dumps({"decision": "allow", "reason": f"Hook parse error: {str(e)}"}))
        return

    workspace_paths = payload.get("workspacePaths", [])
    workspace_root = Path(workspace_paths[0]) if workspace_paths else Path.cwd()

    termination_reason = payload.get("terminationReason", "model_stop")
    if termination_reason != "model_stop":
        # Don't block system cancellation or max_steps
        print(json.dumps({"decision": "allow", "reason": f"Terminated by {termination_reason}"}))
        return

    state_mgr = StateManager(workspace_root)
    curr_state = state_mgr.load()

    # If already passed, stopped at limit, or blocked, allow stopping
    if curr_state.state in {
        LifecycleState.PASSED,
        LifecycleState.RETRY_LIMIT_REACHED,
        LifecycleState.BLOCKED,
    }:
        print(json.dumps({"decision": "allow", "reason": f"SUPERVISOR status: {curr_state.state.value}"}))
        return

    # Check if review or verification is needed
    orchestrator = SupervisorOrchestrator(workspace_root)

    if curr_state.state == LifecycleState.REPAIRING:
        report = orchestrator.execute_verification_cycle()
    else:
        report = orchestrator.execute_review_cycle()

    # Check outcome
    if not report.has_material_issues:
        print(
            json.dumps(
                {
                    "decision": "allow",
                    "reason": f"SUPERVISOR verification PASSED at cycle {report.cycle}. All checks verified.",
                }
            )
        )
    elif curr_state.cycle >= curr_state.max_retries:
        print(
            json.dumps(
                {
                    "decision": "allow",
                    "reason": f"SUPERVISOR RETRY_LIMIT_REACHED ({curr_state.cycle}/{curr_state.max_retries}). Reporting unresolved findings.",
                }
            )
        )
    else:
        # Defect found: force continuation and hand off repair directives!
        handoff = RepairHandoff(workspace_root)
        task_prompt = curr_state.task.prompt if curr_state.task else ""
        directives = handoff.generate_repair_prompt(report, original_task=task_prompt)

        # Advance to REPAIRING state
        state_mgr.transition_to(LifecycleState.REPAIRING, "Automated handoff to coding agent.")

        print(
            json.dumps(
                {
                    "decision": "continue",
                    "reason": directives,
                }
            )
        )


if __name__ == "__main__":
    handle_stop_event()

