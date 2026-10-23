"""Comprehensive integration tests for GitLab Duo CLI integration.

Covers the 16 required categories specified in Prompt 9:
1. GitLab-specific input parsing
2. Shared-core invocation
3. Clean review results
4. Material findings and repair handoff (Imperative exit-code-2 and Structured JSON modalities)
5. Re-review after repair, where supported
6. Retry exhaustion
7. Duplicate events and recursion prevention
8. Missing task context
9. Missing tests and honest verification statuses
10. Secret redaction
11. Malformed input and subprocess failures
12. Timeout and interrupted-state recovery
13. Explicit report-only behavior
14. Default review-and-repair behavior
15. Coexistence with the other three adapters (Antigravity, Codex, Claude Code)
16. Preservation of existing core behavior
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor.adapters.antigravity import AntigravityAdapter
from supervisor.adapters.claude_code import ClaudeCodeAdapter
from supervisor.adapters.codex import CodexAdapter
from supervisor.adapters.gitlab_duo import GitLabDuoAdapter
from supervisor.context import ContextCollector
from supervisor.models import LifecycleState, Severity
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.state import StateManager


class TestGitLabDuoIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.adapter = GitLabDuoAdapter(self.workspace)
        self.state_mgr = StateManager(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. GitLab-specific input parsing
    def test_gitlab_specific_input_parsing(self):
        # Empty string
        code, out, err = self.adapter.process_raw_input("")
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("skipped", res["reason"].lower())

        # Malformed JSON
        code2, out2, err2 = self.adapter.process_raw_input("{invalid_json: 123,")
        self.assertEqual(code2, 0)
        res2 = json.loads(out2)
        self.assertEqual(res2["decision"], "allow")
        self.assertIn("parse error", res2["reason"].lower())

        # Non-dict JSON
        code3, out3, err3 = self.adapter.process_raw_input("[1, 2, 3]")
        self.assertEqual(code3, 0)
        res3 = json.loads(out3)
        self.assertEqual(res3["decision"], "allow")

        # Non-Stop event (e.g., PreToolUse, UserPromptSubmit, SessionStart)
        non_stop = {
            "hook_event_name": "PreToolUse",
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
        code, res, err = self.adapter.handle_stop_payload(payload, exit_code_on_block=False)
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

    # 4. Material findings and repair handoff (Imperative exit-code-2 and Structured JSON modalities)
    def test_material_findings_and_repair_handoff(self):
        (self.workspace / "vuln.py").write_text("def run(cmd):\n    eval(cmd)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Implement command runner", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Done implementing command runner",
        }

        # Modality A: Imperative exit code 2 with directives on stderr (default in GitLab Duo)
        code1, res1, err1 = self.adapter.handle_stop_payload(payload, exit_code_on_block=True)
        self.assertEqual(code1, 2)
        self.assertEqual(res1, {})
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", err1)
        self.assertIn("vuln.py", err1)

        state = self.state_mgr.load()
        self.assertEqual(state.state, LifecycleState.REPAIRING)

        # Modality B: Structured JSON on stdout (exit code 0)
        self.state_mgr.transition_to(LifecycleState.IMPLEMENTING, "Reset")
        code2, res2, err2 = self.adapter.handle_stop_payload(payload, exit_code_on_block=False)
        self.assertEqual(code2, 0)
        self.assertEqual(res2["decision"], "block")
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", res2["reason"])
        self.assertIn("vuln.py", res2["reason"])
        self.assertEqual(err2, "")

    # 5. Re-review after repair, where supported
    def test_rereview_after_repair(self):
        # Step A: Defect detected -> blocked with exit code 2
        target_file = self.workspace / "calc.py"
        target_file.write_text("def compute(x):\n    eval(x)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build safe calculator", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
            "last_assistant_message": "Added calculator with eval",
        }
        code1, _, err1 = self.adapter.handle_stop_payload(payload, exit_code_on_block=True)
        self.assertEqual(code1, 2)
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", err1)
        self.assertEqual(self.state_mgr.load().state, LifecycleState.REPAIRING)

        # Step B: Repair applied + test added -> allowed (exit code 0, PASSED)
        target_file.write_text("def compute(a, b):\n    return a + b\n", encoding="utf-8")
        (self.workspace / "test_calc.py").write_text(
            "import unittest\nfrom calc import compute\nclass T(unittest.TestCase):\n  def test_add(self): self.assertEqual(compute(1, 2), 3)\n",
            encoding="utf-8",
        )
        code2, res2, _ = self.adapter.handle_stop_payload(payload, exit_code_on_block=True)
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

    # 8. Missing task context
    def test_missing_task_context(self):
        (self.workspace / "module.py").write_text("def func(): return 123\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        with patch.object(ContextCollector, "find_transcript_prompt", return_value=None):
            code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        state = self.state_mgr.load()
        self.assertIsNotNone(state.latest_report)
        self.assertTrue(any("prompt unavailable" in lim.lower() for lim in state.latest_report.limitations))

    # 9. Missing tests and honest verification statuses
    def test_missing_tests_and_honest_status(self):
        (self.workspace / "module.py").write_text("def func(): return 123\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        state = self.state_mgr.load()
        self.assertIsNotNone(state.latest_report)
        findings = state.latest_report.findings
        self.assertTrue(any(f.finding_id == "TEST-MISSING-0001" for f in findings))

    # 10. Secret redaction
    def test_secret_redaction(self):
        secret_content = 'API_TOKEN = "FAKE_STRIPE_KEY_FOR_TESTS"\n'
        (self.workspace / "creds.py").write_text(secret_content, encoding="utf-8")
        self.state_mgr.initialize_task("Store credentials", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        raw_in = json.dumps(payload)

        # Imperative mode (exit code 2 with stderr)
        code, stdout_str, stderr_str = self.adapter.process_raw_input(raw_in, exit_code_on_block=True)
        self.assertEqual(code, 2)
        self.assertNotIn("FAKE_STRIPE_KEY_FOR_TESTS", stderr_str)
        self.assertIn("sk_live_****", stderr_str)

        # Structured JSON mode (exit code 0 with stdout)
        code2, stdout_str2, stderr_str2 = self.adapter.process_raw_input(raw_in, exit_code_on_block=False)
        self.assertEqual(code2, 0)
        self.assertNotIn("FAKE_STRIPE_KEY_FOR_TESTS", stdout_str2)
        self.assertIn("sk_live_****", stdout_str2)

    # 11. Malformed input and subprocess failures
    def test_malformed_input_and_subprocess_failures(self):
        with patch.object(SupervisorOrchestrator, "execute_review_cycle", side_effect=RuntimeError("Subprocess crash")):
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(self.workspace),
            }
            code, res, _ = self.adapter.handle_stop_payload(payload)
            self.assertEqual(code, 0)
            self.assertEqual(res["decision"], "allow")
            self.assertIn("BLOCKED", res["reason"])
            state = self.state_mgr.load()
            self.assertEqual(state.state, LifecycleState.BLOCKED)

    # 12. Timeout and interrupted-state recovery
    def test_timeout_and_interrupted_state_recovery(self):
        self.state_mgr.initialize_task("Task", max_retries=3)
        self.state_mgr.transition_to(LifecycleState.REVIEWING, "Simulated active review")

        state_file = self.workspace / ".supervisor" / "state.json"
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["updated_at"] = "2020-01-01T00:00:00+00:00"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, res, _ = self.adapter.handle_stop_payload(payload)
        self.assertEqual(code, 0)
        # Verify it safely recovered from stale REVIEWING lock
        self.assertNotIn("Review already in progress", res.get("reason", ""))

    # 13. Explicit report-only behavior
    def test_explicit_report_only_behavior(self):
        (self.workspace / "calc.py").write_text("def compute(x):\n    eval(x)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build calculator", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        code, res, _ = self.adapter.handle_stop_payload(payload, report_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(res["decision"], "allow")
        self.assertIn("report-only", res["reason"].lower())

        state = self.state_mgr.load()
        self.assertNotEqual(state.state, LifecycleState.REPAIRING)

    # 14. Default review-and-repair behavior
    def test_default_review_and_repair_behavior(self):
        (self.workspace / "calc.py").write_text("def compute(x):\n    eval(x)\n", encoding="utf-8")
        self.state_mgr.initialize_task(prompt="Build calculator", max_retries=3)

        payload = {
            "hook_event_name": "Stop",
            "cwd": str(self.workspace),
        }
        # In GitLab Duo default mode (exit_code_on_block=True), defects yield exit code 2 with directives
        code, res, err = self.adapter.handle_stop_payload(payload, exit_code_on_block=True, report_only=False)
        self.assertEqual(code, 2)
        self.assertIn("[SUPERVISOR AUTO-REPAIR DIRECTIVE]", err)
        self.assertEqual(self.state_mgr.load().state, LifecycleState.REPAIRING)

    # 15. Coexistence with the other three adapters
    def test_coexistence_with_other_three_adapters(self):
        ag_adapter = AntigravityAdapter(self.workspace)
        codex_adapter = CodexAdapter(self.workspace)
        claude_adapter = ClaudeCodeAdapter(self.workspace)
        gitlab_adapter = GitLabDuoAdapter(self.workspace)

        (self.workspace / "shared.py").write_text("def ping(): return 'pong'\n", encoding="utf-8")
        (self.workspace / "test_shared.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n  def test_p(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )

        # 1. Antigravity
        ag_payload = {"terminationReason": "model_stop", "workspacePaths": [str(self.workspace)]}
        ag_res = ag_adapter.handle_stop_payload(ag_payload)
        self.assertEqual(ag_res["decision"], "allow")

        # 2. Codex
        codex_payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        _, codex_res, _ = codex_adapter.handle_stop_payload(codex_payload)
        self.assertEqual(codex_res["decision"], "allow")

        # 3. Claude Code
        claude_payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        _, claude_res, _ = claude_adapter.handle_stop_payload(claude_payload)
        self.assertEqual(claude_res["decision"], "allow")

        # 4. GitLab Duo
        gitlab_payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        code_gd, gitlab_res, _ = gitlab_adapter.handle_stop_payload(gitlab_payload)
        self.assertEqual(code_gd, 0)
        self.assertEqual(gitlab_res["decision"], "allow")

    # 16. Preservation of existing core behavior
    def test_preservation_of_existing_core_behavior(self):
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
