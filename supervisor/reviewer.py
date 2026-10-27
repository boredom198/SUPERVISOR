"""Review engine comparing implementation against task requirements, running checks, and generating findings."""

from __future__ import annotations

from supervisor.identifiers import stable_suffix

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from supervisor.context import WorkspaceContext
from supervisor.inspector import CodeInspector
from supervisor.language_inspector import LanguageInspector, SOURCE_EXTENSIONS

UNSUPPORTED_SOURCE_EXTENSIONS = {".go", ".rs", ".c", ".cpp", ".h", ".java", ".kt", ".php", ".rb", ".swift", ".dart", ".scala", ".ex", ".exs", ".vue", ".svelte"}
from supervisor.security import SecurityInspector
from supervisor.models import (
    Category,
    CheckRecord,
    Confidence,
    Finding,
    FindingStatus,
    LifecycleState,
    ReviewStatus,
    Severity,
    SupervisorReport,
    TestResult,
    TestStatus,
)


class ReviewEngine:
    """Evaluates implementation against requirements and static/dynamic checks."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.inspector = CodeInspector(self.workspace_root)
        self.language_inspector = LanguageInspector(self.workspace_root)

    def review(
        self,
        context: WorkspaceContext,
        cycle: int = 1,
        previous_report: Optional[SupervisorReport] = None,
        test_command: Optional[str] = None,
    ) -> SupervisorReport:
        """Executes a complete review cycle against the workspace and requirements."""
        findings: List[Finding] = []
        checks_executed: List[CheckRecord] = []
        checks_skipped: List[CheckRecord] = []
        files_to_check = context.file_contents

        # 1. Static and AST checks on relevant files
        for rel_path, content in files_to_check.items():
            # Skip virtual environments or temporary files
            if any(part in rel_path for part in (".venv", "venv", "__pycache__")):
                checks_skipped.append(
                    CheckRecord(
                        check_name="environment_filter",
                        target=rel_path,
                        status="SKIPPED",
                        reason="Virtual environment or cache directory",
                    )
                )
                continue

            suffix = Path(rel_path).suffix.lower()
            # Python files
            if suffix == ".py":
                checks_executed.append(
                    CheckRecord(
                        check_name="python_syntax_and_ast",
                        target=rel_path,
                        status="EXECUTED",
                    )
                )
                findings.extend(self.inspector.inspect_python_file(rel_path, content))
                findings.extend(self.inspector.inspect_incomplete_implementations(rel_path, content))

            # JSON files
            elif suffix == ".json":
                checks_executed.append(
                    CheckRecord(
                        check_name="json_syntax_parser",
                        target=rel_path,
                        status="EXECUTED",
                    )
                )
                findings.extend(self.inspector.inspect_json_file(rel_path, content))

            # TOML files
            elif suffix == ".toml":
                checks_executed.append(
                    CheckRecord(
                        check_name="toml_syntax_parser",
                        target=rel_path,
                        status="EXECUTED",
                    )
                )
                findings.extend(self.inspector.inspect_toml_file(rel_path, content))

            elif suffix in SOURCE_EXTENSIONS:
                checks_executed.append(CheckRecord(
                    check_name="source_language_inspection", target=rel_path, status="EXECUTED",
                ))
                findings.extend(self.language_inspector.inspect(rel_path, content))
            elif suffix in UNSUPPORTED_SOURCE_EXTENSIONS:
                checks_skipped.append(CheckRecord(
                    check_name="language_specific_analysis", target=rel_path, status="SKIPPED",
                    reason=f"No language-specific static analyzer is implemented for {suffix}; results require relevant project tests or external tooling.",
                ))

            # General secret scanning
            checks_executed.append(
                CheckRecord(
                    check_name="secret_scanner",
                    target=rel_path,
                    status="EXECUTED",
                )
            )
            findings.extend(self.inspector.inspect_secrets(rel_path, content))

        # 2. Test Execution
        test_results = self.inspector.run_tests(test_command=test_command)
        checks_executed.append(
            CheckRecord(
                check_name="automated_test_runner",
                target=test_command or "discovery",
                status="EXECUTED",
            )
        )
        for tr in test_results:
            if not tr.passed:
                if tr.status in {TestStatus.NOT_AVAILABLE, TestStatus.BLOCKED, TestStatus.NOT_RUN, TestStatus.PARTIAL}:
                    continue
                err_evidence = tr.stderr.strip() or tr.stdout.strip()
                findings.append(
                    Finding(
                        finding_id=f"TEST-FAIL-{stable_suffix(tr.command)}",
                        severity=Severity.HIGH,
                        category=Category.BUG,
                        file_path="tests",
                        line_number=None,
                        description=f"Automated test command `{tr.command}` failed with exit code {tr.exit_code}.",
                        evidence=err_evidence[-1500:],
                        why_it_matters="Failing test suites indicate broken logic, regressions, or unfulfilled requirements.",
                        recommended_correction="Examine test error traceback and fix the underlying implementation.",
                        verification_method=f"Execute `{tr.command}` and verify exit code 0.",
                        confidence=Confidence.CONFIRMED,
                        status=FindingStatus.CONFIRMED,
                        source_check="automated_test_runner",
                        cycle=cycle,
                    )
                )

        # 3. Requirement Verification
        req_findings = self._check_requirements(context, cycle)
        findings.extend(req_findings)
        checks_executed.append(
            CheckRecord(
                check_name="requirement_verifier",
                target=context.task.task_id,
                status="EXECUTED",
            )
        )

        # 4. Inadequate / missing tests check
        test_coverage_findings = self._check_test_presence(context, cycle)
        findings.extend(test_coverage_findings)

        # Redact credentials from every finding channel, not just the dedicated secret finding.
        for finding in findings:
            finding.evidence = SecurityInspector.redact_secret(finding.evidence or "")
            finding.description = SecurityInspector.redact_secret(finding.description or "")
            finding.recommended_correction = SecurityInspector.redact_secret(finding.recommended_correction or "")

        # 5. Reconcile with previous findings (Carry-forward or mark resolved)
        reconciled_findings = self._reconcile_with_previous(
            findings, previous_report, cycle, inspected_paths=set(files_to_check.keys())
        )
        for finding in reconciled_findings:
            finding.evidence = SecurityInspector.redact_secret(finding.evidence or "")
            finding.description = SecurityInspector.redact_secret(finding.description or "")
            finding.recommended_correction = SecurityInspector.redact_secret(finding.recommended_correction or "")

        # 6. Determine final state and limitations for report
        has_material = any(
            f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
            and f.status in {FindingStatus.CONFIRMED, FindingStatus.UNRESOLVED}
            for f in reconciled_findings
        )
        report_state = LifecycleState.FIXES_REQUIRED if has_material else LifecycleState.PASSED

        limitations: List[str] = list(context.limitations)
        if not context.has_explicit_prompt:
            limitations.append("Task prompt is UNAVAILABLE; analysis restricted to code structure, security, and tests.")
        tests_not_available = any(tr.status == TestStatus.NOT_AVAILABLE for tr in test_results)
        tests_unavailable = tests_not_available or any(tr.status in {TestStatus.NOT_RUN, TestStatus.PARTIAL} for tr in test_results)
        tests_blocked = any(tr.status == TestStatus.BLOCKED for tr in test_results)

        # Passing tests in one language must not certify unrelated source in another.
        js_source_files = [f for f in files_to_check if Path(f).suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} and not self._is_test_path(f)]
        js_test_files = [f for f in files_to_check if Path(f).suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} and self._is_test_path(f)]
        js_test_command_passed = any(t.command == "npm test" and t.status == TestStatus.PASSED for t in test_results)
        js_verification_incomplete = bool(js_source_files and (not js_test_files or not js_test_command_passed))
        unsupported_source_files = [f for f in files_to_check if Path(f).suffix.lower() in UNSUPPORTED_SOURCE_EXTENSIONS and not self._is_test_path(f)]
        successful_commands = {t.command for t in test_results if t.status == TestStatus.PASSED}
        language_test_gaps = []
        for source_file in unsupported_source_files:
            suffix = Path(source_file).suffix.lower()
            if suffix == ".go":
                covered = "go test ./..." in successful_commands
            elif suffix == ".rs":
                covered = "cargo test" in successful_commands
            elif suffix in {".java", ".kt"}:
                covered = "mvn test" in successful_commands or "gradle test" in successful_commands
            elif suffix in {".vue", ".svelte"}:
                covered = bool(js_test_files and js_test_command_passed)
            else:
                covered = False
            if not covered:
                language_test_gaps.append(source_file)
        if tests_not_available:
            limitations.append("No automated test suite discovered in workspace.")
        if tests_unavailable and not tests_not_available:
            limitations.append("Automated behavioral verification is incomplete because at least one check did not run to completion.")
        if js_verification_incomplete:
            limitations.append("Language-specific validation is incomplete: JavaScript/TypeScript source was inspected, but no successful JavaScript test command with source-language test files was verified.")
        if language_test_gaps:
            limitations.append("Language-specific validation is incomplete for: " + ", ".join(sorted(language_test_gaps)) + ". SUPERVISOR has no dedicated static analyzer or successful language-native test command for these file types; confirm with language-native tooling.")
        elif unsupported_source_files:
            limitations.append("Language-specific static analysis is limited for: " + ", ".join(sorted(unsupported_source_files)) + "; a configured language-native test command did pass.")
        tests_unavailable = tests_unavailable or js_verification_incomplete or bool(language_test_gaps)
        if tests_blocked:
            limitations.append("At least one validation command was blocked or could not complete; its result is not a pass.")

        if has_material:
            overall_status = ReviewStatus.FIXES_REQUIRED
        elif tests_blocked:
            overall_status = ReviewStatus.BLOCKED
        elif tests_unavailable:
            overall_status = ReviewStatus.VERIFICATION_INCOMPLETE
        else:
            overall_status = ReviewStatus.PASSED

        blocker_reason = None
        if tests_blocked:
            blocker_reason = "; ".join(t.reason or f"Validation command could not run: {t.command}" for t in test_results if t.status == TestStatus.BLOCKED)
        if not has_material and overall_status == ReviewStatus.VERIFICATION_INCOMPLETE:
            report_state = LifecycleState.VERIFICATION_INCOMPLETE
        elif not has_material and overall_status == ReviewStatus.BLOCKED:
            report_state = LifecycleState.BLOCKED

        summary_parts = []
        if not has_material:
            if overall_status == ReviewStatus.VERIFICATION_INCOMPLETE:
                summary_parts.append("Implementation passed static checks; automated test suite not available.")
            elif overall_status == ReviewStatus.BLOCKED:
                summary_parts.append("No material static findings were detected, but at least one validation check was blocked. Verification is not complete.")
            else:
                summary_parts.append("All executed checks and tests passed. No material issues were detected; this does not prove absence of untested logical defects.")
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
            report_id=f"report_cycle_{cycle}_{datetime.now(timezone.utc).strftime('%H%M%S%f')}",
            task_id=context.task.task_id,
            cycle=cycle,
            state=report_state,
            findings=reconciled_findings,
            tests_run=test_results,
            changed_files=context.changed_files + context.untracked_files,
            summary=" ".join(summary_parts),
            blocker_reason=blocker_reason,
            checks_executed=checks_executed,
            checks_skipped=checks_skipped,
            limitations=limitations,
            overall_status=overall_status,
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
                                finding_id=f"REQ-{stable_suffix(req, ident)}",
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

    @staticmethod
    def _is_test_path(path: str) -> bool:
        p = Path(path)
        name = p.name.lower()
        parts = {part.lower() for part in p.parts}
        return (
            any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts)
            or name.startswith("test_")
            or name.endswith("_test.py")
            or ".test." in name
            or ".spec." in name
        )

    def _check_test_presence(self, context: WorkspaceContext, cycle: int) -> List[Finding]:
        """Checks if test files exist when non-test code was added or modified."""
        findings: List[Finding] = []
        has_code_changes = any(
            Path(f).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".c", ".cpp"}
            and not self._is_test_path(f)
            for f in context.file_contents.keys()
        )
        has_tests = any(self._is_test_path(f) for f in context.file_contents.keys())
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
        inspected_paths: Optional[Set[str]] = None,
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
                # Only call a finding resolved when its target was actually inspected.
                # Git-diff mode often reviews a subset of the workspace; absence from
                # that subset is not evidence that an old defect disappeared.
                target_was_inspected = (
                    prev.file_path in (inspected_paths or set())
                    or prev.file_path in {"tests", "tests/"}
                    or prev.file_path == ""
                )
                prev_copy = Finding.from_dict(prev.to_dict())
                prev_copy.status = FindingStatus.RESOLVED if target_was_inspected else FindingStatus.UNRESOLVED
                prev_copy.cycle = cycle
                reconciled.append(prev_copy)

        # Add remaining brand new findings
        for new_f in current_finding_ids.values():
            new_f.cycle = cycle
            reconciled.append(new_f)

        return reconciled

