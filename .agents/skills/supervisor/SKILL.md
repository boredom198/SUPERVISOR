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

## 1. Quick Invocation & Slash Command (`/supervisor`)

The `/supervisor` command initiates the autonomous supervision loop.

* **Full Review & Auto-Repair Handoff** (Default):
  ```powershell
  python -m supervisor review
  ```
  *or run via skill launcher*:
  ```powershell
  python .agents/skills/supervisor/scripts/run_supervisor.py review
  ```
* **Report-Only Mode** (Generates structured report without initiating repair handoff):
  ```powershell
  python -m supervisor review --report-only
  ```
* **Post-Repair Verification**:
  ```powershell
  python -m supervisor verify
  ```
* **Lifecycle Status Check**:
  ```powershell
  python -m supervisor status
  ```
* **Reset Session State**:
  ```powershell
  python -m supervisor reset
  ```

--------------------------------------------------------------------------------

## 2. The 9-Stage Supervision Lifecycle

When completing or reviewing any task in this workspace, follow the required lifecycle:

1. **Stage 1 (Receive Prompt)**: The original prompt and requirements are captured and preserved in `.supervisor/state.json`.
2. **Stage 2 (Implement)**: The coding agent performs the task, writing code, tests, and documentation.
3. **Stage 3 (Trigger Automatically)**: When the agent attempts to stop (`terminationReason == "model_stop"`), the Antigravity `Stop` lifecycle hook in `.agents/hooks.json` intercepts termination and executes `python -m supervisor.hooks.stop_hook`.
4. **Stage 4 (Review)**: `ReviewEngine` runs deterministic Python AST checks, config validation (JSON/TOML), secret scanning, placeholder scanning, requirement verification, and test execution.
5. **Stage 5 (Report)**: Generates structured Markdown and JSON reports in `.supervisor/reports/` with explicit Finding IDs, Severities, Evidence, and Confidence ratings.
6. **Stage 6 (Auto-Handoff)**: If material issues exist and retries remain, the hook returns `{"decision": "continue", "reason": "<directives>"}`. Antigravity injects the directives into the agent turn, and the coding agent repairs the issues immediately without waiting for user prompts.
7. **Stage 7 (Verify)**: After repairs, `VerificationEngine` checks that previously identified findings are resolved, no new regressions were introduced, and test suites pass.
8. **Stage 8 (Repeat)**: The loop continues until all material issues are resolved or the configurable retry limit (default: 3) is reached.
9. **Stage 9 (Completion)**: Task is marked `PASSED` with evidence, or `RETRY_LIMIT_REACHED` with honest disclosure of remaining defects.

--------------------------------------------------------------------------------

## 3. Platform Capabilities & Limitations

### Supported in Antigravity:
* **Stop Hook Interception**: `.agents/hooks.json` intercepting `model_stop` termination.
* **Autonomous Continuation**: Returning `{"decision": "continue", "reason": "<directives>"}` halts turn conclusion and injects directives into the agent context.
* **Secret Redaction**: All secrets detected by `SecurityInspector` are masked (`[REDACTED]` or `****`) before injection into hook output or reports.
* **Non-Destructive Execution**: The engine only reads files and executes discovered test commands with strict timeouts.

### Limitations & Edge Cases:
* **Synchronous Hook Execution**: Hooks execute synchronously. Test suites that exceed the configured hook timeout are capped to prevent hanging.
* **Missing Test Suites**: If no tests exist in the workspace, SUPERVISOR reports `TestStatus.NOT_AVAILABLE` and `ReviewStatus.VERIFICATION_INCOMPLETE` without fabricating passes.
* **Unchanged Code / No Progress**: If the agent makes zero edits between repair cycles, SUPERVISOR advances the retry counter toward the limit to prevent infinite loops.

--------------------------------------------------------------------------------

## 4. Manual Verification Procedure

To manually test the full supervision and repair loop:

1. Introduce a deliberate defect in a workspace file (e.g. `eval("1+1")` in a Python module).
2. Run `python -m supervisor review` in your terminal.
3. Observe that SUPERVISOR flags `SEC-EXEC` or `SYNTAX`, produces `.supervisor/repair_directives.md`, and exits with returncode `1`.
4. Replace `eval(...)` with safe code (e.g. `ast.literal_eval(...)`).
5. Run `python -m supervisor verify`.
6. Observe that SUPERVISOR marks the finding as `RESOLVED`, confirms zero regressions, and exits with returncode `0` (`PASSED`).

--------------------------------------------------------------------------------

## 5. References & Documentation

* [Lifecycle Specification](./references/lifecycle.md)
* [Report Schema](./references/report_schema.md)
* [State Machine & Loop Control](./references/state_machine.md)
* [Antigravity Hooks Guide](../../../docs/antigravity/hooks-and-lifecycle.md)
