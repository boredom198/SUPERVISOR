# Antigravity Rules: SUPERVISOR Verification Protocol

This rule is active for all agents operating within this repository.

1. **Verification Gate**: Do not conclude any coding task without running SUPERVISOR review (`python -m supervisor review`).
2. **Immediate Autonomous Repair**: When findings with severity CRITICAL, HIGH, or MEDIUM are identified, immediately execute fixes on the codebase. Do not require user confirmation.
3. **Preserve Unrelated Work**: When applying fixes, do not overwrite or modify unrelated user changes or introduce regressions.
4. **Verified Completion**: Only report completion once verification passes with zero material unresolved findings.

