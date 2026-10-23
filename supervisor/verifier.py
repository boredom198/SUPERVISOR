"""Verification engine evaluating post-repair corrections, regression detection, and re-running checks (Stage 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from supervisor.context import WorkspaceContext
from supervisor.models import (
    Finding,
    FindingStatus,
    LifecycleState,
    Severity,
    SupervisorReport,
)
from supervisor.reviewer import ReviewEngine


@dataclass
class VerificationSummary:
    """Outcome of a verification run."""

    passed: bool
    resolved_findings: List[Finding] = field(default_factory=list)
    unresolved_findings: List[Finding] = field(default_factory=list)
    new_regressions: List[Finding] = field(default_factory=list)
    report: Optional[SupervisorReport] = None


class VerificationEngine:
    """Verifies that repairs legitimately resolve issues without regressions."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.review_engine = ReviewEngine(self.workspace_root)

    def verify_cycle(
        self,
        context: WorkspaceContext,
        previous_report: SupervisorReport,
        cycle: int,
        test_command: Optional[str] = None,
    ) -> VerificationSummary:
        """Executes verification of corrections against the previous report."""
        # Run a full review for the new cycle
        new_report = self.review_engine.review(
            context=context,
            cycle=cycle,
            previous_report=previous_report,
            test_command=test_command,
        )

        resolved: List[Finding] = []
        unresolved: List[Finding] = []
        new_regressions: List[Finding] = []

        prev_ids = {f.finding_id for f in previous_report.findings}

        for f in new_report.findings:
            if f.status == FindingStatus.RESOLVED:
                resolved.append(f)
            elif f.finding_id in prev_ids:
                unresolved.append(f)
            else:
                # Brand new finding introduced during repair cycle = regression!
                if f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}:
                    new_regressions.append(f)

        passed = len(unresolved) == 0 and len(new_regressions) == 0
        new_report.state = LifecycleState.PASSED if passed else LifecycleState.FIXES_REQUIRED

        summary_parts = []
        if passed:
            summary_parts.append(
                f"Verification PASSED at cycle {cycle}. All {len(resolved)} previous issues resolved. No regressions detected."
            )
        else:
            summary_parts.append(
                f"Verification FAILED at cycle {cycle}. {len(unresolved)} unresolved issues, {len(new_regressions)} regressions."
            )
        new_report.summary = " ".join(summary_parts)

        return VerificationSummary(
            passed=passed,
            resolved_findings=resolved,
            unresolved_findings=unresolved,
            new_regressions=new_regressions,
            report=new_report,
        )
