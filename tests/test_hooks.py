"""Tests for supervisor.hooks.stop_hook."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from supervisor.hooks.stop_hook import handle_stop_event
from supervisor.state import StateManager


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.orig_stdin = sys.stdin
        self.orig_stdout = sys.stdout
        self.captured_stdout = io.StringIO()
        sys.stdout = self.captured_stdout

    def tearDown(self):
        sys.stdin = self.orig_stdin
        sys.stdout = self.orig_stdout
        self.temp_dir.cleanup()

    def test_stop_hook_skips_non_model_stop(self):
        payload = {
            "terminationReason": "user_cancelled",
            "workspacePaths": [str(self.workspace)],
        }
        sys.stdin = io.StringIO(json.dumps(payload))
        handle_stop_event()

        output = json.loads(self.captured_stdout.getvalue())
        self.assertEqual(output["decision"], "allow")
        self.assertIn("user_cancelled", output["reason"])

    def test_stop_hook_intercepts_defect_and_continues(self):
        # Create a file with a defect
        (self.workspace / "flawed.py").write_text("def run(cmd):\n    eval(cmd)\n", encoding="utf-8")

        mgr = StateManager(self.workspace)
        mgr.initialize_task(prompt="Build flawed app", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        sys.stdin = io.StringIO(json.dumps(payload))
        handle_stop_event()

        output = json.loads(self.captured_stdout.getvalue())
        self.assertEqual(output["decision"], "continue")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", output["reason"])
        self.assertIn("flawed.py", output["reason"])

    def test_stop_hook_allows_when_clean(self):
        (self.workspace / "clean.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (self.workspace / "test_clean.py").write_text("def test_run(): pass\n", encoding="utf-8")

        mgr = StateManager(self.workspace)
        mgr.initialize_task(prompt="Build clean app", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        sys.stdin = io.StringIO(json.dumps(payload))
        handle_stop_event()

        output = json.loads(self.captured_stdout.getvalue())
        self.assertEqual(output["decision"], "allow")
        self.assertIn("PASSED", output["reason"])


if __name__ == "__main__":
    unittest.main()
