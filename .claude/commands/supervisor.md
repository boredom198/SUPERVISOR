---
description: Run SUPERVISOR review and autonomous repair cycle on current workspace
---

# SUPERVISOR Review & Repair

Run the SUPERVISOR quality review and autonomous repair workflow:

1. Execute `python -m supervisor review` (or `python -m supervisor review --report-only` if report-only mode is requested).
2. If material defects (Critical, High, Medium, or failing tests) are identified:
   - Do NOT ask the user for confirmation.
   - Immediately apply the recommended corrections.
   - Preserve all unrelated code and project conventions.
3. Run `python -m supervisor verify` to verify the repairs and ensure zero regressions.
4. Conclude only when verification passes with exit code 0 or explicit retry limits are reached.

