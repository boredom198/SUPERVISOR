# Agent Operating Protocol: SUPERVISOR System

## Core Rule
A task is never complete merely because code has been written. It is complete ONLY when SUPERVISOR has verified the implementation or transparently reported blockers/retry limits.

## Agent Lifecycle Responsibilities
1. Implement requested changes cleanly, adding tests and documentation where appropriate.
2. Trigger SUPERVISOR verification using `python -m supervisor review` or the `/supervisor` skill.
3. Automatically repair any material issues flagged by SUPERVISOR without waiting for user instruction.
4. Preserve all unrelated user changes, project conventions, and working tests.
5. Verify repairs using `python -m supervisor verify`.
6. Repeat until PASSED or retry limit is reached.

