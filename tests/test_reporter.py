"""Tests for supervisor.reporter and ReportGenerator."""

import tempfile
import unittest
from pathlib import Path

from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    LifecycleState,
    Severity,
    SupervisorReport,
    TestResult,
)
from supervisor.reporter import ReportGenerator


class TestReporter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.reporter = ReportGenerator(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_to_markdown_formatting(self):
        finding = Finding(
            finding_id="BUG-0001",
            severity=Severity.HIGH,
            category=Category.BUG,
            file_path="src/main.py",
            line_number=10,
            description="Index out of range on empty list.",
            evidence="items[0]",
            why_it_matters="Crashes worker on empty payload.",
            recommended_correction="Add check: if not items: return",
            verification_method="Run test_empty_list()",
            status=FindingStatus.CONFIRMED,
        )
        test_res = TestResult(
            command="python -m unittest",
            exit_code=0,
            stdout="Ran 5 tests in 0.01s OK",
            stderr="",
            duration_seconds=0.01,
            passed=True,
        )
        report = SupervisorReport(
            report_id="rep_test",
            task_id="task_test",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[finding],
            tests_run=[test_res],
            summary="Identified 1 high severity defect.",
        )

        md = self.reporter.to_markdown(report)
        self.assertIn("BUG-0001", md)
        self.assertIn("src/main.py:10", md)
        self.assertIn("Crashes worker on empty payload.", md)
        self.assertIn("Validation & Tests Executed", md)

    def test_save_reports_files_written(self):
        report = SupervisorReport(
            report_id="rep_save",
            task_id="task_save",
            cycle=1,
            state=LifecycleState.PASSED,
            findings=[],
            summary="All clean.",
        )
        paths = self.reporter.save_reports(report)
        self.assertTrue(paths.md_path.exists())
        self.assertTrue(paths.json_path.exists())


if __name__ == "__main__":
    unittest.main()

