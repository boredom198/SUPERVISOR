# SUPERVISOR Architecture Decision Records (ADR)

## ADR-001: Adoption of the Tri-Layer Hybrid Architecture

### Context
We evaluated whether supervision could be achieved exclusively through persistent instructions (`AGENTS.md`), exclusively through an on-demand skill (`SKILL.md`), or exclusively through a background daemon.

### Decision
Adopt the **Tri-Layer Hybrid Architecture**:
* **Layer A (Rules)**: Behavioral framing and protocol compliance.
* **Layer B (Stop Hook)**: Deterministic physical completion gating.
* **Layer C (Skill + CLI)**: Reusable analytical inspection engine and manual interface.

### Rationale & Trade-offs
* *Pure Rules*: Rejected because rules guide model probabilities but cannot physically intercept `model_stop` or force loop continuation if the model hallucinates completion.
* *Pure Skill*: Rejected because skills require invocation; they cannot self-schedule at turn completion or block premature final responses.
* *Standalone Daemon Service*: Rejected as introducing unnecessary infrastructure, socket management overhead, and background process fragility.
* *Consequence*: Requires host environments to support lifecycle hooks for 100% deterministic gating, falling back to rule guidance where hooks are unavailable.

---

## ADR-002: Zero-Dependency Deterministic Core Engine

### Context
Many AI safety and code review tools require external LLM API keys (e.g. OpenAI, Anthropic, Gemini API) to grade code, incurring financial cost, token usage, network latency, and non-deterministic evaluations.

### Decision
Implement the shared core review engine using **100% standard library Python** (AST parsing, regex pattern matching, subprocess test execution, atomic file handling).

### Rationale & Trade-offs
* *Benefits*: Free, zero token cost, ultra-fast (< 1s), 100% deterministic and repeatable, works in air-gapped or offline environments.
* *Trade-off*: Cannot perform high-level semantic reasoning or architectural intent analysis on its own (addressed by ADR-005).

---

## ADR-003: Intercepting Completion via the `Stop` Lifecycle Hook

### Context
Antigravity provides multiple lifecycle hooks (`SessionStart`, `PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, `Stop`). We needed to identify the exact event capable of intercepting task completion and restarting the agent turn.

### Decision
Standardize on the **`Stop` event** (`terminationReason: "model_stop"`) returning `{"decision": "continue", "reason": "<directives>"}` as the primary completion gatekeeper.

### Rationale & Trade-offs
* *Evaluated Alternatives*:
  * `PostToolUse`: Fires after every individual tool. Too noisy and premature; agent has not finished its implementation yet.
  * `PostInvocation`: Fires after model inference before tools run, lacking final tool outputs.
* *The `Stop` Hook*: Fires specifically when the agent decides it is done (`model_stop`). Returning `"continue"` re-enters the execution loop without user intervention, precisely matching Stage 3 & Stage 6 requirements.

---

## ADR-004: Decoupled Adapter Pattern for Cross-Platform Portability

### Context
While Antigravity is our first development target, SUPERVISOR must eventually run in Claude Code, OpenAI Codex harnesses, GitLab Duo CLI, and standalone CI pipelines.

### Decision
Decouple the engine into a shared core (`supervisor/`) and platform adapters (`supervisor/adapters/<platform>/`). The shared core contains zero references to Antigravity-specific schemas or concepts.

### Rationale & Trade-offs
* *Benefits*: Core engine code is written once and tested once. Adding support for Claude Code or Codex requires only authoring a thin adapter mapping to `ExecutionGatingContract` and `RepairDirectiveContract`.
* *Trade-off*: Requires maintaining strict interface contracts and writing abstraction shims.

---

## ADR-005: Dual-Mode Review (Deterministic vs. Deep Hybrid Audit)

### Context
Complex architectural refactoring or security-sensitive diffs benefit from semantic LLM review, but running an LLM auditor on every trivial typo fix is wasteful, slow, and expensive.

### Decision
Implement **Dual-Mode Review**:
* **Mode 1 (Deterministic)**: Default. Runs local static/AST checks and test runners (< 1s, 0 tokens).
* **Mode 2 (Deep Hybrid)**: Runs deterministic checks PLUS spawns the read-only `supervisor-auditor` subagent when diffs touch security paths, exceed 300 lines, or are explicitly requested via `/supervisor --deep`.

### Rationale & Trade-offs
* Balances speed and zero-cost operation for 80% of tasks with deep semantic review for the 20% of high-risk changes.

---

## ADR-006: Atomic Filesystem Storage (`.supervisor/`) over Databases

### Context
Session state, findings, and history must be persisted across agent turns, hooks, and subagents.

### Decision
Use local JSON files inside `.supervisor/` with advisory OS-level file locking (`.supervisor/lock`) and atomic replacement (`os.replace` via temp files).

### Rationale & Trade-offs
* *Rejected*: SQLite or client-server databases. They introduce connection pool locks, binary format issues across git branches, and external dependencies.
* *Atomic JSON*: Human-readable, transparent, git-ignorable, robust against crash corruption, and zero dependencies.

---

## ADR-007: Mandatory Secret Redaction in Findings & Reports

### Context
When the security scanner detects an embedded API key or password, including the unmasked credential in the finding report would leak sensitive secrets into git working trees and log files.

### Decision
The report generator and repair directive formatter **must redact** high-entropy matches, displaying only masked prefixes (e.g., `sk_live_********************`).

### Rationale & Trade-offs
* Guarantees that running SUPERVISOR does not compromise credentials or leak private keys to transcripts or artifacts.

---

## ADR-008: Two-Tiered Timeout Budgeting for Lifecycle Hooks

### Context
Antigravity hooks have a default 30-second execution timeout. Running a 5-minute integration test suite synchronously inside the hook causes host process termination.

### Decision
Partition checks into **Tier 1 (Synchronous In-Hook, < 5s)** for AST, secrets, and fast unit tests, and **Tier 2 (Deferred/Agent Execution)** for long integration suites, instructing the agent to execute them within the turn if needed.

### Rationale & Trade-offs
* Prevents hook timeouts and system crashes while ensuring no test is ever falsely marked `PASSED`.
