"""Tests for supervisor.security and SecurityInspector."""

import tempfile
import unittest
from pathlib import Path

from supervisor.models import Category, Confidence, Severity
from supervisor.security import SecurityInspector


class TestSecurityInspector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.inspector = SecurityInspector(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_private_key(self):
        key_content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0m...\n-----END RSA PRIVATE KEY-----\n"
        findings = self.inspector.scan_content("secrets.pem", key_content)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.CRITICAL)
        self.assertEqual(findings[0].category, Category.SECURITY)
        self.assertEqual(findings[0].confidence, Confidence.CONFIRMED)
        self.assertIn("[REDACTED]", findings[0].evidence)

    def test_detect_known_cloud_tokens(self):
        content = 'GITHUB_TOKEN = "ghp_1234567890abcdef1234567890abcdef"\n'
        findings = self.inspector.scan_content("ci.py", content)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.CRITICAL)
        self.assertIn("ghp_", findings[0].evidence)
        self.assertIn("****************", findings[0].evidence)

    def test_detect_generic_credentials(self):
        content = 'api_key = "abcdef1234567890abcdef"\n'
        findings = self.inspector.scan_content("client.py", content)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Severity.HIGH)
        self.assertIn("[REDACTED]", findings[0].evidence)

    def test_suppression_tag_skips_finding(self):
        content = 'secret = "abcdef1234567890abcdef"  # nosec\n'
        findings = self.inspector.scan_content("auth.py", content)
        self.assertEqual(len(findings), 0)

        content2 = '# supervisor:ignore\nsecret = "abcdef1234567890abcdef"\n'
        findings2 = self.inspector.scan_content("auth2.py", content2)
        self.assertEqual(len(findings2), 0)

    def test_dangerous_ast_exec_eval(self):
        py_code = "def run(x):\n    eval(x)\n    exec(x)\n"
        findings = self.inspector.scan_content("danger.py", py_code)
        exec_eval = [f for f in findings if "Dangerous call" in f.description]
        self.assertEqual(len(exec_eval), 2)
        self.assertTrue(all(f.severity == Severity.CRITICAL for f in exec_eval))

    def test_subprocess_shell_true(self):
        py_code = "import subprocess\nsubprocess.run(['ls'], shell=True)\n"
        findings = self.inspector.scan_content("subp.py", py_code)
        shell_findings = [f for f in findings if "shell=True" in f.description]
        self.assertEqual(len(shell_findings), 1)
        self.assertEqual(shell_findings[0].severity, Severity.HIGH)

    def test_tempfile_mktemp(self):
        py_code = "import tempfile\nf = tempfile.mktemp()\n"
        findings = self.inspector.scan_content("tmp.py", py_code)
        mktemp_findings = [f for f in findings if "mktemp" in f.description]
        self.assertEqual(len(mktemp_findings), 1)
        self.assertEqual(mktemp_findings[0].severity, Severity.HIGH)

    def test_redact_secret_utility(self):
        masked = SecurityInspector.redact_secret('API_KEY = "FAKE_STRIPE_KEY_FOR_TESTS"')
        self.assertIn("[REDACTED]", masked)
        self.assertNotIn("FAKE_STRIPE_KEY_FOR_TESTS", masked)


if __name__ == "__main__":
    unittest.main()

