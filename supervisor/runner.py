"""Test discovery and controlled execution runner for SUPERVISOR."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from supervisor.models import TestResult, TestStatus


class TestRunner:
    """Discovers project test suites and executes them with strict bounds, timeouts, and authorization controls."""

    def __init__(
        self,
        workspace_root: Optional[Path | str] = None,
        timeout_seconds: int = 30,
        max_output_chars: int = 4000,
        allow_repo_scripts: bool = True,
    ):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
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
            for item in self.workspace_root.glob("**/test_*.py"):
                if not any(part in str(item).split(os.sep) for part in (".venv", "venv", "node_modules", ".supervisor")):
                    has_test_files = True
                    break
        except OSError:
            pass

        if has_tests_dir:
            discovered.append(("python -m unittest discover -s tests", "python_unittest_discovery"))
        elif has_test_files:
            discovered.append(("python -m unittest discover -s .", "python_unittest_discovery"))

        # 2. JavaScript / Node.js Discovery
        pkg_json = self.workspace_root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {})
                if "test" in scripts:
                    test_script = scripts["test"]
                    # Skip default placeholder 'echo "Error: no test specified" && exit 1'
                    if "no test specified" not in test_script:
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
            proc = subprocess.run(
                args,
                shell=False,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            duration = time.perf_counter() - start_t
            passed = proc.returncode == 0
            if proc.returncode == 5 and "NO TESTS RAN" in (proc.stderr or ""):
                status = TestStatus.NOT_AVAILABLE
                passed = False
            else:
                status = TestStatus.PASSED if passed else TestStatus.FAILED

            stdout_capped = (proc.stdout or "")[-self.max_output_chars:]
            stderr_capped = (proc.stderr or "")[-self.max_output_chars:]

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
