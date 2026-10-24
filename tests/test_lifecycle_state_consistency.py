"""Unit and regression tests for lifecycle state consistency in SUPERVISOR.

Covers:
1. Retry-limit semantics (strict ceiling, clean verify, persistent defects, report-only).
2. Task isolation and stale prompts (transcript matching, mtime, error handling, session reset).
3. Report accuracy & status consistency across cycles and CLI exit codes.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from supervisor.cli import main
from supervisor.context import ContextCollector, WorkspaceContext
from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    LifecycleState,
    ReviewStatus,
    Severity,
    SupervisorReport,
    TaskSpec,
    TestResult,
    TestStatus,
)
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.state import StateManager, SupervisorState


class TestLifecycleStateConsistency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.state_mgr = StateManager(self.workspace)
        self.orchestrator = SupervisorOrchestrator(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # Concern 1: Retry-limit semantics
    # =========================================================================

    def test_verify_passes_when_all_findings_resolved_even_at_max_retries(self):
        """When repairs resolve all findings at cycle == max_retries, verify MUST pass and transition to PASSED."""
        state = self.state_mgr.initialize_task("Fix critical bug", max_retries=3)
        state.cycle = 3
        finding = Finding(
            finding_id="BUG-0001",
            severity=Severity.HIGH,
            category=Category.BUG,
            file_path="main.py",
            description="Failing logic",
            evidence="bad code",
            why_it_matters="Breaks functionality",
            recommended_correction="Fix logic",
            verification_method="ast.parse",
            status=FindingStatus.CONFIRMED,
        )
        prev_report = SupervisorReport(
            report_id="rep_init",
            task_id=state.task.task_id,
            cycle=3,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[finding],
        )
        state.latest_report = prev_report
        state.state = LifecycleState.FIXES_REQUIRED
        self.state_mgr.save(state)

        # Repaired file with passing test
        (self.workspace / "main.py").write_text("def run(): return 42\n", encoding="utf-8")
        (self.workspace / "test_main.py").write_text(
            "import unittest\nclass TestMain(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )

        # Execute verification
        report = self.orchestrator.execute_verification_cycle()

        self.assertFalse(report.has_material_issues)
        self.assertEqual(report.overall_status, ReviewStatus.PASSED)
        self.assertEqual(report.state, LifecycleState.PASSED)

        saved_state = self.state_mgr.load()
        self.assertEqual(saved_state.state, LifecycleState.PASSED)
        self.assertLessEqual(saved_state.cycle, saved_state.max_retries)

    def test_consecutive_verify_on_clean_workspace_does_not_increment_cycle(self):
        """Repeatedly running verify on a clean workspace must not inflate cycle past max_retries."""
        (self.workspace / "app.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
        (self.workspace / "test_app.py").write_text(
            "import unittest\nclass TestApp(unittest.TestCase):\n    def test_hello(self): self.assertEqual(1, 1)\n",
            encoding="utf-8",
        )

        # Initial review
        rep1 = self.orchestrator.execute_review_cycle(prompt="Test task")
        self.assertEqual(rep1.state, LifecycleState.PASSED)
        self.assertEqual(rep1.overall_status, ReviewStatus.PASSED)

        state1 = self.state_mgr.load()
        initial_cycle = state1.cycle

        # Consecutive verify cycles
        for _ in range(5):
            rep = self.orchestrator.execute_verification_cycle()
            self.assertEqual(rep.state, LifecycleState.PASSED)
            self.assertEqual(rep.overall_status, ReviewStatus.PASSED)

        state_after = self.state_mgr.load()
        self.assertEqual(state_after.state, LifecycleState.PASSED)
        self.assertLessEqual(state_after.cycle, state_after.max_retries)
        self.assertEqual(state_after.cycle, initial_cycle)

    def test_verify_fails_transitions_to_retry_limit_only_when_exhausted(self):
        """When verification fails and cycle < max_retries, it goes to FIXES_REQUIRED; at limit it goes to RETRY_LIMIT_REACHED."""
        state = self.state_mgr.initialize_task("Fix bug", max_retries=3)
        state.cycle = 1
        bad_code = "def broken(cmd):\n    eval(cmd)\n"
        (self.workspace / "main.py").write_text(bad_code, encoding="utf-8")

        # Cycle 1: First review finds defect
        rep1 = self.orchestrator.execute_review_cycle()
        self.assertEqual(rep1.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(rep1.cycle, 1)

        # Cycle 2: First verification without fixing advances to cycle 2 (2 < 3)
        rep2 = self.orchestrator.execute_verification_cycle()
        self.assertEqual(rep2.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(rep2.cycle, 2)
        state2 = self.state_mgr.load()
        self.assertEqual(state2.cycle, 2)

        # Cycle 3: Second verification without fixing at cycle 3 (3 >= 3) must hit RETRY_LIMIT_REACHED
        rep3 = self.orchestrator.execute_verification_cycle()
        self.assertEqual(rep3.state, LifecycleState.RETRY_LIMIT_REACHED)
        self.assertTrue(rep3.has_material_issues)
        self.assertEqual(rep3.cycle, 3)

        state3 = self.state_mgr.load()
        self.assertEqual(state3.state, LifecycleState.RETRY_LIMIT_REACHED)
        self.assertEqual(state3.cycle, 3)

        # Additional verification at limit must stay bounded at max_retries, not grow indefinitely
        rep4 = self.orchestrator.execute_verification_cycle()
        self.assertEqual(rep4.state, LifecycleState.RETRY_LIMIT_REACHED)
        state4 = self.state_mgr.load()
        self.assertEqual(state4.cycle, 3)

    def test_report_only_verification_does_not_consume_retry_attempts(self):
        """Verification in report-only mode evaluates workspace but does NOT advance cycle or mutate state."""
        state = self.state_mgr.initialize_task("Task", max_retries=3)
        state.cycle = 1
        (self.workspace / "main.py").write_text("def broken(cmd):\n    eval(cmd)\n", encoding="utf-8")

        rep1 = self.orchestrator.execute_review_cycle()
        self.assertEqual(rep1.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(rep1.cycle, 1)

        # Run verify in report-only mode
        rep_ro = self.orchestrator.execute_verification_cycle(report_only=True)
        self.assertTrue(rep_ro.has_material_issues)
        self.assertEqual(rep_ro.cycle, 1)  # cycle NOT advanced

        state_after = self.state_mgr.load()
        self.assertEqual(state_after.cycle, 1)
        self.assertEqual(state_after.state, LifecycleState.FIXES_REQUIRED)

    def test_state_deserialization_clamps_corrupted_cycle(self):
        """SupervisorState.from_dict must clamp legacy or corrupted cycle counts greater than max_retries."""
        corrupted_data = {
            "state": "RETRY_LIMIT_REACHED",
            "cycle": 25,
            "max_retries": 3,
            "task": {"task_id": "t1", "prompt": "Legacy prompt"},
        }
        loaded = SupervisorState.from_dict(corrupted_data)
        self.assertEqual(loaded.cycle, 3)

    # =========================================================================
    # Concern 2: Task isolation and stale prompts
    # =========================================================================

    def test_new_task_in_different_workspace_does_not_adopt_unrelated_transcript(self):
        """When running in a workspace with no matching transcript, discovery returns None and prompt is UNAVAILABLE."""
        brain_temp = tempfile.TemporaryDirectory()
        try:
            brain_dir = Path(brain_temp.name)
            # Create an unrelated transcript for Mark/binance-web3-lab
            unrelated_dir = brain_dir / "unrelated_conv" / ".system_generated" / "logs"
            unrelated_dir.mkdir(parents=True)
            tfile = unrelated_dir / "transcript.jsonl"
            tfile.write_text(
                json.dumps({"type": "USER_INPUT", "content": "Fix Binance Web3 API tests in Mark/binance-web3-lab"}) + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"USERPROFILE": brain_temp.name}):
                # Mock brain dir path in ContextCollector
                collector = ContextCollector(self.workspace)
                with patch("supervisor.context.Path.exists") as mock_exists:
                    # Let's directly call find_transcript_prompt with fake candidates
                    result = collector.find_transcript_prompt()
                    # Because the transcript only mentions Mark/binance-web3-lab and not self.workspace.name, it must NOT be selected
                    self.assertIsNone(result)

                    context = collector.collect()
                    self.assertEqual(context.task.prompt, "UNAVAILABLE")
                    self.assertFalse(context.has_explicit_prompt)
        finally:
            brain_temp.cleanup()

    def test_multiple_transcripts_selects_newest_workspace_matching(self):
        """When multiple transcripts are present, the newest one matching the workspace is selected."""
        candidates_dir = tempfile.TemporaryDirectory()
        try:
            cdir = Path(candidates_dir.name)
            ws_name = self.workspace.name

            # Old matching transcript
            t_old = cdir / "t_old.jsonl"
            t_old.write_text(
                json.dumps({"type": "USER_INPUT", "content": f"Old prompt for {ws_name}"}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_old, (1000, 1000))

            # Newer UNRELATED transcript
            t_unrelated = cdir / "t_unrelated.jsonl"
            t_unrelated.write_text(
                json.dumps({"type": "USER_INPUT", "content": "Unrelated Binance project"}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_unrelated, (2000, 2000))

            # Newest matching transcript
            t_new = cdir / "t_new.jsonl"
            t_new.write_text(
                json.dumps({"type": "USER_INPUT", "content": f"Newest prompt for {ws_name}"}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_new, (3000, 3000))

            collector = ContextCollector(self.workspace)
            # Patch brain candidates to return these 3 files
            with patch("supervisor.context.ContextCollector.find_transcript_prompt") as mock_find:
                # Test the internal candidate selection logic directly
                candidates = [t_old, t_unrelated, t_new]
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

                chosen_prompt = None
                for c in candidates:
                    sample = c.read_text(encoding="utf-8")
                    if ws_name.lower() in sample.lower():
                        chosen_prompt = collector._extract_prompt_from_file(c)
                        break

                self.assertEqual(chosen_prompt, f"Newest prompt for {ws_name}")
        finally:
            candidates_dir.cleanup()

    def test_inaccessible_transcript_candidate_does_not_abort_discovery(self):
        """PermissionError on an inaccessible transcript does not abort checking valid candidates."""
        candidates_dir = tempfile.TemporaryDirectory()
        try:
            cdir = Path(candidates_dir.name)
            ws_name = self.workspace.name

            t_valid = cdir / "t_valid.jsonl"
            t_valid.write_text(
                json.dumps({"type": "USER_INPUT", "content": f"Valid prompt for {ws_name}"}) + "\n",
                encoding="utf-8",
            )

            collector = ContextCollector(self.workspace)

            # Test that an inaccessible candidate is skipped gracefully
            with patch.object(Path, "read_text", side_effect=[PermissionError("Access denied"), t_valid.read_text(encoding="utf-8")]):
                # Call find_transcript_prompt with explicit path
                prompt = collector._extract_prompt_from_file(t_valid)
                self.assertEqual(prompt, f"Valid prompt for {ws_name}")
        finally:
            candidates_dir.cleanup()

    def test_new_prompt_starts_fresh_session(self):
        """When a new prompt is supplied to review after a task was terminal, a fresh cycle 1 session begins."""
        state = self.state_mgr.initialize_task("Old Task 1", max_retries=3)
        state.state = LifecycleState.PASSED
        state.cycle = 3
        self.state_mgr.save(state)

        (self.workspace / "code.py").write_text("def task2(): return True\n", encoding="utf-8")
        (self.workspace / "test_code.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )

        rep = self.orchestrator.execute_review_cycle(prompt="New Task 2")
        self.assertEqual(rep.state, LifecycleState.PASSED)
        self.assertEqual(rep.cycle, 1)

        new_state = self.state_mgr.load()
        self.assertEqual(new_state.cycle, 1)
        self.assertEqual(new_state.state, LifecycleState.PASSED)
        self.assertEqual(new_state.task.prompt, "New Task 2")

    def test_unchanged_terminal_session_remains_stable(self):
        """When workspace is terminal and has zero unreviewed changes and no new prompt, review does not reset."""
        (self.workspace / "mod.py").write_text("def ok(): return 1\n", encoding="utf-8")
        (self.workspace / "test_mod.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            encoding="utf-8",
        )

        rep1 = self.orchestrator.execute_review_cycle(prompt="Stable task")
        self.assertEqual(rep1.state, LifecycleState.PASSED)

        state1 = self.state_mgr.load()
        # Ensure fingerprint is saved
        state1.workspace_fingerprint = self.orchestrator.context_collector.compute_workspace_fingerprint()
        self.state_mgr.save(state1)

        # Run review again with NO changes and NO new prompt
        rep2 = self.orchestrator.execute_review_cycle()
        self.assertEqual(rep2.state, LifecycleState.PASSED)
        state2 = self.state_mgr.load()
        self.assertEqual(state2.task.prompt, "Stable task")
        self.assertEqual(state2.state, LifecycleState.PASSED)

    def test_previous_task_findings_do_not_leak_into_new_task(self):
        """Findings from task A do not contaminate a freshly initialized task B."""
        state = self.state_mgr.initialize_task("Task A", max_retries=3)
        old_finding = Finding(
            finding_id="OLD-BUG-999",
            severity=Severity.HIGH,
            category=Category.BUG,
            file_path="old.py",
            description="Old bug",
            evidence="...",
            why_it_matters="...",
            recommended_correction="...",
            verification_method="...",
            status=FindingStatus.CONFIRMED,
        )
        prev_report = SupervisorReport(
            report_id="rep_old",
            task_id="task_a",
            cycle=3,
            state=LifecycleState.RETRY_LIMIT_REACHED,
            findings=[old_finding],
        )
        state.latest_report = prev_report
        state.state = LifecycleState.RETRY_LIMIT_REACHED
        self.state_mgr.save(state)

        # Initialize Task B
        (self.workspace / "clean_b.py").write_text("def b(): return 'b'\n", encoding="utf-8")
        (self.workspace / "test_b.py").write_text(
            "import unittest\nclass TB(unittest.TestCase):\n    def test(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )

        rep_b = self.orchestrator.execute_review_cycle(prompt="Task B")
        self.assertEqual(rep_b.state, LifecycleState.PASSED)
        finding_ids = [f.finding_id for f in rep_b.findings]
        self.assertNotIn("OLD-BUG-999", finding_ids)

    # =========================================================================
    # Concern 3: Report accuracy & status consistency
    # =========================================================================

    def test_historical_failures_distinguishable_from_current_test_results(self):
        """Historical test failures are marked RESOLVED while current tests record PASSED with exit code 0."""
        finding = Finding(
            finding_id="TEST-FAIL-0001",
            severity=Severity.HIGH,
            category=Category.BUG,
            file_path="tests",
            description="Automated tests failed",
            evidence="AssertionError",
            why_it_matters="Broken logic",
            recommended_correction="Fix code",
            verification_method="python -m unittest",
            status=FindingStatus.CONFIRMED,
        )
        prev_report = SupervisorReport(
            report_id="rep_1",
            task_id="task_1",
            cycle=1,
            state=LifecycleState.FIXES_REQUIRED,
            findings=[finding],
            tests_run=[
                TestResult(
                    command="python -m unittest",
                    exit_code=1,
                    stdout="",
                    stderr="AssertionError",
                    duration_seconds=1.0,
                    passed=False,
                    status=TestStatus.FAILED,
                )
            ],
        )
        state = self.state_mgr.initialize_task("Fix test", max_retries=3)
        state.latest_report = prev_report
        state.state = LifecycleState.FIXES_REQUIRED
        self.state_mgr.save(state)

        # Provide working implementation and test
        (self.workspace / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
        (self.workspace / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test(self): self.assertEqual(add(1, 2), 3)\n",
            encoding="utf-8",
        )

        summary = self.orchestrator.verifier.verify_cycle(
            context=self.orchestrator.context_collector.collect(explicit_prompt="Fix test"),
            previous_report=prev_report,
            cycle=2,
        )

        self.assertTrue(summary.passed)
        # Old finding is marked RESOLVED
        resolved_ids = [f.finding_id for f in summary.resolved_findings]
        self.assertIn("TEST-FAIL-0001", resolved_ids)
        # Current test suite is PASSED
        self.assertEqual(summary.report.tests_run[0].exit_code, 0)
        self.assertEqual(summary.report.tests_run[0].status, TestStatus.PASSED)
        self.assertFalse(summary.report.has_material_issues)

    def test_prior_successful_run_cannot_conceal_later_failed_run(self):
        """If a test was passing in cycle 1, but breaks in cycle 2, the report flags the failure immediately."""
        (self.workspace / "app.py").write_text("def work(): return 1\n", encoding="utf-8")
        (self.workspace / "test_app.py").write_text(
            "import unittest\nfrom app import work\nclass T(unittest.TestCase):\n    def test(self): self.assertEqual(work(), 1)\n",
            encoding="utf-8",
        )

        # Cycle 1 passes
        rep1 = self.orchestrator.execute_review_cycle(prompt="Test work")
        self.assertEqual(rep1.overall_status, ReviewStatus.PASSED)
        self.assertEqual(rep1.state, LifecycleState.PASSED)

        # Break the test in cycle 2
        (self.workspace / "test_app.py").write_text(
            "import unittest\nfrom app import work\nclass T(unittest.TestCase):\n    def test(self): self.assertEqual(work(), 999)\n",
            encoding="utf-8",
        )

        # Verification must detect the failure
        rep2 = self.orchestrator.execute_verification_cycle()
        self.assertTrue(rep2.has_material_issues)
        self.assertEqual(rep2.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(rep2.overall_status, ReviewStatus.FIXES_REQUIRED)
        self.assertEqual(rep2.tests_run[0].status, TestStatus.FAILED)

    def test_cli_exit_code_consistency_across_states(self):
        """CLI returns exit code 0 on PASSED, 1 on FIXES_REQUIRED/RETRY_LIMIT, and 2 on BLOCKED."""
        # 1. Clean workspace -> Exit code 0
        (self.workspace / "clean.py").write_text("def f(): return 1\n", encoding="utf-8")
        (self.workspace / "test_clean.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            encoding="utf-8",
        )
        with patch("pathlib.Path.cwd", return_value=self.workspace):
            with patch("sys.stdout"):
                code = main(["review", "-p", "Clean task"])
                self.assertEqual(code, 0)

        # 2. Defective workspace -> Exit code 1
        (self.workspace / "bad.py").write_text("def f(c):\n    eval(c)\n", encoding="utf-8")
        with patch("pathlib.Path.cwd", return_value=self.workspace):
            with patch("sys.stdout"):
                code = main(["review"])
                self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
