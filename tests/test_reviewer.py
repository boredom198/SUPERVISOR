"""Tests for supervisor.reviewer and ReviewEngine."""

import tempfile
import unittest
from pathlib import Path

from supervisor.context import WorkspaceContext
from supervisor.models import Category, FindingStatus, Severity, TaskSpec
from supervisor.reviewer import ReviewEngine


class TestReviewer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.reviewer = ReviewEngine(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_review_identifies_defects(self):
        bad_code = "import os\ndef run(cmd):\n    eval(cmd)\n"
        context = WorkspaceContext(
            task=TaskSpec(task_id="t1", prompt="Build runner"),
            workspace_root=self.workspace,
            file_contents={"runner.py": bad_code},
        )
        report = self.reviewer.review(context, cycle=1)
        self.assertTrue(report.has_material_issues)
        self.assertTrue(any(f.category == Category.SECURITY for f in report.findings))

    def test_review_clean_code(self):
        good_code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        test_code = "import unittest\nfrom math_lib import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n"
        context = WorkspaceContext(
            task=TaskSpec(task_id="t2", prompt="Build add function"),
            workspace_root=self.workspace,
            file_contents={"math_lib.py": good_code, "test_math.py": test_code},
        )
        report = self.reviewer.review(context, cycle=1)
        self.assertFalse(report.has_material_issues)


if __name__ == "__main__":
    unittest.main()

