"""Structured report generation for SUPERVISOR in Markdown and JSON formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from supervisor.models import Finding, FindingStatus, Severity, SupervisorReport


class ReportGenerator:
    """Formats and writes structured SUPERVISOR reports."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.reports_dir = self.workspace_root / ".supervisor" / "reports"

    def to_markdown(self, report: SupervisorReport) -> str:
        """Renders report into GitHub-flavored Markdown following the Stage 5 specification."""
        lines = []
        lines.append(f"# SUPERVISOR Review Report (Cycle {report.cycle})")
        lines.append(f"**Report ID**: `{report.report_id}` | **Task ID**: `{report.task_id}` | **Status**: `{report.state.value}`")
        lines.append(f"**Generated**: `{report.created_at}`")
        lines.append("")

        # Summary box
        lines.append("## Executive Summary")
        lines.append(f"> {report.summary}")
        lines.append("")

        if report.blocker_reason:
            lines.append(f"> [!CAUTION]\n> **Verification Blocked**: {report.blocker_reason}\n")

        # Tests executed section
        lines.append("## Validation & Tests Executed")
        if not report.tests_run:
            lines.append("*(No automated test commands were configured or executed during this cycle.)*")
        else:
            lines.append("| Command | Result | Exit Code | Duration |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for t in report.tests_run:
                status_icon = "PASS" if t.passed else "FAIL"
                lines.append(f"| `{t.command}` | **{status_icon}** | `{t.exit_code}` | {t.duration_seconds}s |")
        lines.append("")

        # Findings Summary Table
        lines.append("## Findings Overview")
        if not report.findings:
            lines.append("> [!NOTE]\n> No findings were identified. Implementation passed verification.\n")
        else:
            lines.append("| Finding ID | Severity | Category | Status | File & Location | Description |")
            lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for f in report.findings:
                loc = f"{f.file_path}"
                if f.line_number:
                    loc += f":{f.line_number}"
                desc_short = f.description[:75] + ("..." if len(f.description) > 75 else "")
                lines.append(f"| `{f.finding_id}` | **{f.severity.value}** | `{f.category.value}` | `{f.status.value}` | `{loc}` | {desc_short} |")
            lines.append("")

            # Detailed Findings
            lines.append("## Detailed Findings")
            for f in report.findings:
                lines.append(f"### `{f.finding_id}`: {f.description}")
                lines.append(f"- **Severity**: `{f.severity.value}`")
                lines.append(f"- **Category**: `{f.category.value}`")
                lines.append(f"- **Current Status**: `{f.status.value}`")
                loc_full = f.file_path + (f":{f.line_number}" if f.line_number else "")
                lines.append(f"- **Affected Location**: `{loc_full}`")
                lines.append(f"- **Evidence**:\n```\n{f.evidence}\n```")
                lines.append(f"- **Why It Matters**: {f.why_it_matters}")
                lines.append(f"- **Recommended Correction**: {f.recommended_correction}")
                lines.append(f"- **Verification Method**: {f.verification_method}")
                lines.append("")

        return "\n".join(lines)

    def save_reports(
        self, report: SupervisorReport, custom_md_path: Optional[Path] = None
    ) -> TuplePath:
        """Persists Markdown and JSON reports to disk."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        json_path = self.reports_dir / f"report_cycle_{report.cycle}.json"
        latest_json_path = self.workspace_root / ".supervisor" / "latest_report.json"
        md_path = custom_md_path or (self.reports_dir / f"report_cycle_{report.cycle}.md")
        latest_md_path = self.workspace_root / ".supervisor" / "latest_report.md"

        md_content = self.to_markdown(report)
        json_content = json.dumps(report.to_dict(), indent=2)

        json_path.write_text(json_content, encoding="utf-8")
        latest_json_path.write_text(json_content, encoding="utf-8")
        md_path.write_text(md_content, encoding="utf-8")
        latest_md_path.write_text(md_content, encoding="utf-8")

        return TuplePath(md_path=md_path, json_path=json_path)


class TuplePath:
    def __init__(self, md_path: Path, json_path: Path):
        self.md_path = md_path
        self.json_path = json_path
