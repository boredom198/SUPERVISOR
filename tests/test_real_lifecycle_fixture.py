"""End-to-end integration test fixture running real unmocked subprocesses.

Validates the full Antigravity lifecycle sequence:
1. Introduce a detectable harmless defect (`eval`).
2. Trigger real subprocess `python -m supervisor.hooks.stop_hook` via stdin.
3. Verify output: decision="continue", repair directives generated, state=REPAIRING.
4. Perform repair: replace defect with clean code and tests.
5. Trigger real subprocess `python -m supervisor.hooks.stop_hook` via stdin again.
6. Verify output: decision="allow", state=PASSED.
7. Verify `python -m supervisor verify` exits 0 with zero unresolved findings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestRealLifecycleFixture(unittest.TestCase):
    """Real subprocess test fixture for end-to-end supervisor lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.supervisor_root = Path(__file__).resolve().parent.parent

        # Initialize real git repository in temp workspace
        subprocess.run(["git", "init"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "SupervisorTester"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(self.workspace), check=True, capture_output=True)

        # Initial commit
        (self.workspace / "README.md").write_text("# Test Project\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.workspace), check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_real_lifecycle_cycle(self):
        env = os.environ.copy()
        # Ensure supervisor module is importable by subprocess
        env["PYTHONPATH"] = str(self.supervisor_root)

        # 1. Introduce harmless detectable defect in workspace
        calc_file = self.workspace / "calc.py"
        calc_file.write_text("def compute(expr):\n    return eval(expr)\n", encoding="utf-8")

        # 2. Call python -m supervisor.hooks.stop_hook via subprocess
        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        proc = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, f"stop_hook failed: {proc.stderr}")
        resp1 = json.loads(proc.stdout.strip())

        # 3. Assert continuation directive returned
        self.assertEqual(resp1["decision"], "continue")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", resp1["reason"])
        self.assertIn("calc.py", resp1["reason"])

        # Check state and artifacts
        state_file = self.workspace / ".supervisor" / "state.json"
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertTrue(state_file.exists(), "state.json was not created")
        self.assertTrue(directives_file.exists(), "repair_directives.md was not created")

        with open(state_file, "r", encoding="utf-8") as f:
            state_data1 = json.load(f)
        self.assertEqual(state_data1["state"], "REPAIRING")
        self.assertEqual(state_data1["cycle"], 1)

        # 4. Repair the defect: safe compute function + unit test
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

        # 5. Call stop_hook again (cycle 2 verification)
        proc2 = subprocess.run(
            [sys.executable, "-m", "supervisor.hooks.stop_hook"],
            input=json.dumps(payload),
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc2.returncode, 0, f"stop_hook cycle 2 failed: {proc2.stderr}")
        resp2 = json.loads(proc2.stdout.strip())

        # 6. Assert verification PASSED and hook allowed termination
        self.assertEqual(resp2["decision"], "allow", f"Decision was not allow: {resp2.get('reason')}")
        self.assertIn("PASSED", resp2["reason"])

        with open(state_file, "r", encoding="utf-8") as f:
            state_data2 = json.load(f)
        self.assertEqual(state_data2["state"], "PASSED")
        self.assertEqual(state_data2["cycle"], 2)

        # 7. Run `python -m supervisor verify` in the workspace
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
