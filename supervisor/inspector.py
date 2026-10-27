"""Code inspection engine for syntax, structure, error handling, and placeholder detection."""

from __future__ import annotations

from supervisor.identifiers import stable_suffix

import ast
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Python 3.11+ standard library tomllib
try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore

from supervisor.models import (
    Category,
    Confidence,
    Finding,
    FindingStatus,
    Severity,
    TestResult,
)
from supervisor.runner import TestRunner
from supervisor.language_inspector import LanguageInspector, SOURCE_EXTENSIONS
from supervisor.security import SecurityInspector


class CodeInspector:
    """Performs deterministic code analysis: syntax parsing, AST structure, placeholders, and error handling."""

    PLACEHOLDER_REGEX = re.compile(
        r"""(?i)\b(?:TODO|FIXME|STUB|XXX)\b\s*[:\-]?\s*(?:implement|fix|handle|finish|complete|broken|unimplemented|temp|mock|dummy)\b.*"""
    )

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.security = SecurityInspector(self.workspace_root)
        self.runner = TestRunner(self.workspace_root)
        self.language_inspector = LanguageInspector(self.workspace_root)

    def inspect_python_file(self, rel_path: str, content: str) -> List[Finding]:
        """Inspects Python source for syntax errors, AST structural problems, security, and error handling."""
        findings: List[Finding] = []
        lines = content.splitlines()

        # 1. Syntax check via AST
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as e:
            findings.append(
                Finding(
                    finding_id=f"SYNTAX-{stable_suffix(rel_path, e.lineno)}",
                    severity=Severity.CRITICAL,
                    category=Category.BUG,
                    file_path=rel_path,
                    line_number=e.lineno,
                    title=f"SyntaxError in {rel_path}:{e.lineno}",
                    description=f"SyntaxError in Python file: {e.msg}",
                    evidence=f"Line {e.lineno}, column {e.offset}: {e.text or ''}".strip(),
                    why_it_matters="Code with syntax errors fails to parse and cannot execute.",
                    recommended_correction="Fix the syntax error at the indicated line and column.",
                    verification_method="Run `python -m py_compile <file>` or `ast.parse`",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="python_syntax_parser",
                )
            )
            return findings  # Cannot perform AST structural analysis if syntax parsing fails

        # 2. AST Visitor for error handling and empty stubs
        class StructuralVisitor(ast.NodeVisitor):
            def __init__(self):
                self.bare_excepts: List[int] = []
                self.stub_functions: List[Tuple[str, int]] = []
                self.not_implemented_nodes: List[Tuple[str, int]] = []

            def visit_ExceptHandler(self, node: ast.ExceptHandler):
                # Check for bare except or silent except (Exception, BaseException) with pass-only body
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    if node.type is None:
                        self.bare_excepts.append(node.lineno)
                    elif isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                        self.bare_excepts.append(node.lineno)
                    elif isinstance(node.type, ast.Tuple):
                        if any(isinstance(elt, ast.Name) and elt.id in ("Exception", "BaseException") for elt in node.type.elts):
                            self.bare_excepts.append(node.lineno)
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._check_function(node)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._check_function(node)
                self.generic_visit(node)

            def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
                # Check if function is abstract or protocol
                is_abstract = any(
                    (isinstance(d, ast.Name) and d.id in ("abstractmethod", "abstractproperty", "overload"))
                    or (isinstance(d, ast.Attribute) and d.attr in ("abstractmethod", "abstractproperty", "overload"))
                    for d in node.decorator_list
                )
                if is_abstract:
                    return

                # Check for raise NotImplementedError
                for child in ast.walk(node):
                    if isinstance(child, ast.Raise) and child.exc:
                        name = ""
                        if isinstance(child.exc, ast.Name):
                            name = child.exc.id
                        elif isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name):
                            name = child.exc.func.id
                        if name == "NotImplementedError":
                            self.not_implemented_nodes.append((node.name, child.lineno))

                # Check for empty stub (single pass body or return None) in non-trivial function
                body_statements = [
                    stmt for stmt in node.body
                    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))
                ]
                if len(body_statements) == 1:
                    first = body_statements[0]
                    if isinstance(first, ast.Pass):
                        # Flag as empty stub if name is not an event hook or dummy
                        if not any(k in node.name.lower() for k in ("noop", "dummy", "setup", "teardown", "hook")):
                            self.stub_functions.append((node.name, node.lineno))

        visitor = StructuralVisitor()
        visitor.visit(tree)

        for lineno in visitor.bare_excepts:
            findings.append(
                Finding(
                    finding_id=f"ERR-SILENT-{stable_suffix(rel_path, lineno)}",
                    severity=Severity.MEDIUM,
                    category=Category.ERROR_HANDLING,
                    file_path=rel_path,
                    line_number=lineno,
                    title="Silent exception swallowing detected",
                    description="Silent exception swallowing (`except: pass` or `except Exception: pass`) detected.",
                    evidence=f"Line {lineno}: Exception handler contains only `pass` without logging or re-raising.",
                    why_it_matters="Silently suppressing exceptions hides bugs, masks catastrophic failures, and corrupts state.",
                    recommended_correction="Catch specific exception types, log the caught exception, or re-raise.",
                    verification_method="Static AST inspection and error handling unit tests.",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="python_ast_error_handling",
                )
            )

        for func_name, lineno in visitor.not_implemented_nodes:
            findings.append(
                Finding(
                    finding_id=f"REQ-NIE-{stable_suffix(rel_path, lineno, func_name)}",
                    severity=Severity.HIGH,
                    category=Category.MISSING_REQUIREMENT,
                    file_path=rel_path,
                    line_number=lineno,
                    title=f"Unimplemented construct in `{func_name}`",
                    description=f"Unimplemented placeholder (`raise NotImplementedError`) in non-abstract function `{func_name}`.",
                    evidence=f"Line {lineno}: function `{func_name}` raises NotImplementedError",
                    why_it_matters="Incomplete implementations cause runtime failures when the code path is exercised.",
                    recommended_correction=f"Provide the complete functional implementation of `{func_name}`.",
                    verification_method="Functional test execution exercising the function.",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="python_ast_unimplemented",
                )
            )

        is_test_file = any(part in rel_path.lower() for part in ("test", "fixture", "mock", "spec"))
        if not is_test_file:
            for func_name, lineno in visitor.stub_functions:
                if func_name.startswith("test_"):
                    continue
                findings.append(
                    Finding(
                        finding_id=f"STUB-{stable_suffix(rel_path, lineno, func_name)}",
                    severity=Severity.MEDIUM,
                    category=Category.MISSING_REQUIREMENT,
                    file_path=rel_path,
                    line_number=lineno,
                    title=f"Empty stub function `{func_name}`",
                    description=f"Function `{func_name}` has an empty implementation containing only `pass`.",
                    evidence=f"Line {lineno}: function `{func_name}` body is empty `pass`",
                    why_it_matters="Empty stub implementations indicate unfinished work that may cause silent no-ops or regressions.",
                    recommended_correction=f"Implement the expected behavior for `{func_name}` or remove it if obsolete.",
                    verification_method="Code review and test execution.",
                    confidence=Confidence.HEURISTIC,
                    status=FindingStatus.CONFIRMED,
                    source_check="python_ast_stub_detector",
                )
            )

        # 3. Contextual Placeholder Comments
        findings.extend(self.inspect_placeholders(rel_path, content))

        # 4. Dangerous AST execution checks (eval, exec, shell=True, mktemp)
        findings.extend(self.security._scan_python_security_ast(rel_path, content, lines))

        return findings

    def inspect_json_file(self, rel_path: str, content: str) -> List[Finding]:
        """Validates JSON files for syntax and parsing errors."""
        findings: List[Finding] = []
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            findings.append(
                Finding(
                    finding_id=f"JSON-{stable_suffix(rel_path, e.lineno)}",
                    severity=Severity.HIGH,
                    category=Category.DEPENDENCY_CONFIG,
                    file_path=rel_path,
                    line_number=e.lineno,
                    title=f"Malformed JSON in {rel_path}:{e.lineno}",
                    description=f"Invalid JSON in configuration file: {e.msg}",
                    evidence=f"Line {e.lineno}, column {e.colno}: {e.msg}",
                    why_it_matters="Malformed JSON configuration causes tools, package managers, and runtimes to crash.",
                    recommended_correction="Correct JSON syntax errors (remove trailing commas, fix unbalanced brackets, quote keys).",
                    verification_method="Run `json.loads` check.",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="json_parser",
                )
            )
        return findings

    def inspect_toml_file(self, rel_path: str, content: str) -> List[Finding]:
        """Validates TOML files using standard library tomllib."""
        findings: List[Finding] = []
        if tomllib is None:
            return findings

        try:
            tomllib.loads(content)
        except Exception as e:
            lineno = getattr(e, "lineno", None)
            findings.append(
                Finding(
                    finding_id=f"TOML-{stable_suffix(rel_path, lineno or 1)}",
                    severity=Severity.HIGH,
                    category=Category.DEPENDENCY_CONFIG,
                    file_path=rel_path,
                    line_number=lineno,
                    title=f"Malformed TOML in {rel_path}",
                    description=f"Invalid TOML in configuration file: {str(e)}",
                    evidence=f"TOML parse error: {str(e)}",
                    why_it_matters="Malformed TOML configurations (such as pyproject.toml) break dependency resolution and builds.",
                    recommended_correction="Correct TOML syntax according to specification.",
                    verification_method="Run TOML parser check.",
                    confidence=Confidence.CONFIRMED,
                    status=FindingStatus.CONFIRMED,
                    source_check="toml_parser",
                )
            )
        return findings

    def inspect_placeholders(self, rel_path: str, content: str) -> List[Finding]:
        """Scans source content for actionable TODO/FIXME/STUB markers relevant to task."""
        findings: List[Finding] = []
        # Ignore test files, documentation, markdown, and supervisor's own code
        if any(part in rel_path.lower() for part in ("test", "fixture", "spec", ".md", "doc")):
            return findings

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Match actionable TODO/FIXME
            m = self.PLACEHOLDER_REGEX.search(stripped)
            if m:
                matched_text = m.group(0)
                findings.append(
                    Finding(
                        finding_id=f"TODO-{stable_suffix(rel_path, i)}",
                        severity=Severity.LOW,
                        category=Category.MISSING_REQUIREMENT,
                        file_path=rel_path,
                        line_number=i,
                        title=f"Actionable placeholder in {rel_path}:{i}",
                        description=f"Actionable incomplete-task marker detected: `{matched_text[:60]}`",
                        evidence=f"Line {i}: {stripped}",
                        why_it_matters="Unfinished TODO/FIXME markers in production code may leave critical requirements unaddressed.",
                        recommended_correction="Complete the task referenced in the comment or remove if obsolete.",
                        verification_method="Source code review confirming resolution.",
                        confidence=Confidence.HEURISTIC,
                        status=FindingStatus.RECOMMENDED,
                        source_check="placeholder_scanner",
                    )
                )
        return findings

    def inspect_file(self, rel_path: str, content: str) -> List[Finding]:
        """Routes file content to appropriate language inspectors based on extension."""
        findings: List[Finding] = []

        if rel_path.endswith(".py"):
            findings.extend(self.inspect_python_file(rel_path, content))
        elif rel_path.endswith(".json"):
            findings.extend(self.inspect_json_file(rel_path, content))
        elif rel_path.endswith(".toml"):
            findings.extend(self.inspect_toml_file(rel_path, content))
        elif Path(rel_path).suffix.lower() in SOURCE_EXTENSIONS:
            findings.extend(self.language_inspector.inspect(rel_path, content))
        else:
            # Check placeholders on general text/code files
            findings.extend(self.inspect_placeholders(rel_path, content))

        return findings

    def inspect_incomplete_implementations(self, rel_path: str, content: str) -> List[Finding]:
        """Scans file for incomplete implementation markers, placeholders, and stubs."""
        return self.inspect_placeholders(rel_path, content)

    def inspect_secrets(self, rel_path: str, content: str) -> List[Finding]:
        """Scans file for credentials and secrets."""
        return self.security.inspect_file(rel_path, content)

    def run_tests(self, test_command: Optional[str] = None) -> List[TestResult]:
        """Runs test discovery and execution via TestRunner."""
        return self.runner.execute_all(explicit_command=test_command)

