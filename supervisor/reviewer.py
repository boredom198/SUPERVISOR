"""Review engine comparing implementation against task requirements, running checks, and generating findings."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from supervisor.context import WorkspaceContext
from supervisor.inspector import CodeInspector
from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    LifecycleState,
    Severity,
    SupervisorReport,
    TestResult,
)


class ReviewEngine:
    """Evaluates implementation against requirements and static/dynamic checks."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.inspector = CodeInspector(self.workspace_root)

    def review(
        self,
        context: WorkspaceContext,
        cycle: int = 1,
        previous_report: Optional[SupervisorReport] = None,
        test_command: Optional[str] = None,
    ) -> SupervisorReport:
        """Executes a complete review cycle against the workspace and requirements."""
        findings: List[Finding] = []
        files_to_check = context.file_contents

        # 1. Static and AST checks on relevant files
        for rel_path, content in files_to_check.items():
            # Skip virtual environments or temporary files
            if any(part in rel_path for part in (".venv", "venv", "__pycache__")):
                continue

            # Python files
            if rel_path.endswith(".py"):
                findings.extend(self.inspector.inspect_python_file(rel_path, content))
                findings.extend(self.inspector.inspect_incomplete_implementations(rel_path, content))

            # JSON files
            elif rel_path.endswith(".json"):
                findings.extend(self.inspector.inspect_json_file(rel_path, content))

            # General secret scanning
            findings.extend(self.inspector.inspect_secrets(rel_path, content))

        # 2. Test Execution
        test_results = self.inspector.run_tests(test_command=test_command)
        for tr in test_results:
            if not tr.passed:
                err_evidence = tr.stderr.strip() or tr.stdout.strip()
                findings.append(
                    Finding(
                        finding_id=f"TEST-FAIL-{abs(hash(tr.command)) % 10000:04d}",
                        severity=Severity.HIGH,
                        category=Category.BUG,
                        file_path="tests",
                        line_number=None,
                        description=f"Automated test command `{tr.command}` failed with exit code {tr.exit_code}.",
                        evidence=err_evidence[-1500:],
                        why_it_matters="Failing test suites indicate broken logic, regressions, or unfulfilled requirements.",
                        recommended_correction="Examine test error traceback and fix the underlying implementation.",
                        verification_method=f"Execute `{tr.command}` and verify exit code 0.",
                        status=FindingStatus.CONFIRMED,
                        cycle=cycle,
                    )
                )

        # 3. Requirement Verification
        req_findings = self._check_requirements(context, cycle)
        findings.extend(req_findings)

        # 4. Inadequate / missing tests check
        test_coverage_findings = self._check_test_presence(context, cycle)
        findings.extend(test_coverage_findings)

        # 5. Reconcile with previous findings (Carry-forward or mark resolved)
        reconciled_findings = self._reconcile_with_previous(
            findings, previous_report, cycle
        )

        # 6. Determine final state for report
        has_material = any(
            f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
            and f.status in {FindingStatus.CONFIRMED, FindingStatus.UNRESOLVED}
            for f in reconciled_findings
        )
        report_state = LifecycleState.FIXES_REQUIRED if has_material else LifecycleState.PASSED

        summary_parts = []
        if not has_material:
            summary_parts.append("Implementation passed all checks. No material issues detected.")
        else:
            material_count = sum(
                1
                for f in reconciled_findings
                if f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
                and f.status in {FindingStatus.CONFIRMED, FindingStatus.UNRESOLVED}
            )
            summary_parts.append(
                f"Review identified {material_count} material issue(s) requiring repair."
            )

        report = SupervisorReport(
            report_id=f"report_cycle_{cycle}_{datetime.now(timezone.utc).strftime('%H%M%S')}",
            task_id=context.task.task_id,
            cycle=cycle,
            state=report_state,
            findings=reconciled_findings,
            tests_run=test_results,
            changed_files=context.changed_files + context.untracked_files,
            summary=" ".join(summary_parts),
        )
        return report

    def _check_requirements(self, context: WorkspaceContext, cycle: int) -> List[Finding]:
        """Checks if specific requirements in task spec are addressed."""
        findings: List[Finding] = []
        all_code = " ".join(context.file_contents.values())

        for req in context.task.requirements:
            # Look for specific target filenames or class names mentioned in quotes/backticks
            identifiers = re.findall(r"`([A-Za-z0-9_\.\-]+)`", req)
            for ident in identifiers:
                if "." in ident:  # Might be a file
                    matched_file = any(ident.lower() in p.lower() for p in context.file_contents.keys())
                    if not matched_file and not any(ident in content for content in context.file_contents.values()):
                        findings.append(
                            Finding(
                                finding_id=f"REQ-{abs(hash(req + ident)) % 10000:04d}",
                                severity=Severity.HIGH,
                                category=Category.MISSING_REQUIREMENT,
                                file_path=ident,
                                description=f"Required component or file `{ident}` was not found.",
                                evidence=f"Requirement mentions `{ident}`: '{req}'",
                                why_it_matters="Missing required artifacts directly violate task specifications.",
                                recommended_correction=f"Create and implement `{ident}` as requested.",
                                verification_method=f"Check existence and implementation of `{ident}`.",
                                status=FindingStatus.CONFIRMED,
                                cycle=cycle,
                            )
                        )
        return findings

    def _check_test_presence(self, context: WorkspaceContext, cycle: int) -> List[Finding]:
        """Checks if test files exist when non-test code was added or modified."""
        findings: List[Finding] = []
        has_code_changes = any(
            f.endswith((".py", ".js", ".ts", ".go", ".rs"))
            and not ("test" in f.lower() or "spec" in f.lower())
            for f in context.file_contents.keys()
        )
        has_tests = any(
            "test" in f.lower() or "spec" in f.lower()
            for f in context.file_contents.keys()
        )
        if has_code_changes and not has_tests:
            findings.append(
                Finding(
                    finding_id="TEST-MISSING-0001",
                    severity=Severity.MEDIUM,
                    category=Category.TESTING,
                    file_path="tests/",
                    description="No automated tests found for implemented code changes.",
                    evidence="Workspace contains implementation files but no test suites or test files.",
                    why_it_matters="Untested code is vulnerable to undetected bugs and future regressions.",
                    recommended_correction="Add automated unit or integration tests verifying the new functionality.",
                    verification_method="Run test runner and verify passing test cases.",
                    status=FindingStatus.RECOMMENDED,
                    cycle=cycle,
                )
            )
        return findings

    def _reconcile_with_previous(
        self,
        current_findings: List[Finding],
        previous_report: Optional[SupervisorReport],
        cycle: int,
    ) -> List[Finding]:
        """Reconciles current findings against the previous cycle."""
        if not previous_report:
            return current_findings

        current_finding_ids = {f.finding_id: f for f in current_findings}
        reconciled: List[Finding] = []

        # Check previous findings
        for prev in previous_report.findings:
            if prev.finding_id in current_finding_ids:
                # Still present!
                curr = current_finding_ids[prev.finding_id]
                curr.status = FindingStatus.UNRESOLVED
                curr.cycle = cycle
                reconciled.append(curr)
                del current_finding_ids[prev.finding_id]
            else:
                # Was resolved!
                prev_copy = Finding.from_dict(prev.to_dict())
                prev_copy.status = FindingStatus.RESOLVED
                prev_copy.cycle = cycle
                reconciled.append(prev_copy)

        # Add remaining brand new findings
        for new_f in current_finding_ids.values():
            new_f.cycle = cycle
            reconciled.append(new_f)

        return reconciled
