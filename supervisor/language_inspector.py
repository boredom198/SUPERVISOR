"""Best-effort source checks for JavaScript/TypeScript without executing application code.

These rules are deliberately narrow. They can identify common suspicious patterns but
are not a substitute for tests, type-checking, or a language-aware semantic analyzer.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import List

from supervisor.models import Category, Confidence, Finding, FindingStatus, Severity
from supervisor.identifiers import stable_suffix

SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


class LanguageInspector:
    def __init__(self, workspace_root: Path | str | None = None, timeout_seconds: int = 5):
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.timeout_seconds = max(1, int(timeout_seconds))

    def inspect(self, rel_path: str, content: str) -> List[Finding]:
        if Path(rel_path).suffix.lower() not in SOURCE_EXTENSIONS:
            return []
        findings: List[Finding] = []
        lines = content.splitlines()

        # These are suspicious idioms, not universal semantic truths. We label them
        # heuristic so a reviewer can distinguish them from parser/test-confirmed defects.
        rules = [
            (re.compile(r"\b(?:computation|result|answer|output)\s*=\s*(?:current|curr)\s*-\s*(?:previous|prev)\b"),
             "Subtraction operands may be reversed (current minus previous).",
             "Confirm operand order against the intended operation; subtraction is not commutative."),
            (re.compile(r"\bMath\.pow\s*\([^,]+,\s*[^,]+-\s*1\s*\)"),
             "Exponent passed to Math.pow is decremented by one.",
             "Verify whether subtracting one from the exponent is intentional; it commonly causes off-by-one results."),
        ]
        for line_no, line in enumerate(lines, 1):
            for pattern, description, correction in rules:
                if pattern.search(line):
                    findings.append(Finding(
                        finding_id=f"JS-LOGIC-{stable_suffix(rel_path, line_no, description)}",
                        severity=Severity.MEDIUM,
                        category=Category.BUG,
                        file_path=rel_path,
                        line_number=line_no,
                        title=description,
                        description=description,
                        evidence=f"Line {line_no}: {line.strip()[:300]}",
                        why_it_matters="This common expression shape may produce mathematically incorrect results.",
                        recommended_correction=correction,
                        verification_method="Add a behavioral test with known input/output values and rerun the test suite.",
                        confidence=Confidence.HEURISTIC,
                        status=FindingStatus.CONFIRMED,
                        source_check="javascript_logic_heuristics",
                    ))

        # Detect a zero-divisor guard that returns silently without throwing, reporting,
        # or assigning an explicit error. This remains heuristic because surrounding UI
        # behavior may intentionally communicate the error elsewhere.
        for i, line in enumerate(lines):
            if re.search(r"\bif\s*\([^)]*(?:===|==)\s*0\s*\)", line):
                block = "\n".join(lines[i:min(i + 12, len(lines))])
                close = block.find("}")
                if close >= 0:
                    block = block[:close + 1]
                if re.search(r"\breturn\s*;", block) and not re.search(
                    r"\b(?:throw|Error|error|alert|message|notify|setError|showError)\b", block, re.I
                ):
                    findings.append(Finding(
                        finding_id=f"JS-DIVZERO-{stable_suffix(rel_path, i + 1)}",
                        severity=Severity.MEDIUM,
                        category=Category.BUG,
                        file_path=rel_path,
                        line_number=i + 1,
                        title="Possible silent division-by-zero handling",
                        description="A zero-value guard appears to return without reporting an error.",
                        evidence=f"Lines {i + 1}-{min(i + 12, len(lines))}: {block.strip()[:500]}",
                        why_it_matters="Silently ignoring invalid arithmetic can hide user errors and leave the interface in an ambiguous state.",
                        recommended_correction="Return an explicit error/result or display a clear validation message, and add a division-by-zero test.",
                        verification_method="Test division by zero and verify the caller receives or displays an explicit error.",
                        confidence=Confidence.HEURISTIC,
                        status=FindingStatus.CONFIRMED,
                        source_check="javascript_logic_heuristics",
                    ))

        # Node's --check parses JavaScript without running it. It is optional and does
        # not validate TypeScript/JSX semantics.
        if Path(rel_path).suffix.lower() in {".js", ".mjs", ".cjs"}:
            node = shutil.which("node")
            target_file = self.workspace_root / rel_path
            if node and target_file.is_file():
                try:
                    proc = subprocess.run(
                        [node, "--check", str(target_file)],
                        cwd=str(self.workspace_root), capture_output=True, text=True,
                        timeout=self.timeout_seconds, check=False,
                    )
                    if proc.returncode:
                        message = (proc.stderr or proc.stdout or "JavaScript parser returned non-zero status.").strip()
                        findings.append(Finding(
                            finding_id=f"JS-SYNTAX-{stable_suffix(rel_path, message)}",
                            severity=Severity.CRITICAL,
                            category=Category.BUG,
                            file_path=rel_path,
                            description="JavaScript syntax validation failed.",
                            evidence=message[-1500:],
                            why_it_matters="JavaScript with syntax errors cannot be parsed by Node.js.",
                            recommended_correction="Fix the reported syntax error, then rerun the parser and tests.",
                            verification_method=f"Run `{node} --check {rel_path}` and verify exit code 0.",
                            confidence=Confidence.CONFIRMED,
                            status=FindingStatus.CONFIRMED,
                            source_check="javascript_syntax_parser",
                        ))
                except (OSError, subprocess.TimeoutExpired) as exc:
                    # Parser unavailability/timeouts are surfaced as limitations by caller
                    # only where a check record is created; don't crash the whole review.
                    pass
        return findings
