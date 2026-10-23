"""Tests for supervisor.state and StateManager."""

import tempfile
import unittest
from pathlib import Path

from supervisor.models import LifecycleState, SupervisorReport
from supervisor.state import StateManager, SupervisorState


class TestState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.mgr = StateManager(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initialize_task(self):
        state = self.mgr.initialize_task(
            prompt="Build a calculator module",
            task_id="calc_task",
            requirements=["Must support add and subtract"],
            max_retries=3,
        )
        self.assertEqual(state.state, LifecycleState.IMPLEMENTING)
        self.assertEqual(state.cycle, 1)
        self.assertEqual(state.task.task_id, "calc_task")

        # Reload from disk
        loaded = self.mgr.load()
        self.assertEqual(loaded.task.task_id, "calc_task")
        self.assertEqual(loaded.cycle, 1)

    def test_transitions(self):
        self.mgr.initialize_task("Test task")
        s1 = self.mgr.transition_to(LifecycleState.REVIEWING, "Testing review transition")
        self.assertEqual(s1.state, LifecycleState.REVIEWING)
        self.assertEqual(len(s1.history), 1)

        s2 = self.mgr.transition_to(LifecycleState.FIXES_REQUIRED, "Defects found")
        self.assertEqual(s2.state, LifecycleState.FIXES_REQUIRED)
        self.assertEqual(len(s2.history), 2)

    def test_retry_limit_advancement(self):
        self.mgr.initialize_task("Retry task", max_retries=2)
        # Cycle 1 -> 2
        s1 = self.mgr.advance_cycle_or_limit()
        self.assertEqual(s1.cycle, 2)
        self.assertEqual(s1.state, LifecycleState.REPAIRING)

        # Cycle 2 -> limit reached
        s2 = self.mgr.advance_cycle_or_limit()
        self.assertEqual(s2.cycle, 2)
        self.assertEqual(s2.state, LifecycleState.RETRY_LIMIT_REACHED)


if __name__ == "__main__":
    unittest.main()
