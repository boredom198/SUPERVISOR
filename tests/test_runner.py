"""Tests for supervisor.runner and TestRunner."""

import json
import tempfile
import unittest
from pathlib import Path

from supervisor.models import TestStatus
from supervisor.runner import TestRunner


class TestTestRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.runner = TestRunner(self.workspace, timeout_seconds=5)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discover_no_tests(self):
        commands = self.runner.discover_test_commands()
        self.assertEqual(len(commands), 0)

    def test_discover_tests_dir(self):
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "test_sample.py").write_text("pass", encoding="utf-8")
        commands = self.runner.discover_test_commands()
        self.assertEqual(len(commands), 1)
        self.assertIn("python -m unittest discover -s tests", commands[0][0])

    def test_discover_package_json(self):
        pkg = {"scripts": {"test": "jest"}}
        (self.workspace / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        commands = self.runner.discover_test_commands()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "npm test")

    def test_execute_all_not_available_when_no_tests(self):
        results = self.runner.execute_all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, TestStatus.NOT_AVAILABLE)
        self.assertFalse(results[0].passed)

    def test_run_command_success(self):
        res = self.runner.run_command("python -c \"print('hello')\"")
        self.assertEqual(res.status, TestStatus.PASSED)
        self.assertTrue(res.passed)
        self.assertIn("hello", res.stdout)

    def test_run_command_failure(self):
        res = self.runner.run_command("python -c \"import sys; sys.exit(2)\"")
        self.assertEqual(res.status, TestStatus.FAILED)
        self.assertFalse(res.passed)
        self.assertEqual(res.exit_code, 2)

    def test_run_command_blocked_by_policy(self):
        restricted_runner = TestRunner(self.workspace, allow_repo_scripts=False)
        res = restricted_runner.run_command("npm test")
        self.assertEqual(res.status, TestStatus.BLOCKED)
        self.assertFalse(res.passed)
        self.assertIn("Authorization policy", res.reason)

    def test_run_command_timeout(self):
        fast_runner = TestRunner(self.workspace, timeout_seconds=1)
        res = fast_runner.run_command("python -c \"import time; time.sleep(3)\"")
        self.assertEqual(res.status, TestStatus.BLOCKED)
        self.assertFalse(res.passed)
        self.assertIn("timeout", res.reason.lower())


    def test_zero_test_unittest_run_is_not_reported_as_passed(self):
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "test_empty.py").write_text("pass\n", encoding="utf-8")
        result = self.runner.execute_all()[0]
        self.assertEqual(result.status, TestStatus.NOT_AVAILABLE)
        self.assertFalse(result.passed)

    def test_output_capping(self):
        capped_runner = TestRunner(self.workspace, max_output_chars=50)
        res = capped_runner.run_command("python -c \"print('A' * 200)\"")
        self.assertLessEqual(len(res.stdout), 55)


if __name__ == "__main__":
    unittest.main()

