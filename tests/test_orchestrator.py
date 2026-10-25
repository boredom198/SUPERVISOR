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

    def test_new_prompt_starts_fresh_task_before_previous_task_is_resolved(self):
        # Task A finds a defect and leaves the lifecycle in FIXES_REQUIRED.
        bad_file = self.workspace / "bad.py"
        bad_file.write_text("def run(value):\n    return eval(value)\n", encoding="utf-8")
        first = self.orchestrator.execute_review_cycle(prompt="First prompt: build unsafe runner")
        self.assertTrue(first.has_material_issues)
        first_state = self.orchestrator.state_mgr.load()
        self.assertEqual(first_state.state, LifecycleState.FIXES_REQUIRED)
        first_task_id = first_state.task.task_id

        # The user starts a different task before Task A is verified. Old
        # findings must not be carried into Task B's report as RESOLVED.
        bad_file.unlink()
        (self.workspace / "clean.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
        second = self.orchestrator.execute_review_cycle(
            prompt="Second prompt: build a greeting function"
        )
        second_state = self.orchestrator.state_mgr.load()

        self.assertNotEqual(second_state.task.task_id, first_task_id)
        self.assertEqual(second_state.task.prompt, "Second prompt: build a greeting function")
        self.assertEqual(second.task_id, second_state.task.task_id)
        self.assertEqual(second.cycle, 1)
        self.assertFalse(any(f.file_path == "bad.py" for f in second.findings))
        self.assertEqual(second_state.latest_report.report_id, second.report_id)

    def test_verification_replaces_latest_report_after_fix(self):
        bad_file = self.workspace / "bad.py"
        bad_file.write_text("def run(value):\n    return eval(value)\n", encoding="utf-8")
        (self.workspace / "test_bad.py").write_text(
            "def test_placeholder():\n    assert True\n", encoding="utf-8"
        )
        first = self.orchestrator.execute_review_cycle(prompt="Fix unsafe runner")
        self.assertTrue(first.has_material_issues)

        bad_file.write_text("def run(value):\n    return str(value)\n", encoding="utf-8")
        verified = self.orchestrator.execute_verification_cycle()
        state = self.orchestrator.state_mgr.load()
        latest_md = (self.workspace / ".supervisor" / "latest_report.md").read_text(encoding="utf-8")

        self.assertEqual(verified.state, LifecycleState.PASSED)
        self.assertEqual(state.latest_report.report_id, verified.report_id)
        self.assertIn("Verification PASSED", latest_md)
        self.assertTrue(any(f.file_path == "bad.py" and f.status.value == "RESOLVED" for f in verified.findings))

    def test_retry_limit_reached(self):
        # Set max_retries to 1
        self.orchestrator.state_mgr.initialize_task(prompt="Impossible task", max_retries=1)
        bad_code = "def broken(cmd):\n    eval(cmd)\n"
        (self.workspace / "bad.py").write_text(bad_code, encoding="utf-8")

        report = self.orchestrator.execute_review_cycle()
        self.assertEqual(report.state, LifecycleState.RETRY_LIMIT_REACHED)


if __name__ == "__main__":
    unittest.main()

