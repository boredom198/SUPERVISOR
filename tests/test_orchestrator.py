"""Tests for supervisor.orchestrator."""

import tempfile
import unittest
from pathlib import Path

from supervisor.models import LifecycleState
from supervisor.orchestrator import SupervisorOrchestrator


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.orchestrator = SupervisorOrchestrator(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_review_passes(self):
        clean_code = "def hello():\n    return 'world'\n"
        test_code = "def test_hello(): pass\n"
        (self.workspace / "clean.py").write_text(clean_code, encoding="utf-8")
        (self.workspace / "test_clean.py").write_text(test_code, encoding="utf-8")

        report = self.orchestrator.execute_review_cycle(prompt="Create clean module")
        self.assertFalse(report.has_material_issues)
        self.assertEqual(report.state, LifecycleState.PASSED)

    def test_report_only_mode(self):
        bad_code = "def broken(cmd):\n    eval(cmd)\n"
        (self.workspace / "bad.py").write_text(bad_code, encoding="utf-8")

        report = self.orchestrator.execute_review_cycle(
            prompt="Create module",
            report_only=True,
        )
        self.assertTrue(report.has_material_issues)
        state = self.orchestrator.state_mgr.load()
        # In report-only mode, state records fixes required but repair directives are not fired
        self.assertEqual(state.state, LifecycleState.FIXES_REQUIRED)

    def test_retry_limit_reached(self):
        # Set max_retries to 1
        self.orchestrator.state_mgr.initialize_task(prompt="Impossible task", max_retries=1)
        bad_code = "def broken(cmd):\n    eval(cmd)\n"
        (self.workspace / "bad.py").write_text(bad_code, encoding="utf-8")

        report = self.orchestrator.execute_review_cycle()
        self.assertEqual(report.state, LifecycleState.RETRY_LIMIT_REACHED)


if __name__ == "__main__":
    unittest.main()
