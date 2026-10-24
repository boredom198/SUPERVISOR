"""Claude Code lifecycle hook and runtime adapter for SUPERVISOR."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from supervisor.models import FindingStatus, LifecycleState, Severity, SupervisorReport
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.repair import RepairHandoff
from supervisor.security import SecurityInspector
from supervisor.state import StateManager, SupervisorState


class ClaudeCodeAdapter:
    """Bridges Claude Code CLI and command workflows with the shared SUPERVISOR core."""

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

    def handle_stop_payload(
        self,
        payload: Dict[str, Any],
        exit_code_on_block: bool = False,
        report_only: bool = False,
    ) -> Tuple[int, Dict[str, Any], str]:
        """Evaluates a Claude Code Stop event payload and returns (exit_code, stdout_dict, stderr_msg).
        
        Supports both Claude Code Stop contract modalities:
        1. Structured stdout JSON: {"decision": "block" | "allow", "reason": "..."} with exit code 0.
        2. Imperative stderr block: exit code 2 with directives on stderr.
        """
        # 1. Resolve workspace
        cwd_val = payload.get("cwd")
        if cwd_val and isinstance(cwd_val, str):
            target_workspace = Path(cwd_val).resolve()
        else:
            target_workspace = self.workspace_root

        # 2. Check hook event name
        event_name = payload.get("hook_event_name", "Stop")
        if event_name != "Stop":
            return 0, {"decision": "allow", "reason": f"Claude Code event '{event_name}' skipped"}, ""

        # 3. Load state and orchestrator
        state_mgr = StateManager(target_workspace)
        curr_state = state_mgr.load()
        orchestrator = SupervisorOrchestrator(target_workspace)

        # 4. Terminal state check: do not reuse stale success if new changes were made
        if curr_state.state in {
            LifecycleState.PASSED,
            LifecycleState.RETRY_LIMIT_REACHED,
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
                return (
                    0,
                    {
                        "decision": "allow",
                        "reason": f"SUPERVISOR session already concluded with state: {curr_state.state.value}",
                    },
                    "",
                )
            # Unreviewed changes exist: reset task state to start a fresh review
            task_prompt = payload.get("last_assistant_message") or (
                context.task.prompt if context.has_explicit_prompt else "Workspace changes"
            )
            curr_state = state_mgr.initialize_task(
                prompt=task_prompt,
                max_retries=curr_state.max_retries,
            )

        # 5. Check for duplicate hook event or recursive review
        in_progress, is_abandoned = state_mgr.is_review_active(curr_state)
        if in_progress:
            if not is_abandoned:
                return (
                    0,
                    {
                        "decision": "allow",
                        "reason": "Review already in progress; avoiding recursive execution.",
                    },
                    "",
                )
            # Abandoned review (process died or timed out): recover safely
            state_mgr.transition_to(
                LifecycleState.IMPLEMENTING,
                "Recovered from interrupted or timed-out REVIEWING state in Claude Code.",
            )
            curr_state = state_mgr.load()

        # 6. Execute review or verification cycle
        try:
            if report_only:
                prompt_hint = payload.get("last_assistant_message")
                report = orchestrator.execute_review_cycle(
                    prompt=prompt_hint if prompt_hint else None,
                    report_only=True,
                )
                return (
                    0,
                    {
                        "decision": "allow",
                        "reason": f"SUPERVISOR report-only review completed at cycle {report.cycle}. Findings recorded.",
                    },
                    "",
                )

            if curr_state.state == LifecycleState.REPAIRING:
                report = orchestrator.execute_verification_cycle()
            else:
                prompt_hint = payload.get("last_assistant_message")
                report = orchestrator.execute_review_cycle(prompt=prompt_hint if prompt_hint else None)
        except Exception as e:
            state_mgr.transition_to(LifecycleState.BLOCKED, f"Core execution error: {str(e)}")
            return (
                0,
                {
                    "decision": "allow",
                    "reason": f"SUPERVISOR execution BLOCKED: {str(e)}",
                },
                "",
            )

        # 7. Re-load updated state after execution
        updated_state = state_mgr.load()

        # 8. Formulate output decision based on report findings and cycle limits
        if not report.has_material_issues:
            return (
                0,
                {
                    "decision": "allow",
                    "reason": f"SUPERVISOR verification PASSED at cycle {report.cycle}. All checks verified.",
                },
                "",
            )

        if updated_state.cycle >= updated_state.max_retries or updated_state.state == LifecycleState.RETRY_LIMIT_REACHED:
            return (
                0,
                {
                    "decision": "allow",
                    "reason": (
                        f"SUPERVISOR RETRY_LIMIT_REACHED ({updated_state.cycle}/{updated_state.max_retries}). "
                        f"{len(report.material_findings)} unresolved material issue(s) remaining."
                    ),
                },
                "",
            )

        if updated_state.state == LifecycleState.BLOCKED:
            return (
                0,
                {
                    "decision": "allow",
                    "reason": f"SUPERVISOR verification BLOCKED: {report.blocker_reason or 'Environment blocker.'}",
                },
                "",
            )

        # 9. Material defects found and retries remaining: BLOCK STOP AND INJECT DIRECTIVES!
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

        if exit_code_on_block:
            # Claude Code exit code 2 imperative block: directives delivered via stderr
            return 2, {}, safe_directives
        else:
            # Claude Code JSON decision block: {"decision": "block", "reason": safe_directives}
            return (
                0,
                {
                    "decision": "block",
                    "reason": safe_directives,
                },
                "",
            )

    def process_raw_input(
        self,
        raw_input: str,
        exit_code_on_block: bool = False,
        report_only: bool = False,
    ) -> Tuple[int, str, str]:
        """Processes raw stdin string and returns (exit_code, stdout_str, stderr_str)."""
        payload, err = self.parse_payload(raw_input)
        if err or payload is None:
            # Fallback gracefully on invalid or empty JSON
            response = {
                "decision": "allow",
                "reason": f"SUPERVISOR hook skipped: {err or 'Invalid payload'}",
            }
            return 0, json.dumps(response), ""

        try:
            exit_code, stdout_dict, stderr_msg = self.handle_stop_payload(
                payload,
                exit_code_on_block=exit_code_on_block,
                report_only=report_only,
            )
        except Exception as e:
            response = {
                "decision": "allow",
                "reason": f"SUPERVISOR hook unexpected exception: {str(e)}",
            }
            return 0, json.dumps(response), ""

        stdout_serialized = json.dumps(stdout_dict) if stdout_dict else "{}"
        safe_stdout = SecurityInspector.redact_secret(stdout_serialized)
        safe_stderr = SecurityInspector.redact_secret(stderr_msg) if stderr_msg else ""
        return exit_code, safe_stdout, safe_stderr

    def evaluate_turn(
        self,
        agent_message: str,
        original_prompt: str = "",
        workspace_path: Optional[Path | str] = None,
        report_only: bool = False,
    ) -> Dict[str, Any]:
        """Programmatic interface for Claude Code custom commands or extension hooks."""
        target_ws = Path(workspace_path).resolve() if workspace_path else self.workspace_root
        state_mgr = StateManager(target_ws)
        curr_state = state_mgr.load()
        orchestrator = SupervisorOrchestrator(target_ws)

        if curr_state.task is None:
            curr_state = state_mgr.initialize_task(prompt=original_prompt or agent_message, max_retries=3)

        if report_only:
            report = orchestrator.execute_review_cycle(prompt=original_prompt or agent_message, report_only=True)
            return {
                "should_continue": False,
                "directives": None,
                "state": curr_state.state.value,
                "cycle": curr_state.cycle,
                "material_findings": [f.to_dict() for f in report.material_findings],
                "report_only": True,
            }

        if curr_state.state == LifecycleState.REPAIRING:
            report = orchestrator.execute_verification_cycle()
        else:
            report = orchestrator.execute_review_cycle(prompt=original_prompt or agent_message)

        updated_state = state_mgr.load()

        if not report.has_material_issues:
            return {
                "should_continue": False,
                "directives": None,
                "state": updated_state.state.value,
                "cycle": updated_state.cycle,
                "material_findings": [],
                "report_only": False,
            }

        if updated_state.cycle >= updated_state.max_retries or updated_state.state in {
            LifecycleState.RETRY_LIMIT_REACHED,
            LifecycleState.BLOCKED,
        }:
            return {
                "should_continue": False,
                "directives": None,
                "state": updated_state.state.value,
                "cycle": updated_state.cycle,
                "material_findings": [f.to_dict() for f in report.material_findings],
                "report_only": False,
            }

        handoff = RepairHandoff(target_ws)
        task_prompt = updated_state.task.prompt if updated_state.task else original_prompt
        directives = handoff.generate_repair_prompt(report, original_task=task_prompt)
        safe_directives = SecurityInspector.redact_secret(directives)

        state_mgr.transition_to(
            LifecycleState.REPAIRING,
            f"Claude Code repair handoff at cycle {report.cycle}.",
        )

        return {
            "should_continue": True,
            "directives": safe_directives,
            "state": LifecycleState.REPAIRING.value,
            "cycle": updated_state.cycle,
            "material_findings": [f.to_dict() for f in report.material_findings],
            "report_only": False,
        }

