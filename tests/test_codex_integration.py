"""Comprehensive integration tests for OpenAI Codex integration.

Covers the 13 required categories specified in Prompt 7:
1. Codex input and event parsing
2. Shared-core invocation
3. Clean review results
4. Findings and repair handoff (structured JSON and exit-code-2 modalities)
5. Re-review after repair where supported
6. Retry exhaustion
7. Duplicate events and recursion prevention
8. Missing task context and missing test infrastructure
9. Secret redaction
10. Malformed payloads and subprocess failures
11. Honest status handling when automatic continuation is unsupported / harness mode
12. Coexistence with the Antigravity integration
13. Preservation of existing shared-core behavior
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor.adapters.antigravity import AntigravityAdapter
from supervisor.adapters.codex import CodexAdapter
from supervisor.models import LifecycleState, Severity
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.state import StateManager


class TestCodexIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.adapter = CodexAdapter(self.workspace)
        self.state_mgr = StateManager(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Codex input and event parsing
    def test_codex_input_and_event_parsing(self):
        # Empty string
        code, out, err = self.adapter.process_raw_input("")
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("skipped", res["reason"].lower())

        # Malformed JSON
        code2, out2, err2 = self.adapter.process_raw_input("{unclosed_json: true,")
        self.assertEqual(code2, 0)
        res2 = json.loads(out2)
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("parse error", res2["reason"].lower())

        # Non-dict JSON
        code3, out3, err3 = self.adapter.process_raw_input("[1, 2, 3]")
        self.assertEqual(code3, 0)
        res3 = json.loads(out3)
        self.assertEqual(res3["decision"], "allow")

        # Non-Stop Codex event (e.g., SessionStart, UserPromptSubmit)
        non_stop = {
            "hook_event_name": "SessionStart",
            "cwd": str(self.workspace),
        }
        code4, out4, err4 = self.adapter.process_raw_input(json.dumps(non_stop))
        self.assertEqual(code4, 0)
        res4 = json.loads(out4)
        self.assertEqual(res4["decision"], "allow")
        self.assertIn("skipped", res4["reason"])

    # 2. Shared-core invocation
    def test_shared_core_invocation(self):
        (self.workspace / "service.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Implemented ping service",
        }
        code, res, err = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        self.assertIn(res["decision"], ("allow", "block"))
        # Verify state file was created and managed by shared core
        state = self.state_mgr.load()
        self.assertIsNotNone(state.latest_report)

    # 3. Clean review results
    def test_clean_review_results(self):
        (self.workspace / "service.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
        (self.workspace / "test_service.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n  def test_p(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.state_mgr.initialize_task(prompt="Create clean ping service", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Created clean service and unit test",
        }
        code, res, err = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("PASSED", res["reason"])

        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.PASSED)

    # 4. Findings and repair handoff (Structured JSON and Exit-Code-2 modalities)
    def test_findings_and_repair_handoff_modalities(self):
        (self.workspace / "vuln.py").write_text("def run(cmd):\n    eval(cmd)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement command runner", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Done implementing command runner",
        }

        # Modality A: Structured JSON on stdout (exit code 0)
        code, res, err = self.adapter.handle_stop_payload(payload, exit_code_on_block=False)
        self.assertEqual(code, 0)
        self.assertEqual(res["decision"], "block")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", res["reason"])
        self.assertIn("vuln.py", res["reason"])
        self.assertEqual(err, "")

        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.REPAIRING)

        # Modality B: Imperative exit code 2 with directives on stderr
        # Reset to IMPLEMENTING to simulate first pass under exit code 2
        self.state_mgr.transition_to(LifecycleState.IMPLEMENTING, "Reset")
        code2, res2, err2 = self.adapter.handle_stop_payload(payload, exit_code_on_block=True)
        self.assertEqual(code2, 2)
        self.assertEqual(res2, {})
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", err2)
        self.assertIn("vuln.py", err2)

    # 5. Re-review after repair where supported
    def test_rereview_after_repair(self):
        # Step A: Defect detected -> blocked
        target_file = self.workspace / "calc.py"
        target_file.write_text("def compute(x):\n    eval(x)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build safe calculator", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Added calculator with eval",
        }
        code1, res1, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code1, 0)
        self.assertEqual(res1["decision"], "block")
        self.assertEqual(self.state_mgr.load().state, LifecycleState.REPAIRING)

        # Step B: Repair applied + test added -> allowed (PASSED)
        target_file.write_text("def compute(a, b):\n    return a + b\n", encoding="utf-8")
        (self.workspace / "test_calc.py").write_text(
            "import unittest\nfrom calc import compute\nclass T(unittest.TestCase):\n  def test_add(self): self.assertEqual(compute(1, 2), 3)\n",
            encoding="utf-8",
        )
        code2, res2, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code2, 0)
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("PASSED", res2["reason"])
        self.assertEqual(self.state_mgr.load().state, LifecycleState.PASSED)

    # 6. Retry exhaustion
    def test_retry_exhaustion(self):
        (self.workspace / "broken.py").write_text("def run():\n    eval('1+1')\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Task", max_retries=1)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("RETRY_LIMIT_REACHED", res["reason"])

        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.RETRY_LIMIT_REACHED)

    # 7. Duplicate events and recursion prevention
    def test_duplicate_events_and_recursion_prevention(self):
        self.state_mgr.initialize_task("Task", max_retries=3)
        self.state_mgr.transition_to(LifecycleState.REVIEWING, "Simulated active review")

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("Review already in progress", res["reason"])

        # Stale REVIEWING lock recovery (> 30s)
        state_file = self.workspace / ".supervisor" / "state.json"
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["updated_at"] = "2020-01-01T00:00:00+00:00"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        code2, res2, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code2, 0)
        # Verify it recovered rather than being permanently blocked
        self.assertNotIn("Review already in progress", res2.get("reason", ""))

    # 8. Missing task context and missing test infrastructure
    def test_missing_task_context_and_missing_test_infrastructure(self):
        from supervisor.context import ContextCollector
        # Empty workspace with no prompt and no tests
        (self.workspace / "module.py").write_text("def func(): return 123\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            # No last_assistant_message and no transcript_path
        }
        with patch.object(ContextCollector, "find_transcript_prompt", return_value=None):
            code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        # Should flag missing test suite (TEST-MISSING-0001) honestly in findings
        state = self.state_mgr.load()
        self.assertIsNotNone(state.latest_report)
        findings = state.latest_report.findings
        self.assertTrue(any(f.finding_id == "TEST-MISSING-0001" for f in findings))
        self.assertTrue(any("prompt unavailable" in lim.lower() for lim in state.latest_report.limitations))

    # 9. Secret redaction
    def test_secret_redaction(self):
        secret_content = 'API_TOKEN = "FAKE_STRIPE_KEY_FOR_TESTS"\n'
        (self.workspace / "creds.py").write_text(secret_content, encoding="utf-8")
        self.state_mgr.initialize_task("Store credentials", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        raw_in = json.dumps(payload)

        # Test structured mode
        _, stdout_str, _ = self.adapter.process_raw_input(raw_in, exit_code_on_block=False)
        self.assertNotIn("FAKE_STRIPE_KEY_FOR_TESTS", stdout_str)
        self.assertIn("sk_live_****", stdout_str)

        # Test imperative mode
        _, _, stderr_str = self.adapter.process_raw_input(raw_in, exit_code_on_block=True)
        self.assertNotIn("FAKE_STRIPE_KEY_FOR_TESTS", stderr_str)
        self.assertIn("sk_live_****", stderr_str)

    # 10. Malformed payloads and subprocess failures
    def test_malformed_payloads_and_subprocess_failures(self):
        # Subprocess or core crash handling
        with patch.object(SupervisorOrchestrator, "execute_review_cycle", side_effect=RuntimeError("Disk I/O error")):
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(self.workspace),
            }
            code, res, _ = self.adapter.handle_stop_payload(payload)
            self.assertEqual(code, 0)
            self.assertEqual(res["decision"], "allow")
            self.assertIn("BLOCKED", res["reason"])
            # State must record BLOCKED, never PASSED
            state = self.state_mgr.load()
            self.assertEqual(state.state, LifecycleState.BLOCKED)

    # 11. Honest status handling when automatic continuation is unsupported / harness mode
    def test_honest_status_handling_and_harness_mode(self):
        # Test programmatic harness mode
        (self.workspace / "math_app.py").write_text("def calc(x):\n    eval(x)\n", encoding="utf-8")

        # Cycle 1: Defect detected -> should_continue = True
        harness_res = self.adapter.evaluate_harness_turn(
            agent_message="Completed math app",
            original_prompt="Build safe math evaluator",
        )
        self.assertTrue(harness_res["should_continue"])
        self.assertIsNotNone(harness_res["directives"])
        self.assertEqual(harness_res["state"], LifecycleState.REPAIRING.value)
        self.assertTrue(len(harness_res["material_findings"]) > 0)

        # Simulate repair
        (self.workspace / "math_app.py").write_text("def calc(a, b):\n    return a * b\n", encoding="utf-8")
        (self.workspace / "test_math.py").write_text(
            "import unittest\nfrom math_app import calc\nclass T(unittest.TestCase):\n  def test_m(self): self.assertEqual(calc(2, 3), 6)\n",
            encoding="utf-8",
        )

        # Cycle 2: Verified -> should_continue = False
        harness_res2 = self.adapter.evaluate_harness_turn(agent_message="Fixed defect")
        self.assertFalse(harness_res2["should_continue"])
        self.assertIsNone(harness_res2["directives"])
        self.assertEqual(harness_res2["state"], LifecycleState.PASSED.value)
        self.assertEqual(len(harness_res2["material_findings"]), 0)

    # 12. Coexistence with the Antigravity integration
    def test_coexistence_with_antigravity_integration(self):
        ag_adapter = AntigravityAdapter(self.workspace)
        codex_adapter = CodexAdapter(self.workspace)

        (self.workspace / "shared.py").write_text("def compute(): return 42\n", encoding="utf-8")
        (self.workspace / "test_shared.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n  def test_c(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )

        # 1. Antigravity invocation
        ag_payload = {
            "terminationReason": "model_stop",
            "workspacePaths": [str(self.workspace)],
        }
        ag_res = ag_adapter.handle_stop_payload(ag_payload)
        self.assertEqual(ag_res["decision"], "allow")
        self.assertIn("PASSED", ag_res["reason"])

        # 2. Codex invocation on same workspace
        codex_payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, codex_res, _ = codex_adapter.handle_stop_payload(codex_payload)
        self.assertEqual(code, 0)
        self.assertEqual(codex_res["decision"], "allow")

    # 13. Preservation of existing shared-core behavior
    def test_preservation_of_existing_shared_core_behavior(self):
        # Confirm that orchestrator and state manager operate with exact same models
        mgr = StateManager(self.workspace)
        state = mgr.initialize_task("Preserve core behavior", max_retries=3)
        self.assertEqual(state.state, LifecycleState.IMPLEMENTING)
        self.assertEqual(state.cycle, 1)

        orchestrator = SupervisorOrchestrator(self.workspace)
        (self.workspace / "core_test.py").write_text("val = 1\n", encoding="utf-8")
        report = orchestrator.execute_review_cycle(prompt="Test core")
        self.assertEqual(report.cycle, 1)
        self.assertIsInstance(report.material_findings, list)


if __name__ == "__main__":
    unittest.main()
