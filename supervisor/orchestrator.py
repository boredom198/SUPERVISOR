"""Supervisor orchestrator managing the review -> repair -> verification lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from supervisor.context import ContextCollector, WorkspaceContext
from supervisor.models import (
    FindingStatus,
    LifecycleState,
    ReviewStatus,
    Severity,
    SupervisorReport,
)
from supervisor.repair import RepairHandoff
from supervisor.reporter import ReportGenerator
from supervisor.reviewer import ReviewEngine
from supervisor.state import StateManager
from supervisor.verifier import VerificationEngine


class SupervisorOrchestrator:
    """Coordinates lifecycle stages, loop control, and completion status."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.state_mgr = StateManager(self.workspace_root)
        self.context_collector = ContextCollector(self.workspace_root)
        self.reviewer = ReviewEngine(self.workspace_root)
        self.reporter = ReportGenerator(self.workspace_root)
        self.repair_handoff = RepairHandoff(self.workspace_root)
        self.verifier = VerificationEngine(self.workspace_root)

    def has_unreviewed_changes(self, state: Optional[SupervisorState] = None) -> bool:
        """Checks if current workspace differs from latest report fingerprint or git status."""
        curr = state or self.state_mgr.load()
        fp = self.context_collector.compute_workspace_fingerprint()
        _, changed, untracked, _ = self.context_collector.inspect_git()
        return self.state_mgr.has_unreviewed_changes(
            latest_report=curr.latest_report,
            current_fingerprint=fp,
            git_changed=changed,
            git_untracked=untracked,
        )

    def execute_review_cycle(
        self,
        prompt: Optional[str] = None,
        task_id: Optional[str] = None,
        test_command: Optional[str] = None,
        report_only: bool = False,
        max_retries: Optional[int] = None,
    ) -> SupervisorReport:
        """Runs a review cycle, updates state, and generates reports."""
        state = self.state_mgr.load()

        # Determine prompt: explicit prompt or discovered from transcript matching current workspace
        discovered_prompt = prompt or self.context_collector.find_transcript_prompt()

        # A different user prompt is a new task even when the previous task is
        # still FIXES_REQUIRED/REPAIRING. Reusing that task's report here carries
        # old findings and retry counters into the new prompt, making the latest
        # report appear to belong to the previous task.
        is_terminal = state.state in {
            LifecycleState.PASSED,
            LifecycleState.RETRY_LIMIT_REACHED,
            LifecycleState.VERIFICATION_INCOMPLETE,
            LifecycleState.BLOCKED,
        }
        has_new_prompt = bool(
            discovered_prompt
            and state.task
            and discovered_prompt.strip() != state.task.prompt.strip()
        )
        has_unreviewed_changes = self.has_unreviewed_changes(state)
        should_start_new_task = has_new_prompt or (
            is_terminal and (bool(prompt) or has_unreviewed_changes)
        )
        if should_start_new_task:
            state = self.state_mgr.initialize_task(
                prompt=discovered_prompt or (prompt or ""),
                task_id=task_id,
                max_retries=max_retries if max_retries is not None else state.max_retries,
            )
        elif max_retries is not None:
            state.max_retries = max(1, int(max_retries))
            state.cycle = min(max(1, state.cycle), state.max_retries)
            self.state_mgr.save(state)

        # Guard against duplicate recursive review if already reviewing
        if state.state == LifecycleState.REVIEWING:
            pass  # proceed safely

        self.state_mgr.transition_to(LifecycleState.REVIEWING, "Starting review cycle.")
        state = self.state_mgr.load()
        state.active_pid = os.getpid()
        self.state_mgr.save(state)

        # Determine task & context
        active_prompt = discovered_prompt or (state.task.prompt if state.task else "")
        active_task_id = task_id or (state.task.task_id if state.task else "task_auto")

        context = self.context_collector.collect(
            explicit_prompt=active_prompt,
            task_id=active_task_id,
        )

        # Update task in state if missing or new prompt provided
        if not state.task or prompt or has_new_prompt or (state.task.prompt in ("", "UNAVAILABLE") and context.task.prompt not in ("", "UNAVAILABLE")):
            state.task = context.task
            self.state_mgr.save(state)

        # Run review
        report = self.reviewer.review(
            context=context,
            cycle=state.cycle,
            previous_report=state.latest_report,
            test_command=test_command,
        )
        report.workspace_fingerprint = context.workspace_fingerprint

        # Determine next state
        if not report.has_material_issues:
            if report.blocker_reason or report.overall_status == ReviewStatus.BLOCKED:
                self.state_mgr.transition_to(
                    LifecycleState.BLOCKED,
                    f"Review blocked: {report.blocker_reason or 'A validation command was blocked.'}",
                )
                report.state = LifecycleState.BLOCKED
            elif report.overall_status == ReviewStatus.VERIFICATION_INCOMPLETE:
                self.state_mgr.transition_to(
                    LifecycleState.VERIFICATION_INCOMPLETE,
                    "Review has no material static findings, but behavioral verification is incomplete.",
                )
                report.state = LifecycleState.VERIFICATION_INCOMPLETE
            else:
                self.state_mgr.transition_to(
                    LifecycleState.PASSED, "All executed checks and tests passed."
                )
                report.state = LifecycleState.PASSED
        else:
            if report_only:
                self.state_mgr.transition_to(
                    LifecycleState.FIXES_REQUIRED,
                    "Report-only mode: findings saved, repairs not initiated.",
                )
                report.state = LifecycleState.FIXES_REQUIRED
            elif state.cycle >= state.max_retries:
                self.state_mgr.transition_to(
                    LifecycleState.RETRY_LIMIT_REACHED,
                    f"Retry limit ({state.cycle}/{state.max_retries}) reached with unresolved findings.",
                )
                report.state = LifecycleState.RETRY_LIMIT_REACHED
            else:
                self.state_mgr.transition_to(
                    LifecycleState.FIXES_REQUIRED,
                    f"Material findings detected at cycle {state.cycle}. Auto-repair required.",
                )
                report.state = LifecycleState.FIXES_REQUIRED
                # Generate actionable repair directives
                self.repair_handoff.generate_repair_prompt(
                    report=report,
                    original_task=state.task.prompt if state.task else None,
                )

        # Save reports to disk after state is established
        self.reporter.save_reports(report)

        state = self.state_mgr.load()
        state.active_pid = None
        state.workspace_fingerprint = context.workspace_fingerprint
        state.latest_report = report
        state.state = report.state
        if state.cycle > state.max_retries:
            state.cycle = state.max_retries
        self.state_mgr.save(state)
        return report

    def execute_verification_cycle(
        self,
        test_command: Optional[str] = None,
        report_only: bool = False,
    ) -> SupervisorReport:
        """Verifies corrections after a repair cycle (Stage 7)."""
        state = self.state_mgr.load()
        if not state.latest_report:
            # Fall back to review
            return self.execute_review_cycle(test_command=test_command, report_only=report_only)

        # Advance cycle counter ONLY if previous report had material issues (a repair was attempted) and not report_only
        if state.latest_report.has_material_issues and not report_only:
            state.cycle = min(state.cycle + 1, state.max_retries)
        elif state.cycle > state.max_retries:
            state.cycle = state.max_retries
        self.state_mgr.save(state)

        if not report_only:
            self.state_mgr.transition_to(
                LifecycleState.VERIFYING, "Verifying repair corrections."
            )
        state = self.state_mgr.load()
        state.active_pid = os.getpid()
        self.state_mgr.save(state)

        context = self.context_collector.collect(
            explicit_prompt=state.task.prompt if state.task else "",
            task_id=state.task.task_id if state.task else "task_auto",
        )

        summary = self.verifier.verify_cycle(
            context=context,
            previous_report=state.latest_report,
            cycle=state.cycle,
            test_command=test_command,
        )

        report = summary.report or state.latest_report
        report.workspace_fingerprint = context.workspace_fingerprint
        report.cycle = state.cycle

        if summary.passed:
            if report.blocker_reason or report.overall_status == ReviewStatus.BLOCKED:
                if not report_only:
                    self.state_mgr.transition_to(
                        LifecycleState.BLOCKED,
                        f"Review blocked: {report.blocker_reason or 'Environment blocker.'}",
                    )
                report.state = LifecycleState.BLOCKED
            else:
                if not report_only:
                    self.state_mgr.transition_to(
                        LifecycleState.PASSED, "Verification passed all checks."
                    )
                report.state = LifecycleState.PASSED
        else:
            # Incomplete verification is not a pass and should not trigger an endless repair loop.
            if report.overall_status == ReviewStatus.BLOCKED and not report.has_material_issues:
                report.state = LifecycleState.BLOCKED
                if not report_only:
                    self.state_mgr.transition_to(LifecycleState.BLOCKED, report.blocker_reason or "A validation command was blocked.")
            elif report.overall_status == ReviewStatus.VERIFICATION_INCOMPLETE and not report.has_material_issues:
                report.state = LifecycleState.VERIFICATION_INCOMPLETE
                if not report_only:
                    self.state_mgr.transition_to(LifecycleState.VERIFICATION_INCOMPLETE, "Verification could not establish a reliable pass.")
            # Verification failed: check if retry limit is reached
            elif report_only:
                report.state = LifecycleState.FIXES_REQUIRED
            elif state.cycle >= state.max_retries:
                self.state_mgr.transition_to(
                    LifecycleState.RETRY_LIMIT_REACHED,
                    f"Retry limit ({state.cycle}/{state.max_retries}) reached during verification.",
                )
                report.state = LifecycleState.RETRY_LIMIT_REACHED
            else:
                self.state_mgr.transition_to(
                    LifecycleState.FIXES_REQUIRED,
                    f"Verification failed at cycle {state.cycle}. Fixes required.",
                )
                report.state = LifecycleState.FIXES_REQUIRED
                self.repair_handoff.generate_repair_prompt(
                    report=report,
                    original_task=state.task.prompt if state.task else None,
                )

        self.reporter.save_reports(report)

        state = self.state_mgr.load()
        state.active_pid = None
        state.workspace_fingerprint = context.workspace_fingerprint
        state.latest_report = report
        if not report_only:
            state.state = report.state
        if state.cycle > state.max_retries:
            state.cycle = state.max_retries
        self.state_mgr.save(state)
        return report

    def run_full_lifecycle(
        self,
        task_prompt: str,
        repair_agent_callback: Optional[Callable[[str], None]] = None,
        max_retries: int = 3,
        test_command: Optional[str] = None,
    ) -> SupervisorReport:
        """Executes full loop (Review -> Handoff -> Repair -> Verify) until PASSED or limit reached."""
        self.state_mgr.initialize_task(prompt=task_prompt, max_retries=max_retries)

        report = self.execute_review_cycle(prompt=task_prompt, test_command=test_command)

        while report.has_material_issues:
            state = self.state_mgr.load()
            if state.state in {LifecycleState.PASSED, LifecycleState.RETRY_LIMIT_REACHED, LifecycleState.BLOCKED}:
                break

            if repair_agent_callback:
                directives = self.repair_handoff.generate_repair_prompt(report, task_prompt)
                self.state_mgr.transition_to(LifecycleState.REPAIRING, "Executing agent repair callback.")
                repair_agent_callback(directives)
                report = self.execute_verification_cycle(test_command=test_command)
            else:
                # No automated callback provided (e.g. running outside automated agent loop)
                break

        return report

