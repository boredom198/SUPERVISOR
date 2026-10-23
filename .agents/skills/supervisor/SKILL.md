---
name: supervisor
description: >-
  Reviews work produced by AI coding agents, identifies bugs, missing requirements, regressions,
  and security vulnerabilities, generates structured findings reports, and automatically initiates
  a repair cycle to fix identified defects before tasks are considered complete.
  Use when the user invokes /supervisor or when completing any coding implementation task.
---

# SUPERVISOR: AI Coding Supervision & Automatic Repair System

The central principle of this workspace is:
> **A coding task is not complete merely because the AI agent has finished writing code. It is complete only after SUPERVISOR has reviewed the implementation and verified the result, or has transparently reported why verification could not be completed.**

--------------------------------------------------------------------------------

## 1. Quick Invocation

- **Full Review & Auto-Repair Handoff** (Default):
  ```powershell
  python -m supervisor review
  ```
- **Report-Only Mode** (When user explicitly requests review without initiating repairs):
  ```powershell
  python -m supervisor review --report-only
  ```
- **Post-Repair Verification**:
  ```powershell
  python -m supervisor verify
  ```
- **Lifecycle Status Check**:
  ```powershell
  python -m supervisor status
  ```

--------------------------------------------------------------------------------

## 2. The 9-Stage Supervision Lifecycle

When performing or reviewing any task in this workspace, follow the required lifecycle:

1. **Stage 1 (Receive Prompt)**: Preserve original task requirements throughout implementation and verification.
2. **Stage 2 (Implement)**: Implement features, dependencies, files, and tests according to instructions.
3. **Stage 3 (Trigger Automatically)**: As the task reaches completion, SUPERVISOR triggers automatically via `.agents/hooks.json` on `Stop` or directly via this skill.
4. **Stage 4 (Review)**: SUPERVISOR analyzes workspace diffs, syntax/AST, security, missing requirements, and test suites.
5. **Stage 5 (Report)**: Detailed structured report generated in `.supervisor/reports/` with Finding IDs, Severities, Locations, and Evidence.
6. **Stage 6 (Auto-Handoff)**: Actionable defect list passed to the coding agent. **The coding agent must fix the defects immediately without waiting for user prompting.**
7. **Stage 7 (Verify)**: After repairs, SUPERVISOR re-checks all findings, verifies test passes, and checks for new regressions.
8. **Stage 8 (Repeat)**: Cycle repeats until all material issues are resolved or the configurable retry limit (default: 3) is reached.
9. **Stage 9 (Completion)**: Task is marked `PASSED` only if verified by evidence, or `RETRY_LIMIT_REACHED` with honest disclosure of remaining limitations.

--------------------------------------------------------------------------------

## 3. Subagent Supervision Pattern

When delegating tasks or running in multi-agent mode:
1. The Coding Agent performs the task in the workspace.
2. The Supervisor Subagent is invoked:
   ```json
   {
     "TypeName": "research",
     "Role": "Supervisor Auditor",
     "Prompt": "Run `python -m supervisor review` to audit the workspace changes against requirements. Report findings."
   }
   ```
3. If material findings exist, send them back to the coding agent via `send_message`.
4. Rerun verification with `python -m supervisor verify`.

--------------------------------------------------------------------------------

## 4. References & Documentation

- [Lifecycle Specification](./references/lifecycle.md)
- [Report Schema](./references/report_schema.md)
- [State Machine & Loop Control](./references/state_machine.md)
