"""Tests for supervisor.models."""

import unittest
from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    LifecycleState,
    Severity,
    SupervisorReport,
    TaskSpec,
    TestResult,
)


class TestModels(unittest.TestCase):
    def test_finding_serialization(self):
        finding = Finding(
            finding_id="SEC-0001",
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            file_path="app.py",
            line_number=42,
            description="Use of eval() detected.",
            evidence="eval(user_input)",
            why_it_matters="Arbitrary code execution.",
            recommended_correction="Use ast.literal_eval.",
            verification_method="Static AST check",
            status=FindingStatus.CONFIRMED,
            cycle=1,
        )
        d = finding.to_dict()
        self.assertEqual(d["severity"], "CRITICAL")
        self.assertEqual(d["category"], "SECURITY")
        self.assertEqual(d["status"], "CONFIRMED")

        restored = Finding.from_dict(d)
        self.assertEqual(restored.finding_id, "SEC-0001")
        self.assertEqual(restored.severity, Severity.CRITICAL)
        self.assertEqual(restored.line_number, 42)

    def test_report_material_findings(self):
        f_crit = Finding(
            finding_id="F-1",
            severity=Severity.CRITICAL,
            category=Category.BUG,
            file_path="a.py",
            description="Crit",
            evidence="...",
            why_it_matters="...",
            recommended_correction="...",
            verification_method="...",
            status=FindingStatus.CONFIRMED,
        )
        f_info = Finding(
            finding_id="F-2",
            severity=Severity.INFO,
            category=Category.TESTING,
            file_path="b.py",
            description="Info note",
            evidence="...",
            why_it_matters="...",
            recommended_correction="...",
            verification_method="...",
            status=FindingStatus.CONFIRMED,
        )
        f_resolved = Finding(
            finding_id="F-3",
            severity=Severity.HIGH,
            category=Category.BUG,
            file_path="c.py",
            description="High bug resolved",
            evidence="...",
            why_it_matters="...",
            recommended_correction="...",
            verification_method="...",
            status=FindingStatus.RESOLVED,
        )

        report = SupervisorReport(
            report_id="rep_1",
            task_id="task_1",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[f_crit, f_info, f_resolved],
        )

        self.assertTrue(report.has_material_issues)
        self.assertEqual(len(report.material_findings), 1)
        self.assertEqual(report.material_findings[0].finding_id, "F-1")

    def test_task_spec_serialization(self):
        task = TaskSpec(
            task_id="task_100",
            prompt="Implement user authentication with JWT.",
            requirements=["Must support token expiration", "Must validate signature"],
        )
        d = task.to_dict()
        restored = TaskSpec.from_dict(d)
        self.assertEqual(restored.task_id, "task_100")
        self.assertEqual(len(restored.requirements), 2)


if __name__ == "__main__":
    unittest.main()
