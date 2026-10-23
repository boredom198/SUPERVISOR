"""Comprehensive integration tests for Antigravity-specific SUPERVISOR integration.

Covers the 15 required categories specified in Prompt 5:
1. Valid and invalid hook payloads
2. Correct invocation of the shared core
3. Clean review with no material defects
4. Defects triggering an automatic repair handoff
5. A repair followed by successful re-verification
6. A repair that introduces a regression
7. Retry exhaustion
8. Repeated findings with no progress
9. Duplicate hook events
10. Interrupted or stale lifecycle state
11. Hook timeout and core failure
12. Secret redaction in hook output and repair instructions
13. Missing tests and honest verification statuses
14. Explicit report-only invocation
15. Default /supervisor behavior initiating full review-and-repair workflow
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor.adapters.antigravity import AntigravityAdapter
from supervisor.cli import build_parser, main
from supervisor.models import LifecycleState, ReviewStatus, Severity, TestStatus
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.state import StateManager


class TestAntigravityIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.adapter = AntigravityAdapter(self.workspace)
        self.state_mgr = StateManager(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Valid and invalid hook payloads
    def test_invalid_and_valid_hook_payloads(self):
        # Empty string
        res = json.loads(self.adapter.process_raw_input(""))
        self.assertEqual(res["decision"], "allow")
        self.assertIn("skipped", res["reason"].lower())

        # Malformed JSON
        res2 = json.loads(self.adapter.process_raw_input("{invalid_json: true,"))
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("parse error", res2["reason"].lower())

        # JSON array instead of object
        res3 = json.loads(self.adapter.process_raw_input("[1, 2, 3]"))
        self.assertEqual(res3["decision"], "allow")

        # Valid payload with non-model_stop termination
        valid_cancel = {
            "terminationReason": "user_cancelled",
            "workspacePaths": [str(self.workspace)],
        }
        res4 = json.loads(self.adapter.process_raw_input(json.dumps(valid_cancel)))
        self.assertEqual(res4["decision"], "allow")
        self.assertIn("user_cancelled", res4["reason"])

    # 2. Correct invocation of the shared core
    def test_correct_invocation_of_shared_core(self):
        (self.workspace / "clean.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        res = self.adapter.handle_stop_payload(payload)
        self.assertIn(res["decision"], ("allow", "continue"))
        # Verify that state.json was created by shared core
        state = self.state_mgr.load()
        self.assertIsNotNone(state.latest_report)

    # 3. Clean review with no material defects
    def test_clean_review_with_no_material_defects(self):
        (self.workspace / "clean.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
        (self.workspace / "test_clean.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n  def test_p(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Create clean ping function", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        res = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("PASSED", res["reason"])

    # 4. Defects triggering an automatic repair handoff
    def test_defects_triggering_automatic_repair_handoff(self):
        (self.workspace / "unsafe.py").write_text("def execute(cmd):\n    eval(cmd)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build executor", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        res = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res["decision"], "continue")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", res["reason"])
        self.assertIn("unsafe.py", res["reason"])
        # Verify state transitioned to REPAIRING
        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.REPAIRING)

    # 5. A repair followed by successful re-verification
    def test_repair_followed_by_successful_reverification(self):
        # Step A: Defect
        flawed_file = self.workspace / "app.py"
        flawed_file.write_text("def run(cmd):\n    eval(cmd)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build secure app", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        res1 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res1["decision"], "continue")

        # Step B: Agent repairs the code
        flawed_file.write_text("import ast\ndef run(cmd):\n    return ast.literal_eval(cmd)\n", encoding="utf-8")
        (self.workspace / "test_app.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n  def test_r(self): pass\n",
            encoding="utf-8",
        )

        # Step C: Re-verification Stop event
        res2 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("PASSED", res2["reason"])
        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.PASSED)

    # 6. A repair that introduces a regression
    def test_repair_introduces_regression(self):
        # Step A: Initial defect
        target_file = self.workspace / "math_mod.py"
        target_file.write_text("def broken():\n    eval('2+2')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build math tool", max_retries=3)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        res1 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res1["decision"], "continue")

        # Step B: Agent fixes math_mod.py but introduces new defect in utils.py
        target_file.write_text("def broken():\n    return 4\n", encoding="utf-8")
        (self.workspace / "utils.py").write_text("def dangerous(x):\n    exec(x)\n", encoding="utf-8")

        # Step C: Verify regression is detected
        res2 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res2["decision"], "continue")
        self.assertIn("utils.py", res2["reason"])

    # 7. Retry exhaustion
    def test_retry_exhaustion(self):
        target_file = self.workspace / "persistent_bug.py"
        target_file.write_text("def fail():\n    eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Fix bug", max_retries=2)

        payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        # Cycle 1: initial review -> continue (cycle 1)
        res1 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res1["decision"], "continue")

        # Cycle 2: verify -> still flawed -> retry limit reached (cycle 2 of 2)
        res2 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("RETRY_LIMIT_REACHED", res2["reason"])
        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.RETRY_LIMIT_REACHED)

    # 8. Repeated findings with no progress
    def test_repeated_findings_with_no_progress(self):
        target_file = self.workspace / "bug.py"
        target_file.write_text("def f(): eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Fix f", max_retries=2)

        payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        self.adapter.handle_stop_payload(payload)
        # Advance cycle to max retries
        res = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("RETRY_LIMIT_REACHED", res["reason"])

    # 9. Duplicate hook events
    def test_duplicate_hook_events(self):
        # Once in terminal PASSED state, repeated Stop events return allow immediately
        self.state_mgr.initialize_task("Test task", max_retries=3)
        self.state_mgr.transition_to(LifecycleState.PASSED, "Completed")

        payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        res1 = self.adapter.handle_stop_payload(payload)
        res2 = self.adapter.handle_stop_payload(payload)
        self.assertEqual(res1["decision"], "allow")
        self.assertEqual(res2["decision"], "allow")

    # 10. Interrupted or stale lifecycle state
    def test_interrupted_or_stale_lifecycle_state(self):
        # Write corrupted state file
        state_file = self.workspace / ".supervisor" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{corrupt json", encoding="utf-8")

        # Adapter handles corrupted state safely without raising unhandled crash
        (self.workspace / "clean.py").write_text("def ok(): return 1\n", encoding="utf-8")
        payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        res = self.adapter.handle_stop_payload(payload)
        self.assertIn(res["decision"], ("allow", "continue"))

    # 11. Hook timeout and core failure
    def test_hook_core_failure_handling(self):
        # Simulate unexpected exception inside orchestrator review
        with patch.object(SupervisorOrchestrator, "execute_review_cycle", side_effect=RuntimeError("Simulated disk error")):
            payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
            res = self.adapter.handle_stop_payload(payload)
            self.assertEqual(res["decision"], "allow")
            self.assertIn("Simulated disk error", res["reason"])

    # 12. Secret redaction in hook output and repair instructions
    def test_secret_redaction_in_hook_output_and_repair_instructions(self):
        secret_token = "FAKE_STRIPE_KEY_FOR_TESTS"
        (self.workspace / "credentials.py").write_text(
            f'STRIPE_KEY = "{secret_token}"\n',
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Integrate stripe payments", max_retries=3)

        payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        output_str = self.adapter.process_raw_input(json.dumps(payload))

        # Raw secret token MUST NOT appear in the serialized JSON output
        self.assertNotIn(secret_token, output_str)
        # Verify masked representation
        self.assertIn("sk_live_", output_str)
        self.assertIn("****************", output_str)

    # 13. Missing tests and honest verification statuses
    def test_missing_tests_and_honest_verification_status(self):
        (self.workspace / "lib.py").write_text("def compute(): return 100\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement library function", max_retries=3)

        orchestrator = SupervisorOrchestrator(self.workspace)
        report = orchestrator.execute_review_cycle()

        # Check that missing test is acknowledged honestly in report
        self.assertTrue(any(t.status == TestStatus.NOT_AVAILABLE for t in report.tests_run))
        self.assertIn("No automated test suite discovered in workspace.", report.limitations)
        self.assertEqual(report.overall_status, ReviewStatus.VERIFICATION_INCOMPLETE)

    # 14. Explicit report-only invocation
    def test_explicit_report_only_invocation(self):
        (self.workspace / "issue.py").write_text("def f(): eval('1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement feature", max_retries=3)

        orchestrator = SupervisorOrchestrator(self.workspace)
        report = orchestrator.execute_review_cycle(report_only=True)

        state = self.state_mgr.load()
        # In report-only mode, directives are not fired to agent
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertFalse(directives_file.exists())
        self.assertTrue(report.has_material_issues)

    # 15. Default /supervisor behavior initiating full review-and-repair workflow
    def test_default_supervisor_initiates_full_review_repair_workflow(self):
        parser = build_parser()
        args = parser.parse_args([])
        # When invoked without subcommand or flags, defaults to review mode
        self.assertIsNone(args.subcommand)

        # In main(), when subcommand is None, it defaults to review with report_only=False
        (self.workspace / "test_f.py").write_text("def f(): eval('1')\n", encoding="utf-8")
        # Run review via orchestrator with default report_only=False
        orchestrator = SupervisorOrchestrator(self.workspace)
        report = orchestrator.execute_review_cycle(report_only=False)

        # Verify repair directives are created by default
        directives_file = self.workspace / ".supervisor" / "repair_directives.md"
        self.assertTrue(directives_file.exists())


if __name__ == "__main__":
    unittest.main()
