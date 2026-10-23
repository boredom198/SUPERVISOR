"""Real unmocked subprocess integration test fixture for Claude Code Stop hook lifecycle.

Validates the complete Claude Code lifecycle sequence:
1. Introduce a detectable harmless defect (`eval`).
2. Trigger real subprocess `python -m supervisor.hooks.claude_code_stop_hook` via stdin in structured mode.
3. Verify output: exit code 0, decision="block", repair directives generated, state=REPAIRING.
4. Test imperative mode: trigger with SUPERVISOR_CLAUDE_EXIT_CODE_BLOCK=1 and verify exit code 2 with stderr directives.
5. Test report-only mode: trigger with --report-only argument and verify decision="allow".
6. Perform repair: replace defect with clean code and tests.
7. Trigger real subprocess `python -m supervisor.hooks.claude_code_stop_hook` via stdin again.
8. Verify output: exit code 0, decision="allow", state=PASSED.
9. Verify `python -m supervisor verify` exits 0 with zero unresolved findings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestClaudeCodeRealLifecycleFixture(unittest.TestCase):
    """Real subprocess test fixture for Claude Code lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.supervisor_root = Path(__file__).resolve().parent.parent

        # Initialize real git repository in temp workspace
        subprocess.run(["git", "init"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "ClaudeSupervisorTester"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "claude-tester@example.com"], cwd=str(self.workspace), check=True, capture_output=True)

        # Initial commit
        (self.workspace / "README.md").write_text("# Test Claude Code Project\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.workspace), check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_claude_code_real_lifecycle_cycle(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.supervisor_root)

        # 1. Introduce harmless detectable defect in workspace
        calc_file = self.workspace / "calc.py"
        calc_file.write_text("def compute(expr):\n    return eval(expr)\n", encoding="utf-8")

        # 2. Call python -m supervisor.hooks.claude_code_stop_hook via subprocess (Structured Mode)
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Implemented compute function with eval",
        }
        proc = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.claude_code_stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, f"claude_code_stop_hook failed: {proc.stderr}")
        resp1 = json.loads(proc.stdout.strip())

        # 3. Assert blocking directive returned
        self.assertEqual(resp1["decision"], "block")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", resp1["reason"])
        self.assertIn("calc.py", resp1["reason"])

        state_file = self.workspace / ".supervisor" / "state.json"
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertTrue(state_file.exists(), "state.json was not created")
        self.assertTrue(directives_file.exists(), "repair_directives.md was not created")

        with open(state_file, "r", encoding="utf-8") as f:
            state_data1 = json.load(f)
        self.assertEqual(state_data1["state"], "REPAIRING")
        self.assertEqual(state_data1["cycle"], 1)

        # 4. Test imperative exit code 2 mode
        env_exit2 = env.copy()
        env_exit2["SUPERVISOR_CLAUDE_EXIT_CODE_BLOCK"] = "1"
        proc_exit2 = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.claude_code_stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env_exit2,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_exit2.returncode, 2, f"Expected exit code 2, got {proc_exit2.returncode}")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", proc_exit2.stderr)

        # 5. Test report-only CLI argument
        proc_report_only = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.claude_code_stop_hook", "--report-only"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_report_only.returncode, 0)
        resp_ro = json.loads(proc_report_only.stdout.strip())
        self.assertEqual(resp_ro["decision"], "allow")
        self.assertIn("report-only", resp_ro["reason"].lower())

        # 6. Repair the defect: safe compute function + unit test
        calc_file.write_text("def compute(a, b):\n    return a + b\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        test_calc = tests_dir / "test_calc.py"
        test_calc.write_text(
            "import unittest\n"
            "from calc import compute\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_compute(self):\n"
            "        self.assertEqual(compute(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        # 7. Call claude_code_stop_hook again (cycle verification)
        proc2 = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.claude_code_stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc2.returncode, 0, f"claude_code_stop_hook cycle verification failed: {proc2.stderr}")
        resp2 = json.loads(proc2.stdout.strip())

        # 8. Assert verification PASSED and hook allowed termination
        self.assertEqual(resp2["decision"], "allow")
        self.assertIn("PASSED", resp2["reason"])

        with open(state_file, "r", encoding="utf-8") as f:
            state_data2 = json.load(f)
        self.assertEqual(state_data2["state"], "PASSED")

        # 9. Run `python -m supervisor verify` in the workspace
        proc_verify = subprocess.run(
            [sys.executable, "-m", "supervisor", "verify"],
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_verify.returncode, 0, f"supervisor verify failed: {proc_verify.stderr}")


if __name__ == "__main__":
    unittest.main()

