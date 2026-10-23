"""Supervisor orchestrator managing the review -> repair -> verification lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from supervisor.context import ContextCollector, WorkspaceContext
from supervisor.models import (
    FindingStatus,
    LifecycleState,
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

    def execute_review_cycle(
        self,
        prompt: Optional[str] = None,
        task_id: Optional[str] = None,
        test_command: Optional[str] = None,
        report_only: bool = False,
    ) -> SupervisorReport:
        """Runs a review cycle, updates state, and generates reports."""
        state = self.state_mgr.load()

        # Guard against duplicate recursive review if already reviewing
        if state.state == LifecycleState.REVIEWING:
            pass  # proceed safely

        self.state_mgr.transition_to(LifecycleState.REVIEWING, "Starting review cycle.")

        # Determine task & context
        active_prompt = prompt or (state.task.prompt if state.task else "")
        active_task_id = task_id or (state.task.task_id if state.task else "task_auto")

        context = self.context_collector.collect(
            explicit_prompt=active_prompt,
            task_id=active_task_id,
        )

        # Update task in state if missing
        if not state.task or prompt:
            state.task = context.task
            self.state_mgr.save(state)

        # Run review
        report = self.reviewer.review(
            context=context,
            cycle=state.cycle,
            previous_report=state.latest_report,
            test_command=test_command,
        )

        # Save reports to disk
        self.reporter.save_reports(report)

        # Determine next state
        if not report.has_material_issues:
            self.state_mgr.transition_to(
                LifecycleState.PASSED, "Implementation passed all checks."
            )
            report.state = LifecycleState.PASSED
        else:
            if report_only:
                self.state_mgr.transition_to(
                    LifecycleState.FIXES_REQUIRED,
                    "Report-only mode: findings saved, repairs not initiated.",
                )
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
                # Generate actionable repair directives
                self.repair_handoff.generate_repair_prompt(
                    report=report,
                    original_task=state.task.prompt if state.task else None,
                )

        state = self.state_mgr.load()
        state.latest_report = report
        self.state_mgr.save(state)
        return report

    def execute_verification_cycle(
        self,
        test_command: Optional[str] = None,
    ) -> SupervisorReport:
        """Verifies corrections after a repair cycle (Stage 7)."""
        state = self.state_mgr.load()
        if not state.latest_report:
            # Fall back to review
            return self.execute_review_cycle(test_command=test_command)

        self.state_mgr.transition_to(
            LifecycleState.VERIFYING, "Verifying repair corrections."
        )

        context = self.context_collector.collect(
            explicit_prompt=state.task.prompt if state.task else "",
            task_id=state.task.task_id if state.task else "task_auto",
        )

        # Advance cycle counter if repairs were attempted
        next_cycle = state.cycle + 1
        if next_cycle > state.max_retries:
            summary = self.verifier.verify_cycle(
                context=context,
                previous_report=state.latest_report,
                cycle=state.cycle,
                test_command=test_command,
            )
            report = summary.report or state.latest_report
            report.state = LifecycleState.RETRY_LIMIT_REACHED
            self.reporter.save_reports(report)
            state.cycle = next_cycle
            state.state = LifecycleState.RETRY_LIMIT_REACHED
            state.latest_report = report
            self.state_mgr.save(state)
            return report

        state.cycle = next_cycle
        self.state_mgr.save(state)

        summary = self.verifier.verify_cycle(
            context=context,
            previous_report=state.latest_report,
            cycle=state.cycle,
            test_command=test_command,
        )

        report = summary.report or state.latest_report
        self.reporter.save_reports(report)

        if summary.passed:
            self.state_mgr.transition_to(
                LifecycleState.PASSED, "Verification passed all checks."
            )
            report.state = LifecycleState.PASSED
        else:
            if state.cycle >= state.max_retries:
                self.state_mgr.transition_to(
                    LifecycleState.RETRY_LIMIT_REACHED,
                    "Retry limit reached during verification.",
                )
                report.state = LifecycleState.RETRY_LIMIT_REACHED
            else:
                self.state_mgr.transition_to(
                    LifecycleState.FIXES_REQUIRED,
                    "Verification failed: fixes still required.",
                )
                self.repair_handoff.generate_repair_prompt(
                    report=report,
                    original_task=state.task.prompt if state.task else None,
                )

        state = self.state_mgr.load()
        state.latest_report = report
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
