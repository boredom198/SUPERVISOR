# Antigravity Workspace Instructions: SUPERVISOR Enforcement

## Core Mandate
> **A coding task is not complete merely because the AI agent has finished writing code. It is complete only after SUPERVISOR has reviewed the implementation and verified the result, or has transparently reported why verification could not be completed.**

## Mandatory Execution Protocol for Coding Agents
Whenever you implement, modify, or repair code in this workspace:

1. **Perform Implementation**: Write high-quality, tested, secure code satisfying the user's prompt.
2. **Never Terminate Without Verification**:
   - Before considering your turn or task complete, run `python -m supervisor review`.
   - Alternatively, use the `/supervisor` slash command or skill.
3. **Automatic Defect Repair (No User Prompt Needed)**:
   - If SUPERVISOR reports any material defects (Critical, High, Medium, or failing tests), you MUST immediately proceed to repairing the code.
   - **Do NOT ask the user "Would you like me to fix these issues?" or wait for another prompt.**
   - Apply the recommended corrections directly to the files.
4. **Re-verify After Repairs**:
   - Run `python -m supervisor verify`.
   - Ensure previous findings are resolved and no regressions were introduced.
5. **Completion Reporting**:
   - Only describe the task as complete when SUPERVISOR verification has PASSED.
   - If stopped by retry limits or external blockers, state the unresolved issues and incomplete verification clearly and honestly.
