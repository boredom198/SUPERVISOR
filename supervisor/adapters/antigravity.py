"""Antigravity lifecycle hook and runtime adapter for SUPERVISOR."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from supervisor.models import FindingStatus, LifecycleState, ReviewStatus, Severity, SupervisorReport
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.repair import RepairHandoff
from supervisor.security import SecurityInspector
from supervisor.state import StateManager, SupervisorState


class AntigravityAdapter:
    """Bridges Google Antigravity lifecycle events with the shared SUPERVISOR core."""

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    def parse_payload(self, raw_input: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Parses JSON input from stdin with strict error handling."""
        stripped = raw_input.strip()
        if not stripped:
            return None, "Empty hook payload on stdin"
        try:
            data = json.loads(stripped)
            if not isinstance(data, dict):
                return None, f"Hook payload must be a JSON object, got {type(data).__name__}"
            return data, None
        except json.JSONDecodeError as e:
            return None, f"Hook JSON parse error: {str(e)}"

    def handle_stop_payload(self, payload: Dict[str, Any]) -> Dict[str, str]:
        """Evaluates an Antigravity Stop event payload and returns the decision directive."""
        # 1. Resolve workspace
        workspace_paths = payload.get("workspacePaths", [])
        if workspace_paths and isinstance(workspace_paths, list):
            target_workspace = Path(workspace_paths[0]).resolve()
        else:
            target_workspace = self.workspace_root

        # 2. Check termination reason
        term_reason = payload.get("terminationReason", "model_stop")
        # Antigravity versions may report normal agent completion as NO_TOOL_CALL
        # rather than model_stop. Both must enter review; otherwise the hook
        # allows completion without forwarding findings to the agent.
        reviewable_termination_reasons = {"model_stop", "NO_TOOL_CALL"}
        if term_reason not in reviewable_termination_reasons:
            # Allow user cancellation, errors, or step limits to stop without blocking
            return {
                "decision": "allow",
                "reason": f"Execution halted by termination reason: {term_reason}",
            }

        # 3. Load state
        state_mgr = StateManager(target_workspace)
        curr_state = state_mgr.load()
        orchestrator = SupervisorOrchestrator(target_workspace)

        # Terminal state check: do not reuse stale success if new changes were made
        if curr_state.state in {
            LifecycleState.PASSED,
            LifecycleState.RETRY_LIMIT_REACHED,
            LifecycleState.VERIFICATION_INCOMPLETE,
            LifecycleState.BLOCKED,
        }:
            context = orchestrator.context_collector.collect()
            unreviewed = state_mgr.has_unreviewed_changes(
                latest_report=curr_state.latest_report,
                current_fingerprint=context.workspace_fingerprint,
                git_changed=context.changed_files,
                git_untracked=context.untracked_files,
            )
            if not unreviewed and curr_state.latest_report:
                return {
                    "decision": "allow",
                    "reason": f"SUPERVISOR session already concluded with state: {curr_state.state.value}",
                }
            # Unreviewed changes exist: reset task state to start a fresh review
            curr_state = state_mgr.initialize_task(
                prompt=context.task.prompt if context.has_explicit_prompt else "Workspace changes",
                max_retries=curr_state.max_retries,
            )

        # 4. Check for duplicate hook event or recursive review
        in_progress, is_abandoned = state_mgr.is_review_active(curr_state)
        if in_progress:
            if not is_abandoned:
                return {
                    "decision": "allow",
                    "reason": "Review already in progress; avoiding recursive execution.",
                }
            # Abandoned review (process died or timed out): recover safely
            state_mgr.transition_to(
                LifecycleState.IMPLEMENTING,
                "Recovered from interrupted or abandoned REVIEWING state.",
            )
            curr_state = state_mgr.load()

        # 5. Execute review or verification cycle
        try:
            if curr_state.state == LifecycleState.REPAIRING:
                # Progress check: verify if any changes were made since repair handoff
                report = orchestrator.execute_verification_cycle()
            else:
                report = orchestrator.execute_review_cycle()
        except Exception as e:
            # Core execution failure handling: record state as BLOCKED and fail open for the host loop
            state_mgr.transition_to(LifecycleState.BLOCKED, f"Core execution error: {str(e)}")
            return {
                "decision": "allow",
                "reason": f"SUPERVISOR execution BLOCKED: {str(e)}",
            }

        # 6. Re-load updated state after execution
        updated_state = state_mgr.load()

        # 7. Formulate output decision based on report findings and cycle limits
        if report.overall_status == ReviewStatus.BLOCKED or report.state == LifecycleState.BLOCKED or (report.blocker_reason and not report.has_material_issues):
            return {
                "decision": "allow",
                "reason": f"SUPERVISOR BLOCKED at cycle {report.cycle}: {report.blocker_reason or 'A required validation could not run.'} No pass is claimed.",
            }
        if report.overall_status == ReviewStatus.VERIFICATION_INCOMPLETE or report.state == LifecycleState.VERIFICATION_INCOMPLETE:
            return {
                "decision": "allow",
                "reason": f"SUPERVISOR VERIFICATION_INCOMPLETE at cycle {report.cycle}. No material finding was confirmed, but available checks do not establish a pass. Review limitations and add/run relevant tests.",
            }

        if not report.has_material_issues:
            return {
                "decision": "allow",
                "reason": f"SUPERVISOR verification PASSED at cycle {report.cycle}. All executed checks passed; untested behavior is not proven correct.",
            }

        if updated_state.cycle >= updated_state.max_retries or updated_state.state == LifecycleState.RETRY_LIMIT_REACHED:
            return {
                "decision": "allow",
                "reason": (
                    f"SUPERVISOR RETRY_LIMIT_REACHED ({updated_state.cycle}/{updated_state.max_retries}). "
                    f"{len(report.material_findings)} unresolved material issue(s) remaining."
                ),
            }

        if updated_state.state == LifecycleState.BLOCKED:
            return {
                "decision": "allow",
                "reason": f"SUPERVISOR verification BLOCKED: {report.blocker_reason or 'Environment blocker.'}",
            }

        # Material defects found and retries remaining: FORCE CONTINUATION!
        handoff = RepairHandoff(target_workspace)
        task_prompt = updated_state.task.prompt if updated_state.task else ""
        directives = handoff.generate_repair_prompt(report, original_task=task_prompt)

        # Ensure all secrets are redacted in directives before returning to host
        safe_directives = SecurityInspector.redact_secret(directives)

        # Transition state to REPAIRING so next Stop event runs verification
        state_mgr.transition_to(
            LifecycleState.REPAIRING,
            f"Autonomous handoff at cycle {report.cycle}: {len(report.material_findings)} defects identified.",
        )

        return {
            "decision": "continue",
            "reason": safe_directives,
        }

    def process_raw_input(self, raw_input: str) -> str:
        """Processes raw stdin string and returns formatted JSON stdout string."""
        payload, err = self.parse_payload(raw_input)
        if err or payload is None:
            # Fallback gracefully on invalid or empty JSON
            response = {
                "decision": "allow",
                "reason": f"SUPERVISOR hook skipped: {err or 'Invalid payload'}",
            }
        else:
            try:
                response = self.handle_stop_payload(payload)
            except Exception as e:
                response = {
                    "decision": "allow",
                    "reason": f"SUPERVISOR hook unexpected exception: {str(e)}",
                }

        # Final secret redaction check on the full JSON output
        serialized = json.dumps(response)
        return SecurityInspector.redact_secret(serialized)

