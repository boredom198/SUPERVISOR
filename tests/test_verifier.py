"""Tests for supervisor.verifier and VerificationEngine."""

import tempfile
import unittest
from pathlib import Path

from supervisor.context import WorkspaceContext
from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    LifecycleState,
    Severity,
    SupervisorReport,
    TaskSpec,
)
from supervisor.verifier import VerificationEngine


class TestVerifier(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.verifier = VerificationEngine(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_verify_resolves_finding_when_fixed(self):
        prev_finding = Finding(
            finding_id="SYNTAX-0001",
            severity=Severity.CRITICAL,
            category=Category.BUG,
            file_path="app.py",
            description="Syntax error in app.py",
            evidence="invalid code",
            why_it_matters="Fails to run",
            recommended_correction="Fix syntax",
            verification_method="ast.parse",
            status=FindingStatus.CONFIRMED,
        )
        prev_report = SupervisorReport(
            report_id="rep_1",
            task_id="task_1",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[prev_finding],
        )

        # Repaired file
        fixed_code = "def valid():\n    return 42\n"
        context = WorkspaceContext(
            task=TaskSpec(task_id="task_1", prompt="Fix app"),
            workspace_root=self.workspace,
            file_contents={"app.py": fixed_code, "test_app.py": "def test_ok(): pass\n"},
        )

        summary = self.verifier.verify_cycle(
            context=context,
            previous_report=prev_report,
            cycle=2,
        )

        self.assertTrue(summary.passed)
        self.assertEqual(len(summary.resolved_findings), 1)
        self.assertEqual(summary.resolved_findings[0].status, FindingStatus.RESOLVED)
        self.assertEqual(len(summary.unresolved_findings), 0)

    def test_verify_detects_new_regression(self):
        prev_finding = Finding(
            finding_id="SYNTAX-0001",
            severity=Severity.CRITICAL,
            category=Category.BUG,
            file_path="app.py",
            description="Syntax error",
            evidence="...",
            why_it_matters="...",
            recommended_correction="...",
            verification_method="...",
            status=FindingStatus.CONFIRMED,
        )
        prev_report = SupervisorReport(
            report_id="rep_1",
            task_id="task_1",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[prev_finding],
        )

        # Fix syntax error, but introduce critical security flaw (eval)
        regression_code = "def valid(cmd):\n    return eval(cmd)\n"
        context = WorkspaceContext(
            task=TaskSpec(task_id="task_1", prompt="Fix app"),
            workspace_root=self.workspace,
            file_contents={"app.py": regression_code},
        )

        summary = self.verifier.verify_cycle(
            context=context,
            previous_report=prev_report,
            cycle=2,
        )

        self.assertFalse(summary.passed)
        self.assertEqual(len(summary.resolved_findings), 1)  # syntax resolved
        self.assertGreater(len(summary.new_regressions), 0)  # new security regression caught!


if __name__ == "__main__":
    unittest.main()
