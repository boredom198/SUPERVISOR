# SUPERVISOR 9-Stage Lifecycle Specification

This document details the exact execution semantics of the 9 stages of SUPERVISOR.

## Stage 1: Receive the Original Prompt
- The user gives the AI coding agent a task.
- The original prompt and its extracted requirements are saved in `.supervisor/state.json` (`TaskSpec`).
- The task requirements remain immutable context across all review and repair iterations.

## Stage 2: Implement
- The coding agent performs the requested work.
- It creates or modifies files, updates dependencies, runs tests, and adjusts configuration.

## Stage 3: Trigger SUPERVISOR Automatically
- As the coding agent completes its turn, the Antigravity `Stop` lifecycle hook fires (`.agents/hooks.json`).
- If in an environment without active hooks, persistent instructions in `GEMINI.md` / `AGENTS.md` instruct the agent to run `python -m supervisor review` before claiming task completion.

## Stage 4: Review the Implementation
- SUPERVISOR inspects git status and diffs against the requirements.
- Performs AST parsing for syntax errors and unsafe patterns (`eval`, `exec`, `shell=True`).
- Detects silent exception swallowing (`except: pass`).
- Scans for leaked credentials and private keys.
- Discovers and executes automated test suites (`pytest`, `unittest`, `npm test`).
- Analyzes requirement coverage and placeholder implementations (`NotImplementedError`).

## Stage 5: Generate a Detailed Report
- Produces Markdown and JSON reports in `.supervisor/reports/report_cycle_<N>.md`.
- Distinguishes confirmed defects from suspected concerns and recommendations.
- Every finding includes: ID, Severity, Category, File & Location, Description, Evidence, Why it matters, Recommended correction, Verification method, and Status.

## Stage 6: Automatically Send Findings to Coding Agent
- Actionable repair directives are generated in `.supervisor/repair_directives.md`.
- In hook mode, the `Stop` hook returns `{"decision": "continue", "reason": directives}`, resuming the agent immediately with instructions to repair.
- The user is not required to prompt the agent to fix the issues.

## Stage 7: Verify the Corrections
- SUPERVISOR reviews the modified workspace.
- Re-checks each previously identified finding.
- Checks for newly introduced regressions.
- Reruns automated test suites.
- Requires concrete verification evidence before marking any finding `RESOLVED`.

## Stage 8: Repeat When Necessary
- If material findings remain, the cycle repeats.
- Loop control: Configurable retry limit (`max_retries`, default: 3).
- If `cycle >= max_retries`, transitions to `RETRY_LIMIT_REACHED` to prevent endless loops.

## Stage 9: Complete the Original Task
- Task is marked `PASSED` only if verified by empirical evidence.
- If stopped by retry limits or blockers, transparently reports unresolved issues and incomplete verification.
