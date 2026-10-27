"""Test discovery and controlled execution runner for SUPERVISOR."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import sys
import re
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from supervisor.models import TestResult, TestStatus


class TestRunner:
    """Discovers project test suites and executes them with strict bounds, timeouts, and authorization controls."""

    def __init__(
        self,
        workspace_root: Optional[Path | str] = None,
        timeout_seconds: Optional[int] = None,
        max_output_chars: int = 4000,
        allow_repo_scripts: bool = True,
    ):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds
        else:
            try:
                self.timeout_seconds = max(1, min(600, int(os.environ.get("SUPERVISOR_TEST_TIMEOUT", "60"))))
            except ValueError:
                self.timeout_seconds = 60
        self.timeout_seconds = max(1, min(600, int(self.timeout_seconds)))
        self.max_output_chars = max(1, int(max_output_chars))
        self.allow_repo_scripts = allow_repo_scripts

    def discover_test_commands(self) -> List[Tuple[str, str]]:
        """Discovers applicable test commands based on repository metadata.
        
        Returns a list of tuples: (command_string, discovery_source)
        """
        discovered: List[Tuple[str, str]] = []

        # 1. Python Discovery
        tests_dir = self.workspace_root / "tests"
        has_tests_dir = tests_dir.is_dir()
        has_test_files = False
        try:
            candidates = list(self.workspace_root.glob("**/test_*.py")) + list(self.workspace_root.glob("**/*_test.py"))
            for item in candidates:
                if not any(part in str(item).split(os.sep) for part in (".venv", "venv", "node_modules", ".supervisor")):
                    has_test_files = True
                    break
        except OSError:
            pass

        tests_dir_has_python_tests = False
        if has_tests_dir:
            try:
                tests_dir_has_python_tests = any(t.is_file() and (t.name.startswith("test_") or t.name.endswith("_test.py")) and t.suffix == ".py" for t in tests_dir.rglob("*.py"))
            except OSError:
                tests_dir_has_python_tests = False
        if tests_dir_has_python_tests or has_test_files:
            # unittest silently exits 0 when it collects zero tests. Prefer pytest when
            # pytest-style top-level test functions exist and pytest is installed.
            pytest_style = False
            try:
                candidate_tests = list(self.workspace_root.glob("**/test_*.py")) + list(self.workspace_root.glob("**/*_test.py"))
                for item in candidate_tests:
                    if any(part in str(item).split(os.sep) for part in (".venv", "venv", "node_modules", ".supervisor")):
                        continue
                    sample = item.read_text(encoding="utf-8", errors="replace")[:200_000]
                    if re.search(r"^def\s+test_", sample, re.M):
                        pytest_style = True
                        break
            except OSError:
                pass
            try:
                pytest_available = __import__("importlib.util", fromlist=["find_spec"]).find_spec("pytest") is not None
            except (ImportError, ValueError):
                pytest_available = False
            if pytest_style and pytest_available:
                discovered.append(("python -m pytest -q", "python_pytest_discovery"))
            elif tests_dir_has_python_tests:
                discovered.append(("python -m unittest discover -s tests", "python_unittest_discovery"))
            else:
                discovered.append(("python -m unittest discover -s .", "python_unittest_discovery"))

        # 2. Go and Rust projects have conventional, repository-level test commands.
        if (self.workspace_root / "go.mod").is_file():
            discovered.append(("go test ./...", "go_module_test"))
        if (self.workspace_root / "Cargo.toml").is_file():
            discovered.append(("cargo test", "cargo_test"))

        # 3. Java build tools, when their standard project descriptors are present.
        if (self.workspace_root / "pom.xml").is_file():
            discovered.append(("mvn test", "maven_test"))
        elif any((self.workspace_root / name).is_file() for name in ("build.gradle", "build.gradle.kts")):
            discovered.append(("gradle test", "gradle_test"))

        # 4. JavaScript / Node.js Discovery
        pkg_json = self.workspace_root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {})
                if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
                    test_script = scripts["test"]
                    # Skip npm's conventional placeholder test script.
                    if "no test specified" not in test_script.lower():
                        discovered.append(("npm test", "package_json_test_script"))
            except (json.JSONDecodeError, OSError):
                pass

        return discovered

    def run_command(self, cmd: str, source_label: str = "custom") -> TestResult:
        """Executes a single test command safely with bounded timeout and output."""
        if not self.allow_repo_scripts and any(k in cmd for k in ("npm", "sh ", "bash ", "./")):
            return TestResult(
                command=cmd,
                exit_code=-1,
                stdout="",
                stderr="Execution blocked by policy: running repository-controlled scripts is not authorized.",
                duration_seconds=0.0,
                passed=False,
                status=TestStatus.BLOCKED,
                reason="Authorization policy prevented execution of repository script.",
            )

        start_t = time.perf_counter()
        try:
            # Safe arg splitting: shell=False
            if os.name == "nt":
                raw_args = shlex.split(cmd, posix=False)
                args = [
                    a[1:-1] if (a.startswith('"') and a.endswith('"')) or (a.startswith("'") and a.endswith("'")) else a
                    for a in raw_args
                ]
            else:
                args = shlex.split(cmd)
            if not args:
                raise ValueError("Empty test command")
            # Use the current interpreter rather than relying on a PATH alias named python.
            if args[0].lower() in {"python", "python3"} and len(args) > 1:
                args[0] = sys.executable
            # Capture output to temporary files rather than unbounded in-memory pipes;
            # malicious/noisy tests must not exhaust SUPERVISOR's memory.
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                proc = subprocess.run(
                    args,
                    shell=False,
                    cwd=str(self.workspace_root),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                stdout_file.seek(0, os.SEEK_END)
                stdout_size = stdout_file.tell()
                stdout_file.seek(max(0, stdout_size - self.max_output_chars))
                stdout_capped = stdout_file.read(self.max_output_chars).decode("utf-8", errors="replace")
                stderr_file.seek(0, os.SEEK_END)
                stderr_size = stderr_file.tell()
                stderr_file.seek(max(0, stderr_size - self.max_output_chars))
                stderr_capped = stderr_file.read(self.max_output_chars).decode("utf-8", errors="replace")
            duration = time.perf_counter() - start_t
            passed = proc.returncode == 0
            combined_output = f"{stdout_capped}\n{stderr_capped}"
            zero_test_run = bool(re.search(r"Ran 0 tests?\b", combined_output, re.I)) or "NO TESTS RAN" in combined_output.upper()
            if zero_test_run:
                status = TestStatus.NOT_AVAILABLE
                passed = False
            else:
                status = TestStatus.PASSED if passed else TestStatus.FAILED

            return TestResult(
                command=cmd,
                exit_code=proc.returncode,
                stdout=stdout_capped,
                stderr=stderr_capped,
                duration_seconds=round(duration, 3),
                passed=passed,
                status=status,
                reason="No test cases discovered." if status == TestStatus.NOT_AVAILABLE else None,
            )

        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - start_t
            return TestResult(
                command=cmd,
                exit_code=-1,
                stdout="",
                stderr=f"Test command timed out after {self.timeout_seconds} seconds.",
                duration_seconds=round(duration, 3),
                passed=False,
                status=TestStatus.BLOCKED,
                reason=f"Execution exceeded timeout ceiling ({self.timeout_seconds}s).",
            )
        except FileNotFoundError as e:
            duration = time.perf_counter() - start_t
            return TestResult(
                command=cmd,
                exit_code=-1,
                stdout="",
                stderr=f"Command binary not found: {str(e)}",
                duration_seconds=round(duration, 3),
                passed=False,
                status=TestStatus.BLOCKED,
                reason=f"Required executable is not installed or not in PATH: {str(e)}",
            )
        except Exception as e:
            duration = time.perf_counter() - start_t
            return TestResult(
                command=cmd,
                exit_code=-1,
                stdout="",
                stderr=f"Subprocess execution error: {str(e)}",
                duration_seconds=round(duration, 3),
                passed=False,
                status=TestStatus.BLOCKED,
                reason=f"Subprocess launch failed: {str(e)}",
            )

    def execute_all(self, explicit_command: Optional[str] = None) -> List[TestResult]:
        """Discovers and runs test commands.
        
        If no test infrastructure is found, returns a synthetic TestResult with NOT_AVAILABLE status.
        Never fabricates a successful result.
        """
        results: List[TestResult] = []

        if explicit_command:
            commands = [(explicit_command, "explicit_cli_command")]
        else:
            commands = self.discover_test_commands()

        if not commands:
            # Genuine honest reporting: NO test infrastructure available
            results.append(
                TestResult(
                    command="<none>",
                    exit_code=0,
                    stdout="",
                    stderr="No test runner, test directory (tests/), or package test scripts found.",
                    duration_seconds=0.0,
                    passed=False,
                    status=TestStatus.NOT_AVAILABLE,
                    reason="No test infrastructure discovered in workspace.",
                )
            )
            return results

        for cmd, source in commands:
            res = self.run_command(cmd, source)
            results.append(res)

        return results
