"""End-to-end integration tests simulating the 9-stage SUPERVISOR lifecycle."""

import tempfile
import unittest
from pathlib import Path

from supervisor.models import LifecycleState
from supervisor.orchestrator import SupervisorOrchestrator


class TestLifecycleE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.orchestrator = SupervisorOrchestrator(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_review_repair_verification_pass_cycle(self):
        """Simulates Stage 1 through Stage 9 where a defect is caught, repaired, and verified."""
        prompt = "Create an authentication token parser and validator."
        target_file = self.workspace / "auth.py"
        test_file = self.workspace / "test_auth.py"

        # Stage 1: Initialize Task
        self.orchestrator.state_mgr.initialize_task(prompt=prompt, max_retries=3)

        # Stage 2: Agent implements code with a security defect (eval)
        target_file.write_text("def parse_token(raw):\n    return eval(raw)\n", encoding="utf-8")
        test_file.write_text("def test_dummy(): pass\n", encoding="utf-8")

        # Stage 3, 4, 5: Trigger review and generate report
        report1 = self.orchestrator.execute_review_cycle()
        self.assertTrue(report1.has_material_issues)
        self.assertEqual(report1.state, LifecycleState.FIXES_REQUIRED)

        # Verify Stage 5 report was generated
        latest_report = self.workspace / ".supervisor" / "latest_report.md"
        self.assertTrue(latest_report.exists())

        # Stage 6: Generate repair directives
        directives = (self.workspace / ".supervisor" / "repair_directives.md").read_text(encoding="utf-8")
        self.assertIn("auth.py", directives)
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", directives)

        # Agent repairs the code
        target_file.write_text(
            "import json\ndef parse_token(raw):\n    return json.loads(raw)\n",
            encoding="utf-8",
        )

        # Stage 7 & 8: Verify corrections
        report2 = self.orchestrator.execute_verification_cycle()

        # Stage 9: Task is marked PASSED with zero material issues
        self.assertFalse(report2.has_material_issues)
        self.assertEqual(report2.state, LifecycleState.PASSED)
        final_state = self.orchestrator.state_mgr.load()
        self.assertEqual(final_state.state, LifecycleState.PASSED)

    def test_retry_limit_reached_on_persistent_defect(self):
        """Simulates reaching retry limit when the agent fails to resolve an issue."""
        prompt = "Build secure payment processor"
        target_file = self.workspace / "pay.py"

        # Initialize with max_retries = 2
        self.orchestrator.state_mgr.initialize_task(prompt=prompt, max_retries=2)

        # Defect in code
        target_file.write_text("def process(cmd):\n    eval(cmd)\n", encoding="utf-8")

        # Cycle 1: Review
        r1 = self.orchestrator.execute_review_cycle()
        self.assertTrue(r1.has_material_issues)
        self.assertEqual(r1.state, LifecycleState.FIXES_REQUIRED)

        # Cycle 2: Agent fails to fix, verifier runs
        r2 = self.orchestrator.execute_verification_cycle()
        self.assertTrue(r2.has_material_issues)
        self.assertEqual(r2.state, LifecycleState.RETRY_LIMIT_REACHED)

        final_state = self.orchestrator.state_mgr.load()
        self.assertEqual(final_state.state, LifecycleState.RETRY_LIMIT_REACHED)


if __name__ == "__main__":
    unittest.main()

