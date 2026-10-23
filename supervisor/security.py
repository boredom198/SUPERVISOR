"""Security and credential inspection module for SUPERVISOR."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from supervisor.models import (
    Category,
    Confidence,
    Finding,
    FindingStatus,
    Severity,
)


class SecurityInspector:
    """Detects credentials, dangerous code execution patterns, and insecure configurations with secret redaction."""

    # Patterns for high-confidence secrets
    SECRET_PATTERNS = [
        (
            re.compile(r"""-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"""),
            "Private key block embedded in code",
            Severity.CRITICAL,
            Confidence.CONFIRMED,
            "SEC-KEY-001",
        ),
        (
            re.compile(r"""(?i)(?:sk_live_|ghp_|AKIA[0-9A-Z]{16}|xoxb-[0-9]{10,})[A-Za-z0-9_\-\.]{10,}"""),
            "Well-known cloud or API service token detected",
            Severity.CRITICAL,
            Confidence.CONFIRMED,
            "SEC-TOK-001",
        ),
        (
            re.compile(
                r"""(?i)(?:api_key|apikey|secret|password|auth_token|client_secret)\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]"""
            ),
            "Hardcoded sensitive credential or API key assignment",
            Severity.HIGH,
            Confidence.HEURISTIC,
            "SEC-CRED-001",
        ),
    ]

    SUPPRESSION_TAGS = ("# supervisor:ignore", "# supervisor:suppress", "# nosec")

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    @staticmethod
    def redact_secret(raw_str: str) -> str:
        """Redacts sensitive credentials from strings, replacing secret characters with asterisks."""
        # Redact private key bodies
        redacted = re.sub(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "---" + "--BEGIN PRIVATE KEY-----\n[REDACTED PRIVATE KEY]\n---" + "--END PRIVATE KEY-----",  # nosec
            raw_str,
        )
        redacted = re.sub(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "---" + "--BEGIN [REDACTED] PRIVATE KEY-----",  # nosec
            redacted,
        )
        # Redact known token prefixes
        def _mask_match(m):
            val = m.group(0)
            prefix = val[:8] if len(val) >= 12 else val[:4]
            return prefix + ("*" * (len(val) - len(prefix)))

        redacted = re.sub(
            r"""(?i)(?:sk_live_|ghp_|AKIA[0-9A-Z]{16}|xoxb-[0-9]{10,})[A-Za-z0-9_\-\.]{10,}""",
            _mask_match,
            redacted,
        )
        # Redact key=value assignments
        def _mask_assignment(m):
            val = m.group(0)
            parts = val.split("=", 1)
            var_part = parts[0] + "= "
            quote = parts[1].strip()[0]
            return f'{var_part}{quote}[REDACTED]{quote}'

        redacted = re.sub(
            r"""(?i)(?:api_key|apikey|secret|password|auth_token|client_secret)\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]""",
            _mask_assignment,
            redacted,
        )
        return redacted

    def _is_suppressed(self, lines: List[str], line_idx: int) -> bool:
        """Checks if a finding on line_idx (0-indexed) has an inline suppression tag."""
        # Check current line
        if line_idx < len(lines):
            curr_line = lines[line_idx].strip()
            if any(tag in curr_line for tag in self.SUPPRESSION_TAGS):
                return True
        # Check preceding line
        if line_idx > 0:
            prev_line = lines[line_idx - 1].strip()
            if any(tag in prev_line for tag in self.SUPPRESSION_TAGS):
                return True
        return False

    def inspect_file(self, rel_path: str, content: str) -> List[Finding]:
        """Alias for scan_content."""
        return self.scan_content(rel_path, content)

    def scan_content(self, rel_path: str, content: str) -> List[Finding]:
        """Scans source content for secrets, dangerous executions, and insecure patterns."""
        findings: List[Finding] = []

        # Ignore non-code files, test fixtures, or test files for secret warnings
        is_test_file = any(part in rel_path.lower() for part in ("test", "fixture", "mock", "spec"))
        lines = content.splitlines()

        # 1. Secret Scanning
        if not is_test_file:
            reported_lines: Set[int] = set()
            for pattern, desc, severity, confidence, rule_id in self.SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    lineno = content[: match.start()].count("\n") + 1
                    if lineno in reported_lines:
                        continue
                    line_idx = lineno - 1

                    if self._is_suppressed(lines, line_idx):
                        continue

                    reported_lines.add(lineno)
                    raw_snippet = lines[line_idx] if line_idx < len(lines) else match.group(0)
                    safe_evidence = self.redact_secret(raw_snippet.strip())

                    findings.append(
                        Finding(
                            finding_id=f"{rule_id}-{abs(hash(rel_path + str(lineno))) % 10000:04d}",
                            severity=severity,
                            category=Category.SECURITY,
                            file_path=rel_path,
                            line_number=lineno,
                            description=desc,
                            evidence=f"Line {lineno}: {safe_evidence}",
                            why_it_matters="Exposing credentials in source code enables unauthorized access and token compromise.",
                            recommended_correction="Load credentials dynamically from environment variables or a secret vault.",
                            verification_method="Static secret scan verifying removal of hardcoded values.",
                            confidence=confidence,
                            status=FindingStatus.CONFIRMED,
                            source_check="security_scanner",
                        )
                    )

        # 2. Python-specific dangerous execution AST checks
        if rel_path.endswith(".py"):
            findings.extend(self._scan_python_security_ast(rel_path, content, lines))

        return findings

    def _scan_python_security_ast(
        self, rel_path: str, content: str, lines: List[str]
    ) -> List[Finding]:
        """Uses AST to inspect Python code for dangerous calls: eval, exec, shell=True, mktemp."""
        findings: List[Finding] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return findings

        class SecurityASTVisitor(ast.NodeVisitor):
            def __init__(self, inspector):
                self.inspector = inspector
                self.records: List[Tuple[str, int, str, Severity]] = []

            def visit_Call(self, node: ast.Call):
                lineno = node.lineno
                line_idx = lineno - 1

                # Check inline suppression
                if self.inspector._is_suppressed(lines, line_idx):
                    self.generic_visit(node)
                    return

                # Check eval/exec
                if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                    self.records.append(
                        (
                            f"Dangerous call `{node.func.id}()` detected.",
                            lineno,
                            f"SEC-EXEC-{abs(hash(rel_path + str(lineno) + node.func.id)) % 10000:04d}",
                            Severity.CRITICAL,
                        )
                    )

                # Check tempfile.mktemp
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "mktemp"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "tempfile"
                ):
                    self.records.append(
                        (
                            "Insecure function `tempfile.mktemp()` detected. Use `NamedTemporaryFile` instead.",
                            lineno,
                            f"SEC-TMP-{abs(hash(rel_path + str(lineno))) % 10000:04d}",
                            Severity.HIGH,
                        )
                    )

                # Check subprocess shell=True
                if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "Popen",
                    "run",
                    "call",
                    "check_output",
                ):
                    for kw in node.keywords:
                        if (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            self.records.append(
                                (
                                    "Subprocess invocation with `shell=True` detected.",
                                    lineno,
                                    f"SEC-SHELL-{abs(hash(rel_path + str(lineno))) % 10000:04d}",
                                    Severity.HIGH,
                                )
                            )
                self.generic_visit(node)

        visitor = SecurityASTVisitor(self)
        visitor.visit(tree)

        for desc, lineno, fid, sev in visitor.records:
            snippet = lines[lineno - 1].strip() if lineno - 1 < len(lines) else ""
            findings.append(
                Finding(
                    finding_id=fid,
                    severity=sev,
                    category=Category.SECURITY,
                    file_path=rel_path,
                    line_number=lineno,
                    description=desc,
                    evidence=f"Line {lineno}: {snippet}",
                    why_it_matters="Insecure execution patterns introduce arbitrary code execution or command injection vulnerabilities.",
                    recommended_correction="Use safe alternatives (e.g., ast.literal_eval, shlex.split with shell=False, or tempfile.NamedTemporaryFile).",
                    verification_method="Static AST inspection",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="security_ast_scanner",
                )
            )

        return findings
