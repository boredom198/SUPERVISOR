"""Tests for supervisor.repair and RepairHandoff."""

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
)
from supervisor.repair import RepairHandoff


class TestRepair(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.handoff = RepairHandoff(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_repair_prompt_actionable(self):
        finding = Finding(
            finding_id="SEC-0099",
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            file_path="auth.py",
            line_number=25,
            description="Hardcoded admin password detected.",
            evidence='password = "supersecret"',
            why_it_matters="Allows credential compromise.",
            recommended_correction="Read password from os.environ.",
            verification_method="Static inspection",
            status=FindingStatus.CONFIRMED,
        )
        report = SupervisorReport(
            report_id="rep_sec",
            task_id="task_sec",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[finding],
        )

        prompt = self.handoff.generate_repair_prompt(report, original_task="Secure authentication service")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", prompt)
        self.assertIn("SEC-0099", prompt)
        self.assertIn("auth.py:25", prompt)
        self.assertIn("Read password from os.environ.", prompt)
        self.assertIn("Do NOT wait for the user to prompt you", prompt)

        # Check file was written to disk
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertTrue(directives_file.exists())


if __name__ == "__main__":
    unittest.main()

