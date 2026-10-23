"""Tests for supervisor.inspector and CodeInspector."""

import tempfile
import unittest
from pathlib import Path

from supervisor.inspector import CodeInspector
from supervisor.models import Category, FindingStatus, Severity


class TestInspector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.inspector = CodeInspector(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inspect_syntax_error(self):
        code = "def broken(\n    return 42\n"
        findings = self.inspector.inspect_python_file("broken.py", code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.CRITICAL)
        self.assertEqual(findings[0].category, Category.BUG)
        self.assertIn("SyntaxError", findings[0].description)

    def test_inspect_dangerous_eval(self):
        code = "def calculate(expr):\n    return eval(expr)\n"
        findings = self.inspector.inspect_python_file("calc.py", code)
        eval_findings = [f for f in findings if "eval" in f.description]
        self.assertEqual(len(eval_findings), 1)
        self.assertEqual(eval_findings[0].severity, Severity.CRITICAL)
        self.assertEqual(eval_findings[0].category, Category.SECURITY)

    def test_inspect_silent_except_pass(self):
        code = "try:\n    do_something()\nexcept Exception:\n    pass\n"
        findings = self.inspector.inspect_python_file("handler.py", code)
        err_findings = [f for f in findings if f.category == Category.ERROR_HANDLING]
        self.assertEqual(len(err_findings), 1)
        self.assertIn("Silent exception swallowing", err_findings[0].description)

    def test_inspect_secrets(self):
        code = 'DATABASE_URL = "postgres://..."\nAPI_KEY = "FAKE_STRIPE_KEY_FOR_TESTS"\n'
        findings = self.inspector.inspect_secrets("config.py", code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.CRITICAL)
        self.assertEqual(findings[0].category, Category.SECURITY)

    def test_inspect_json_syntax(self):
        bad_json = '{\n  "name": "project",\n  "version": \n}'
        findings = self.inspector.inspect_json_file("config.json", bad_json)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.HIGH)
        self.assertIn("Invalid JSON", findings[0].description)


if __name__ == "__main__":
    unittest.main()
