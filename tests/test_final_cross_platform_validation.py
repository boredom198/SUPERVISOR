"""Comprehensive cross-platform engineering validation test suite for SUPERVISOR (Prompt 10).

Validates all 18 lifecycle, security, and cross-platform consistency scenarios:
1. Clean task with passing tests.
2. New defect introduced by current task.
3. Existing defect unrelated to current task.
4. Defect repaired after first review.
5. Repair that introduces a regression.
6. Tests that fail.
7. Test infrastructure missing (honest status, not silently passing).
8. Review timeout handling.
9. Malformed hook inputs across platforms.
10. Secret material redaction across source, diffs, and reports.
11. Repeated repair failures.
12. Retry limit reached.
13. Files changed after a previous successful review (must invalidate stale PASSED state).
14. Concurrent/overlapping sessions and PID liveness check.
15. Interrupted review recovery.
16. Non-Git working directory handling.
17. Report-only vs default review-repair invocation.
18. Consistency of completion decisions across all four platform adapters.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor.adapters.antigravity import AntigravityAdapter
from supervisor.adapters.claude_code import ClaudeCodeAdapter
from supervisor.adapters.codex import CodexAdapter
from supervisor.adapters.gitlab_duo import GitLabDuoAdapter
from supervisor.context import ContextCollector
from supervisor.models import LifecycleState, ReviewStatus, Severity, TestStatus
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.security import SecurityInspector
from supervisor.state import StateManager, is_pid_alive


class TestFinalCrossPlatformValidation(unittest.TestCase):
    """Rigorous cross-platform validation covering core lifecycle and all 4 adapters."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

        # Initialize real git repository
        subprocess.run(["git", "init"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "ValidationTester"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "validation@example.com"], cwd=str(self.workspace), check=True, capture_output=True)

        # Baseline commit
        (self.workspace / "README.md").write_text("# Project Root\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.workspace), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial baseline"], cwd=str(self.workspace), check=True, capture_output=True)

        self.state_mgr = StateManager(self.workspace)
        self.orchestrator = SupervisorOrchestrator(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    # Scenario 1: Clean task with passing tests
    def test_scenario_01_clean_task_with_passing_tests(self):
        (self.workspace / "math_ops.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_math_ops.py").write_text(
            "import unittest\nfrom math_ops import add\n\n"
            "class TestMath(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Implement add function", max_retries=3)
        report = self.orchestrator.execute_review_cycle()

        self.assertFalse(report.has_material_issues)
        self.assertEqual(report.state, LifecycleState.PASSED)
        self.assertEqual(report.overall_status, ReviewStatus.PASSED)
        self.assertTrue(any(t.passed for t in report.tests_run))

    # Scenario 2: New defect introduced by current task
    def test_scenario_02_new_defect_introduced_by_current_task(self):
        (self.workspace / "runner.py").write_text("def run(cmd):\n    import subprocess\n    subprocess.call(cmd, shell=True)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement command runner", max_retries=3)
        report = self.orchestrator.execute_review_cycle()

        self.assertTrue(report.has_material_issues)
        self.assertEqual(report.state, LifecycleState.FIXES_REQUIRED)
        self.assertTrue(any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in report.material_findings))

    # Scenario 3: Existing defect unrelated to current task
    def test_scenario_03_existing_unrelated_defect_captured(self):
        # Create an unrelated file with a defect
        (self.workspace / "legacy.py").write_text("def old_eval(x):\n    return eval(x)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement user service", max_retries=3)
        report = self.orchestrator.execute_review_cycle()

        # Engine flags security defect in workspace even if prompt was for user service
        self.assertTrue(any("eval" in f.description.lower() for f in report.material_findings))

    # Scenario 4: Defect repaired after first review
    def test_scenario_04_defect_repaired_after_first_review(self):
        target_file = self.workspace / "calc.py"
        target_file.write_text("def parse(e):\n    return eval(e)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build safe calculator", max_retries=3)
        rep1 = self.orchestrator.execute_review_cycle()
        self.assertTrue(rep1.has_material_issues)

        # Repair the defect and add tests
        target_file.write_text("def parse(e):\n    return int(e)\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_calc.py").write_text(
            "import unittest\nfrom calc import parse\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_p(self):\n        self.assertEqual(parse('42'), 42)\n",
            encoding="utf-8",
        )

        rep2 = self.orchestrator.execute_verification_cycle()
        self.assertFalse(rep2.has_material_issues)
        self.assertEqual(rep2.state, LifecycleState.PASSED)

    # Scenario 5: Repair introduces a regression
    def test_scenario_05_repair_introduces_regression(self):
        target_file = self.workspace / "calc.py"
        target_file.write_text("def parse(e):\n    return eval(e)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build safe calculator", max_retries=3)
        self.orchestrator.execute_review_cycle()

        # "Repair" fixes eval but introduces silent exception swallowing and hardcoded key
        target_file.write_text(
            "def parse(e):\n"
            "    try:\n"
            "        return int(e)\n"
            "    except:\n"
            "        pass\n"
            "API_KEY = 'FAKE_STRIPE_KEY_FOR_TESTS'\n",
            encoding="utf-8",
        )

        rep2 = self.orchestrator.execute_verification_cycle()
        self.assertTrue(rep2.has_material_issues)
        self.assertEqual(rep2.state, LifecycleState.FIXES_REQUIRED)

    # Scenario 6: Tests that fail
    def test_scenario_06_failing_tests(self):
        (self.workspace / "mod.py").write_text("def get_val(): return 1\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_mod.py").write_text(
            "import unittest\nfrom mod import get_val\n\n"
            "class TestMod(unittest.TestCase):\n"
            "    def test_val(self):\n        self.assertEqual(get_val(), 999)\n",  # Intentionally failing
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Implement mod", max_retries=3)
        report = self.orchestrator.execute_review_cycle()

        self.assertTrue(report.has_material_issues)
        self.assertTrue(any(f.category.value == "BUG" and "test" in f.file_path.lower() for f in report.material_findings))

    # Scenario 7: Test infrastructure missing (honest status)
    def test_scenario_07_test_infrastructure_missing_honest_status(self):
        (self.workspace / "feature.py").write_text("def do_something(): return True\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement feature", max_retries=3)
        report = self.orchestrator.execute_review_cycle()

        # Verification must NOT claim tests passed
        self.assertTrue(any(t.status == TestStatus.NOT_AVAILABLE for t in report.tests_run))
        self.assertEqual(report.overall_status, ReviewStatus.VERIFICATION_INCOMPLETE)
        self.assertTrue(any("no automated test suite" in lim.lower() for lim in report.limitations))

    # Scenario 8: Review timeout handling
    def test_scenario_08_review_timeout_handling(self):
        adapter = AntigravityAdapter(self.workspace)
        with patch.object(SupervisorOrchestrator, "execute_review_cycle", side_effect=TimeoutError("Review execution exceeded 60s")):
            payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
            res = adapter.handle_stop_payload(payload)
            # Must handle timeout safely and mark state BLOCKED
            self.assertEqual(res["decision"], "allow")
            self.assertIn("BLOCKED", res["reason"])
            state = self.state_mgr.load()
            self.assertEqual(state.state, LifecycleState.BLOCKED)

    # Scenario 9: Malformed hook input across platforms
    def test_scenario_09_malformed_hook_input_all_platforms(self):
        ag = AntigravityAdapter(self.workspace)
        codex = CodexAdapter(self.workspace)
        claude = ClaudeCodeAdapter(self.workspace)
        duo = GitLabDuoAdapter(self.workspace)

        malformed_inputs = ["", "{not valid json", "[1, 2, 3]", "   "]
        for bad_in in malformed_inputs:
            # Antigravity
            ag_res = json.loads(ag.process_raw_input(bad_in))
            self.assertEqual(ag_res["decision"], "allow")

            # Codex
            c_code, c_stdout, _ = codex.process_raw_input(bad_in)
            self.assertEqual(c_code, 0)

            # Claude Code
            cl_code, cl_stdout, _ = claude.process_raw_input(bad_in)
            self.assertEqual(cl_code, 0)

            # GitLab Duo
            d_code, d_stdout, _ = duo.process_raw_input(bad_in)
            self.assertEqual(d_code, 0)

    # Scenario 10: Secret material redaction across source, diffs, and reports
    def test_scenario_10_secret_material_redaction(self):
        secret_key = "FAKE_STRIPE_KEY_FOR_TESTS"
        (self.workspace / "auth.py").write_text(f"KEY = '{secret_key}'\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Setup auth key", max_retries=3)

        report = self.orchestrator.execute_review_cycle()
        rep_dict = report.to_dict()
        rep_str = json.dumps(rep_dict)

        # Raw secret token MUST NOT appear anywhere in the serialized report
        self.assertNotIn(secret_key, rep_str)
        self.assertIn("sk_live_", rep_str)
        self.assertIn("****************", rep_str)

    # Scenario 11: Repeated repair failures
    def test_scenario_11_repeated_repair_failures(self):
        (self.workspace / "app.py").write_text("def f(): eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Fix eval", max_retries=3)

        # Cycle 1
        rep1 = self.orchestrator.execute_review_cycle()
        self.assertEqual(rep1.state, LifecycleState.FIXES_REQUIRED)

        # Cycle 2 - agent fails to fix
        self.state_mgr.transition_to(LifecycleState.REPAIRING, "Agent attempted repair")
        rep2 = self.orchestrator.execute_verification_cycle()
        self.assertEqual(rep2.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(self.state_mgr.load().cycle, 2)

    # Scenario 12: Retry limit reached
    def test_scenario_12_retry_limit_reached(self):
        (self.workspace / "app.py").write_text("def f(): eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Fix eval", max_retries=2)

        self.orchestrator.execute_review_cycle()
        self.state_mgr.transition_to(LifecycleState.REPAIRING, "Attempt 1")
        self.orchestrator.execute_verification_cycle()

        # At max retries, state transitions to RETRY_LIMIT_REACHED
        self.state_mgr.transition_to(LifecycleState.REPAIRING, "Attempt 2")
        final_rep = self.orchestrator.execute_verification_cycle()
        self.assertEqual(final_rep.state, LifecycleState.RETRY_LIMIT_REACHED)

    # Scenario 13: Files changed after previous successful review
    def test_scenario_13_files_changed_after_previous_successful_review(self):
        calc = self.workspace / "calc.py"
        calc.write_text("def add(a, b): return a + b\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n"
            "class TestC(unittest.TestCase):\n    def test_a(self): self.assertEqual(add(1, 1), 2)\n",
            encoding="utf-8",
        )

        # 1. Clean review passes
        self.state_mgr.initialize_task(prompt="Add calculator", max_retries=3)
        report1 = self.orchestrator.execute_review_cycle()
        self.assertEqual(report1.state, LifecycleState.PASSED)
        self.assertEqual(self.state_mgr.load().state, LifecycleState.PASSED)

        # 2. Agent or user changes a file afterwards (introducing a defect)
        calc.write_text("def add(a, b): return eval(f'{a}+{b}')\n", encoding="utf-8")

        # 3. Hook is triggered again
        ag = AntigravityAdapter(self.workspace)
        payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        res = ag.handle_stop_payload(payload)

        # Must NOT return stale "session already concluded"; must detect changes and block with directives!
        self.assertEqual(res["decision"], "continue")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", res["reason"])
        self.assertIn("calc.py", res["reason"])

    # Scenario 14: Concurrent/overlapping sessions and PID liveness check
    def test_scenario_14_pid_liveness_and_active_review_guard(self):
        my_pid = os.getpid()
        self.assertTrue(is_pid_alive(my_pid))
        self.assertFalse(is_pid_alive(9999999))  # Guaranteed dead PID

        # When an active PID is set, is_review_active reports (in_progress=True, is_abandoned=False)
        st = self.state_mgr.initialize_task("Task", max_retries=3)
        st.state = LifecycleState.REVIEWING
        st.active_pid = my_pid
        self.state_mgr.save(st)

        in_progress, abandoned = self.state_mgr.is_review_active(st)
        self.assertTrue(in_progress)
        self.assertFalse(abandoned)

        # When PID is dead, is_review_active reports (in_progress=True, is_abandoned=True)
        st.active_pid = 9999999
        self.state_mgr.save(st)
        in_progress, abandoned = self.state_mgr.is_review_active(st)
        self.assertTrue(in_progress)
        self.assertTrue(abandoned)

    # Scenario 15: Interrupted review and stale state recovery
    def test_scenario_15_interrupted_review_recovery(self):
        st = self.state_mgr.initialize_task("Task", max_retries=3)
        st.state = LifecycleState.REVIEWING
        st.active_pid = 9999999  # Dead process PID
        self.state_mgr.save(st)

        claude = ClaudeCodeAdapter(self.workspace)
        payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        code, res, _ = claude.handle_stop_payload(payload)

        self.assertEqual(code, 0)
        # Verify it safely recovered from abandoned review
        self.assertNotIn("Review already in progress", res.get("reason", ""))

    # Scenario 16: Non-Git working directory handling
    def test_scenario_16_non_git_working_directory(self):
        with tempfile.TemporaryDirectory() as non_git_dir:
            ng_path = Path(non_git_dir).resolve()
            (ng_path / "hello.py").write_text("print('hello')\n", encoding="utf-8")

            collector = ContextCollector(ng_path)
            ctx = collector.collect()
            self.assertFalse(ctx.is_git_repo)
            self.assertTrue(any("not a git repository" in lim.lower() for lim in ctx.limitations))
            self.assertIn("hello.py", ctx.file_contents)

    # Scenario 17: Report-only vs default review-repair invocation
    def test_scenario_17_report_only_vs_default_invocation(self):
        (self.workspace / "leak.py").write_text("def f(): eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Check code", max_retries=3)

        # 1. Report-only mode: does NOT transition to REPAIRING and does NOT block
        duo = GitLabDuoAdapter(self.workspace)
        payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        code_ro, res_ro, _ = duo.handle_stop_payload(payload, report_only=True)
        self.assertEqual(code_ro, 0)
        self.assertEqual(res_ro["decision"], "allow")
        self.assertIn("report-only", res_ro["reason"].lower())

        # 2. Default mode: blocks with imperative exit code 2 and repair directives
        code_def, _, stderr_def = duo.handle_stop_payload(payload, report_only=False)
        self.assertEqual(code_def, 2)
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", stderr_def)

    # Scenario 18: Consistency of completion decisions across all 4 adapters
    def test_scenario_18_adapter_completion_consistency(self):
        (self.workspace / "clean.py").write_text("def ok(): return True\n", encoding="utf-8")
        tests_dir = self.workspace / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_clean.py").write_text(
            "import unittest\nfrom clean import ok\n\n"
            "class TestClean(unittest.TestCase):\n    def test_ok(self): self.assertTrue(ok())\n",
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Clean task", max_retries=3)

        # 1. Antigravity
        ag = AntigravityAdapter(self.workspace)
        res_ag = ag.handle_stop_payload({"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]})
        self.assertEqual(res_ag["decision"], "allow")

        # 2. Codex
        codex = CodexAdapter(self.workspace)
        code_c, res_c, _ = codex.handle_stop_payload({"hook_event_name": "Stop", "cwd": str(self.workspace)})
        self.assertEqual(code_c, 0)
        self.assertEqual(res_c["decision"], "allow")

        # 3. Claude Code
        claude = ClaudeCodeAdapter(self.workspace)
        code_cl, res_cl, _ = claude.handle_stop_payload({"hook_event_name": "Stop", "cwd": str(self.workspace)})
        self.assertEqual(code_cl, 0)
        self.assertEqual(res_cl["decision"], "allow")

        # 4. GitLab Duo
        duo = GitLabDuoAdapter(self.workspace)
        code_d, res_d, _ = duo.handle_stop_payload({"hook_event_name": "Stop", "cwd": str(self.workspace)})
        self.assertEqual(code_d, 0)
        self.assertEqual(res_d["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
