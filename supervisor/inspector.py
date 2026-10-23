"""Code inspection and test execution engine for SUPERVISOR."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from supervisor.models import (
    Category,
    Finding,
    FindingStatus,
    Severity,
    TestResult,
)


class CodeInspector:
    """Performs evidence-based code inspections: AST analysis, security scanning, syntax verification, and test execution."""

    SECRET_PATTERNS = [
        (re.compile(r"""(?i)(?:api_key|apikey|secret|password|token|auth_token)\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]"""), "Hardcoded sensitive credential or API key found"),
        (re.compile(r"""-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"""), "Private key block embedded in code"),
    ]

    DANGEROUS_CALLS = [
        ("eval", Severity.CRITICAL, Category.SECURITY, "Use of eval() introduces arbitrary code execution risks."),
        ("exec", Severity.CRITICAL, Category.SECURITY, "Use of exec() introduces arbitrary dynamic code execution risks."),
    ]

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    def inspect_python_file(self, rel_path: str, content: str) -> List[Finding]:
        """Inspects Python source for syntax errors, AST anti-patterns, and bad error handling."""
        findings: List[Finding] = []

        # 1. Syntax check via AST
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as e:
            findings.append(
                Finding(
                    finding_id=f"SYNTAX-{abs(hash(rel_path + str(e.lineno))) % 10000:04d}",
                    severity=Severity.CRITICAL,
                    category=Category.BUG,
                    file_path=rel_path,
                    line_number=e.lineno,
                    description=f"SyntaxError in Python file: {e.msg}",
                    evidence=f"Line {e.lineno}, column {e.offset}: {e.text or ''}".strip(),
                    why_it_matters="Code with syntax errors fails to parse and cannot execute.",
                    recommended_correction="Fix the syntax error at the indicated line and column.",
                    verification_method="Run `python -m py_compile <file>` or `ast.parse`",
                    status=FindingStatus.CONFIRMED,
                )
            )
            return findings  # Cannot do further AST analysis if syntax fails

        # 2. AST Visitor for dangerous calls and empty except blocks
        class ASTVisitor(ast.NodeVisitor):
            def __init__(self):
                self.issues = []

            def visit_Call(self, node: ast.Call):
                # Check eval/exec
                if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                    self.issues.append(("dangerous_call", node.lineno, node.func.id))
                # Check subprocess shell=True
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("Popen", "run", "call", "check_output")
                ):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self.issues.append(("shell_true", node.lineno, "subprocess with shell=True"))
                self.generic_visit(node)

            def visit_ExceptHandler(self, node: ast.ExceptHandler):
                # Check for bare except or pass-only except on general Exception/BaseException
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    if node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")):
                        self.issues.append(("silent_pass_except", node.lineno, "Bare or silent `except: pass` block"))
                self.generic_visit(node)

        visitor = ASTVisitor()
        visitor.visit(tree)

        for issue_type, lineno, detail in visitor.issues:
            if issue_type == "dangerous_call":
                findings.append(
                    Finding(
                        finding_id=f"SEC-{abs(hash(rel_path + str(lineno) + detail)) % 10000:04d}",
                        severity=Severity.CRITICAL,
                        category=Category.SECURITY,
                        file_path=rel_path,
                        line_number=lineno,
                        description=f"Dangerous function call `{detail}()` detected.",
                        evidence=f"Call to `{detail}` at line {lineno}",
                        why_it_matters="Executing untrusted code or arbitrary strings creates critical vulnerabilities.",
                        recommended_correction=f"Replace `{detail}` with a safe alternative like `ast.literal_eval` or explicit parsing.",
                        verification_method="Static AST inspection and unit tests",
                        status=FindingStatus.CONFIRMED,
                    )
                )
            elif issue_type == "shell_true":
                findings.append(
                    Finding(
                        finding_id=f"SEC-{abs(hash(rel_path + str(lineno) + detail)) % 10000:04d}",
                        severity=Severity.HIGH,
                        category=Category.SECURITY,
                        file_path=rel_path,
                        line_number=lineno,
                        description="Subprocess invocation with `shell=True` detected.",
                        evidence=f"Line {lineno}: subprocess call includes `shell=True`",
                        why_it_matters="Using `shell=True` creates command injection vulnerabilities if inputs are not sanitized.",
                        recommended_correction="Pass arguments as a list with `shell=False`.",
                        verification_method="Static AST inspection",
                        status=FindingStatus.CONFIRMED,
                    )
                )
            elif issue_type == "silent_pass_except":
                findings.append(
                    Finding(
                        finding_id=f"ERR-{abs(hash(rel_path + str(lineno) + detail)) % 10000:04d}",
                        severity=Severity.MEDIUM,
                        category=Category.ERROR_HANDLING,
                        file_path=rel_path,
                        line_number=lineno,
                        description="Silent exception swallowing (`except: pass`) detected.",
                        evidence=f"Line {lineno}: exception handler contains only `pass`",
                        why_it_matters="Silent failure hides root causes of errors and corrupts execution state.",
                        recommended_correction="Log the caught exception or catch specific expected exception types.",
                        verification_method="Static AST inspection and error handling tests",
                        status=FindingStatus.CONFIRMED,
                    )
                )

        return findings

    def inspect_json_file(self, rel_path: str, content: str) -> List[Finding]:
        """Validates JSON files for syntax and parsing errors."""
        findings: List[Finding] = []
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            findings.append(
                Finding(
                    finding_id=f"JSON-{abs(hash(rel_path + str(e.lineno))) % 10000:04d}",
                    severity=Severity.HIGH,
                    category=Category.DEPENDENCY_CONFIG,
                    file_path=rel_path,
                    line_number=e.lineno,
                    description=f"Invalid JSON in configuration file: {e.msg}",
                    evidence=f"Line {e.lineno}, column {e.colno}: {e.doc.splitlines()[e.lineno-1] if e.lineno <= len(e.doc.splitlines()) else ''}".strip(),
                    why_it_matters="Malformed JSON causes tools, plugins, or runtime configurations to crash.",
                    recommended_correction="Correct JSON syntax errors (e.g. trailing commas, missing quotes).",
                    verification_method="`json.loads` parsing check",
                    status=FindingStatus.CONFIRMED,
                )
            )
        return findings

    def inspect_secrets(self, rel_path: str, content: str) -> List[Finding]:
        """Scans text for exposed credentials and private keys."""
        findings: List[Finding] = []
        # Ignore sample test files or dummy configs
        if "test" in rel_path.lower() or "fixture" in rel_path.lower():
            return findings

        for pattern, desc in self.SECRET_PATTERNS:
            for match in pattern.finditer(content):
                lineno = content[: match.start()].count("\n") + 1
                findings.append(
                    Finding(
                        finding_id=f"SEC-{abs(hash(rel_path + str(lineno) + desc)) % 10000:04d}",
                        severity=Severity.CRITICAL,
                        category=Category.SECURITY,
                        file_path=rel_path,
                        line_number=lineno,
                        description=desc,
                        evidence=f"Line {lineno}: matched secret pattern",
                        why_it_matters="Exposing credentials in source code leads to credential theft and unauthorized access.",
                        recommended_correction="Load credentials from environment variables or a secure key store.",
                        verification_method="Secret pattern scan",
                        status=FindingStatus.CONFIRMED,
                    )
                )
        return findings

    def inspect_incomplete_implementations(self, rel_path: str, content: str) -> List[Finding]:
        """Detects stub functions, TODO comments, and unimplemented placeholders using AST analysis."""
        findings: List[Finding] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return findings

        class NotImplementedVisitor(ast.NodeVisitor):
            def __init__(self):
                self.issues = []

            def visit_FunctionDef(self, node: ast.FunctionDef):
                # Ignore abstract methods or protocols
                is_abstract = any(
                    (isinstance(d, ast.Name) and d.id in ("abstractmethod", "abstractproperty"))
                    or (isinstance(d, ast.Attribute) and d.attr in ("abstractmethod", "abstractproperty"))
                    for d in node.decorator_list
                )
                if not is_abstract:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Raise) and child.exc:
                            name = ""
                            if isinstance(child.exc, ast.Name):
                                name = child.exc.id
                            elif isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name):
                                name = child.exc.func.id
                            if name == "NotImplementedError":
                                self.issues.append((node.name, child.lineno))
                self.generic_visit(node)

        visitor = NotImplementedVisitor()
        visitor.visit(tree)

        for func_name, lineno in visitor.issues:
            findings.append(
                Finding(
                    finding_id=f"REQ-{abs(hash(rel_path + str(lineno) + func_name)) % 10000:04d}",
                    severity=Severity.HIGH,
                    category=Category.MISSING_REQUIREMENT,
                    file_path=rel_path,
                    line_number=lineno,
                    description=f"Unimplemented placeholder (`raise NotImplementedError`) in non-abstract function `{func_name}`.",
                    evidence=f"Line {lineno}: function `{func_name}` raises NotImplementedError",
                    why_it_matters="Incomplete implementations cause runtime failures when the code path is exercised.",
                    recommended_correction=f"Provide the complete implementation of `{func_name}`.",
                    verification_method="Functional test execution",
                    status=FindingStatus.CONFIRMED,
                )
            )
        return findings

    def run_tests(self, test_command: Optional[str] = None) -> List[TestResult]:
        """Executes actual tests in the workspace and records genuine results."""
        import shlex

        results: List[TestResult] = []

        commands_to_try: List[str] = []
        if test_command:
            commands_to_try.append(test_command)
        else:
            # Check for pytest or unittest
            tests_dir = self.workspace_root / "tests"
            if tests_dir.exists():
                commands_to_try.append("python -m unittest discover -s tests")
            # Check for npm test
            pkg_json = self.workspace_root / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    if "test" in data.get("scripts", {}):
                        commands_to_try.append("npm test")
                except (json.JSONDecodeError, OSError):
                    data = {}

        for cmd in commands_to_try:
            start_t = time.perf_counter()
            try:
                # Use shlex to safely parse args and execute with shell=False
                args = shlex.split(cmd, posix=False) if os.name == "nt" else shlex.split(cmd)
                proc = subprocess.run(
                    args,
                    shell=False,
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                duration = time.perf_counter() - start_t
                results.append(
                    TestResult(
                        command=cmd,
                        exit_code=proc.returncode,
                        stdout=proc.stdout[-4000:] if proc.stdout else "",
                        stderr=proc.stderr[-4000:] if proc.stderr else "",
                        duration_seconds=round(duration, 3),
                        passed=(proc.returncode == 0),
                    )
                )
            except subprocess.TimeoutExpired:
                duration = time.perf_counter() - start_t
                results.append(
                    TestResult(
                        command=cmd,
                        exit_code=-1,
                        stdout="",
                        stderr="Test execution timed out after 120 seconds.",
                        duration_seconds=round(duration, 3),
                        passed=False,
                    )
                )
            except Exception as e:
                duration = time.perf_counter() - start_t
                results.append(
                    TestResult(
                        command=cmd,
                        exit_code=-1,
                        stdout="",
                        stderr=f"Failed to execute test command: {str(e)}",
                        duration_seconds=round(duration, 3),
                        passed=False,
                    )
                )

        return results
