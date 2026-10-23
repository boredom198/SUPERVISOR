# Antigravity Lifecycle Hooks & Execution Interception Research

## 1. Executive Definition
In Google Antigravity, **Lifecycle Hooks** are external command-line handlers configured in `.agents/hooks.json` (workspace) or `~/.gemini/config/hooks.json` (global). Hooks execute synchronously at defined boundaries in the agent loop, reading contextual state via standard input (`stdin`) as JSON and returning execution directives via standard output (`stdout`) as JSON.

- **Primary Documentation Source**: `https://antigravity.google/docs/hooks`
- **Internal Specification Reference**: Built-in skill `agy-customizations` (`builtin/skills/agy-customizations/docs/hooks.md`)

---

## 2. Event Catalog & Structural Schema

Antigravity distinguishes between **Tool-Scoped Events** (which require a tool name regular expression `matcher` and a nested `hooks` array) and **Global Lifecycle Events** (which take a flat array of command objects directly).

```json
{
  "supervisor-hooks": {
    "enabled": true,
    "PreInvocation": [
      { "type": "command", "command": "python -m supervisor.hooks.pre_invocation" }
    ],
    "PostInvocation": [
      { "type": "command", "command": "python -m supervisor.hooks.post_invocation" }
    ],
    "PreToolUse": [
      {
        "matcher": "run_command|write_to_file",
        "hooks": [
          { "type": "command", "command": "python -m supervisor.hooks.pre_tool" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "run_command|write_to_file",
        "hooks": [
          { "type": "command", "command": "python -m supervisor.hooks.post_tool" }
        ]
      }
    ],
    "Stop": [
      { "type": "command", "command": "python -m supervisor.hooks.stop_hook", "timeout": 60 }
    ]
  }
}
```

---

## 3. Comprehensive Lifecycle Hook Property Matrix

| Event Name | Trigger Timing | Scope | Input Payload (`stdin`) | Output Contract (`stdout`) | Blocking Behavior | Agent Interaction | Completion Control | Evidence & Section | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`SessionStart`** | When a conversation session initializes. | Session | Common metadata (`conversationId`, `workspacePaths`, `modelName`). | None (empty JSON `{}`). | Non-blocking. | Injects initial environment variables. | Cannot block completion. | `docs/hooks#SessionStart` | Documented |
| **`PreInvocation`** | Immediately before model inference is called. | Invocation turn | `invocationNum`, `initialNumSteps`, common metadata. | `{"injectSteps": [{"ephemeralMessage": "...", "userMessage": "..."}]}` | Can inject transient system/user steps before the model reasons. | Injects instructions into active prompt context. | Cannot cancel turn. | `docs/hooks#PreInvocation` | Documented |
| **`PostInvocation`** | Immediately after tool calls finish executing. | Invocation turn | `invocationNum`, `initialNumSteps`, common metadata. | `{"injectSteps": [...], "terminationBehavior": "force_continue" \| "terminate"}` | Can force loop to continue or terminate early. | Injects steps and forces continuation. | Can prevent loop termination. | `docs/hooks#PostInvocation` | Documented |
| **`PreToolUse`** | Before a tool step executes. | Individual tool call | `toolCall: {name, args}`, `stepIdx`, common metadata. | `{"decision": "allow" \| "deny" \| "ask", "reason": "...", "overwrite": {...}}` | **Hard block**: `decision: "deny"` stops tool execution immediately. | Injects refusal message to agent. | Gating only; does not intercept completion. | `docs/hooks#PreToolUse` | Documented & Tested |
| **`PostToolUse`** | After a tool step completes. | Individual tool call | `stepIdx`, `error` (if failed), common metadata. | Empty JSON `{}`. | Non-blocking. | Indirectly via logs/state. | Cannot block completion. | `docs/hooks#PostToolUse` | Documented & Tested |
| **`Stop`** | When the agent execution loop terminates. | Turn conclusion | `executionNum`, `terminationReason`, `fullyIdle`, common metadata. | `{"decision": "continue" \| "stop", "reason": "..."}` | **Hard interception**: `decision: "continue"` halts termination and re-enters the loop. | Injects `reason` string as a system message to the agent. | **Directly prevents premature task completion.** | `docs/hooks#Stop` | **Documented & Tested** |

---

## 4. Deep Dive: The `Stop` Hook Contract

The `Stop` event is the single most critical hook for SUPERVISOR. It is the only documented mechanism capable of intercepting task termination and deterministically re-opening the agent's execution loop.

### A. Input Contract (`stdin` to handler):
```json
{
  "executionNum": 1,
  "terminationReason": "model_stop",
  "error": "",
  "fullyIdle": true,
  "conversationId": "44294bfd-9a99-4312-b2c4-f945b5134b60",
  "workspacePaths": ["c:\\Users\\DELL\\Downloads\\SUPERVISOR"],
  "transcriptPath": "C:\\Users\\DELL\\.gemini\\antigravity\\brain\\...\\transcript.jsonl",
  "artifactDirectoryPath": "C:\\Users\\DELL\\.gemini\\antigravity\\brain\\...\\artifacts",
  "modelName": "auto"
}
```

#### Key Fields:
* **`terminationReason`**: Identifies *why* the loop is stopping:
  * `"model_stop"`: The agent completed its work and chose not to call further tools.
  * `"user_cancelled"`: The user clicked Stop.
  * `"max_steps_exceeded"`: The engine step limit was reached.
  * `"error"`: Fatal execution crash.
* **`fullyIdle`**: Boolean indicating whether background asynchronous tasks have finished.

### B. Output Contract (`stdout` from handler):
To allow the agent to stop (e.g. when verification passes):
```json
{
  "decision": "allow",
  "reason": "SUPERVISOR verification PASSED."
}
```

To **block completion and force autonomous repair**:
```json
{
  "decision": "continue",
  "reason": "## [SUPERVISOR AUTO-REPAIR DIRECTIVE]\nDefects identified:\n1. SYNTAX-001: SyntaxError in auth.py\nPlease correct these issues immediately."
}
```

---

## 5. Technical Guarantees and Risk Analysis

### What Lifecycle Hooks CAN Guarantee:
1. **Deterministic Execution Interception**: When `.agents/hooks.json` is active, the agent cannot conclude a `model_stop` without executing the `Stop` hook script.
2. **Autonomous Continuation without User Prompts**: Returning `"decision": "continue"` causes Antigravity to inject the `reason` payload directly into the active conversation and start a new reasoning turn for the coding agent.
3. **Loop Defense**: Because the hook script is external Python code, it can enforce hard retry ceilings (`max_retries = 3`) and break infinite ping-pong loops by emitting `"decision": "allow"` once limits are reached.

### Limitations & Edge Cases:
* **Host Compatibility**: Hooks require an Antigravity surface that implements the hook runner. If Antigravity runs in a sandboxed mode where external process execution is disabled, hooks will fail or timeout.
* **Synchronous Execution**: Hooks run synchronously and block the event loop. The hook execution timeout defaults to 30 seconds (configurable up to 120s). The review process must execute rapidly (using AST and targeted tests) rather than running unbounded test suites.
