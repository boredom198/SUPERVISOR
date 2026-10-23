"""Real unmocked subprocess integration test fixture for GitLab Duo CLI Stop hook lifecycle.

Validates the complete GitLab Duo CLI lifecycle sequence:
1. Introduce a detectable harmless defect (`eval`).
2. Trigger real subprocess `python -m supervisor.hooks.gitlab_duo_stop_hook` via stdin in default imperative mode.
3. Verify output: exit code 2, repair directives output to stderr, state=REPAIRING.
4. Test structured JSON mode: trigger with --json-block flag and verify exit code 0 with decision="block".
5. Test report-only mode: trigger with --report-only argument and verify exit code 0 with decision="allow".
6. Perform repair: replace defect with clean code and tests.
7. Trigger real subprocess `python -m supervisor.hooks.gitlab_duo_stop_hook` via stdin again.
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


class TestGitLabDuoRealLifecycleFixture(unittest.TestCase):
    """Real subprocess test fixture for GitLab Duo CLI lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.supervisor_root = Path(__file__).resolve().parent.parent

        # Initialize real git repository in temp workspace
        subprocess.run(["git", "init"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "GitLabDuoSupervisorTester"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "duo-tester@example.com"], cwd=str(self.workspace), check=True, capture_output=True)

        # Initial commit
        (self.workspace / "README.md").write_text("# Test GitLab Duo Project\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.workspace), check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_gitlab_duo_real_lifecycle_cycle(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.supervisor_root)
        env["DUO_PROJECT_DIR"] = str(self.workspace)
        env["DUO_SESSION_ID"] = "session-real-fixture-123"

        # 1. Introduce harmless detectable defect in workspace
        calc_file = self.workspace / "calc.py"
        calc_file.write_text("def compute(expr):\n    return eval(expr)\n", encoding="utf-8")

        # 2. Call python -m supervisor.hooks.gitlab_duo_stop_hook via subprocess (Default Imperative Mode)
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "session_id": "session-real-fixture-123",
            "last_assistant_message": "Implemented compute function using eval in calc.py",
        }
        proc = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.gitlab_duo_stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        # 3. Assert imperative exit code 2 and directive in stderr
        self.assertEqual(proc.returncode, 2, f"Expected exit code 2 for GitLab Duo block, got {proc.returncode}. stderr: {proc.stderr}")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", proc.stderr)
        self.assertIn("calc.py", proc.stderr)

        state_file = self.workspace / ".supervisor" / "state.json"
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertTrue(state_file.exists(), "state.json was not created")
        self.assertTrue(directives_file.exists(), "repair_directives.md was not created")

        with open(state_file, "r", encoding="utf-8") as f:
            state_data1 = json.load(f)
        self.assertEqual(state_data1["state"], "REPAIRING")
        self.assertEqual(state_data1["cycle"], 1)

        # 4. Test structured JSON mode (--json-block)
        proc_json = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.gitlab_duo_stop_hook", "--json-block"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_json.returncode, 0, f"Expected exit code 0 for JSON mode, got {proc_json.returncode}. stderr: {proc_json.stderr}")
        resp_json = json.loads(proc_json.stdout.strip())
        self.assertEqual(resp_json["decision"], "block")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", resp_json["reason"])

        # 5. Test report-only CLI argument
        proc_report_only = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.gitlab_duo_stop_hook", "--report-only"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_report_only.returncode, 0)
        self.assertIn("report-only", proc_report_only.stdout.lower())

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

        # 7. Call gitlab_duo_stop_hook again (cycle verification)
        proc2 = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.gitlab_duo_stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc2.returncode, 0, f"gitlab_duo_stop_hook cycle verification failed: {proc2.stderr}")
        self.assertIn("PASSED", proc2.stdout)

        with open(state_file, "r", encoding="utf-8") as f:
            state_data2 = json.load(f)
        self.assertEqual(state_data2["state"], "PASSED")

        # 8. Run `python -m supervisor verify` in the workspace
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
