"""Tests for supervisor.context and ContextCollector."""

import tempfile
import unittest
from pathlib import Path

from supervisor.context import ContextCollector


class TestContext(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.collector = ContextCollector(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_requirements_bullets_and_numbered(self):
        prompt = """
Please implement the auth service:
* Must support JWT token generation
* Must validate user passwords against bcrypt
1. Ensure tokens expire after 3600 seconds
2. Implement refresh token rotation
        """
        reqs = self.collector.parse_requirements(prompt)
        self.assertIn("Must support JWT token generation", reqs)
        self.assertIn("Must validate user passwords against bcrypt", reqs)
        self.assertIn("Ensure tokens expire after 3600 seconds", reqs)
        self.assertIn("Implement refresh token rotation", reqs)

    def test_read_workspace_files(self):
        test_file = self.workspace / "service.py"
        test_file.write_text("def run():\n    return True\n", encoding="utf-8")

        files = self.collector.read_workspace_files()
        self.assertIn("service.py", files)
        self.assertEqual(files["service.py"], "def run():\n    return True\n")


if __name__ == "__main__":
    unittest.main()

