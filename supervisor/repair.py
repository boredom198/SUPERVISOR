"""Repair handoff generator and agent directive formatter (Stage 6)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from supervisor.models import Finding, FindingStatus, ReviewStatus, Severity, SupervisorReport
from supervisor.security import SecurityInspector


class RepairHandoff:
    """Constructs unambiguous, actionable repair directives for the coding agent."""

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    def generate_repair_prompt(
        self, report: SupervisorReport, original_task: Optional[str] = None
    ) -> str:
        """Formats actionable instructions for the AI coding agent without requiring user intervention."""
        material_findings = [
            f
            for f in report.findings
            if f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
            and f.status in {FindingStatus.CONFIRMED, FindingStatus.UNRESOLVED}
        ]

        if not material_findings:
            if report.overall_status == ReviewStatus.PASSED:
                return "No material findings requiring repair. All executed checks passed; untested behavior is not proven correct."
            return "No material findings requiring automatic repair, but verification is incomplete or blocked. Review the report limitations and add/run relevant tests before treating the task as complete."

        lines = []
        lines.append("## [SUPERVISOR AUTO-REPAIR DIRECTIVE]")
        lines.append(
            f"**Cycle**: {report.cycle} | **Material Defects Found**: {len(material_findings)}"
        )
        lines.append(
            "SUPERVISOR has reviewed your implementation and identified defects that MUST be corrected before the task is complete."
        )
        lines.append(
            "**Do NOT wait for the user to prompt you. Proceed immediately to repairing the issues below.**"
        )
        lines.append("")

        if original_task:
            lines.append("### Original Task Context")
            lines.append(f"> {original_task.strip()[:400]}")
            lines.append("")

        lines.append("### Actionable Defect List")
        for i, f in enumerate(material_findings, start=1):
            loc = f.file_path + (f":{f.line_number}" if f.line_number else "")
            lines.append(f"#### {i}. `{f.finding_id}`: {f.description}")
            lines.append(f"- **Severity**: {f.severity.value} ({f.category.value})")
            lines.append(f"- **File & Location**: `{loc}`")
            lines.append(f"- **Problem Evidence**:\n```\n{f.evidence}\n```")
            lines.append(f"- **Recommended Correction**: {f.recommended_correction}")
            lines.append(f"- **Verification Target**: {f.verification_method}")
            lines.append("")

        lines.append("### Mandatory Repair Guidelines:")
        lines.append("1. Modify the necessary files to address every finding above.")
        lines.append("2. Preserve all existing unrelated user work and project conventions.")
        lines.append("3. Do not introduce new syntax errors, stub code, or regressions.")
        lines.append("4. Once repairs are completed, execute the relevant tests/verification steps.")
        lines.append("")

        directive_text = SecurityInspector.redact_secret("\n".join(lines))

        # Save to .supervisor directory
        out_file = self.workspace_root / ".supervisor" / "repair_directives.md"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(directive_text, encoding="utf-8")

        return directive_text

