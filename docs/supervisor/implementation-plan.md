# SUPERVISOR Phased Implementation Plan (Prompts 4 – 10)

## 1. Roadmap Overview

This plan defines the sequential execution stages for constructing, integrating, and hardening SUPERVISOR from Prompt 4 through Prompt 10. Each prompt delivers a discrete, independently testable milestone with explicit acceptance criteria and dependencies.

```mermaid
gantt
    title SUPERVISOR Implementation Roadmap
    dateFormat  X
    axisFormat %d

    section Core Engine
    Prompt 4: Shared Core Review Engine & Adapters       :active, p4, 0, 1
    Prompt 5: State Machine, Storage & Reports           :p5, 1, 2

    section Antigravity Production
    Prompt 6: Production Stop Hook & Gating              :p6, 2, 3
    Prompt 7: Antigravity Skill & /supervisor Slash Cmd  :p7, 3, 4
    Prompt 8: Independent AI Auditor Profile             :p8, 4, 5

    section Cross-Platform & Hardening
    Prompt 9: Claude Code, Codex & CI Adapters           :p9, 5, 6
    Prompt 10: E2E Benchmarks, Verification & Delivery   :p10, 6, 7
```

---

## 2. Stage Breakdown & Acceptance Criteria

### Prompt 4: Shared Core Review Engine & Multi-Language Adapters
* **Focus**: Build the zero-dependency deterministic inspection core (`supervisor/engine/`).
* **Deliverables**:
  - `context.py`: Git diff extractor and prompt requirement parser.
  - `inspector.py`: Python AST analyzer, JSON/TOML validator, dangerous function scanner (`eval`, `exec`, `shell=True`), empty `except: pass` detector, and context-aware `NotImplementedError` checker.
  - `security.py`: High-confidence secret detection and redaction engine.
  - `runner.py`: Subprocess test runner with strict timeouts and buffer truncation.
* **Dependencies**: None (self-contained standard library Python).
* **Acceptance Criteria**:
  - [ ] 100% test coverage across AST, security, and test-running modules.
  - [ ] Detected secrets are consistently redacted in outputs (`[REDACTED]`).
  - [ ] Subprocess execution is safe (`shell=False`, argument splitting via `shlex`).

---

### Prompt 5: State Machine, Atomic Storage & Report Generation
* **Focus**: Implement session lifecycle tracking, process concurrency locks, and structured report outputs.
* **Deliverables**:
  - `models.py`: Strongly-typed dataclasses for `Finding`, `SupervisorReport`, `TaskSpec`, `LifecycleState`.
  - `state.py`: Atomic state manager with advisory file locking (`.supervisor/lock`) and crash recovery.
  - `reporter.py`: Markdown and JSON report generator conforming to Stage 5 schema.
  - `repair.py`: Actionable agent repair directive formatter.
* **Dependencies**: Prompt 4 Core Engine.
* **Acceptance Criteria**:
  - [ ] Concurrent file access produces zero race conditions or corrupted JSON files.
  - [ ] Cycle advancement correctly records state transitions from `IMPLEMENTING` to `PASSED` or `RETRY_LIMIT_REACHED`.
  - [ ] Reports distinguish confirmed defects from recommendations and un-run tests.

---

### Prompt 6: Antigravity Production Hook & Completion Gating
* **Focus**: Implement the production-grade `Stop` lifecycle hook adapter for Google Antigravity.
* **Deliverables**:
  - `.agents/hooks.json`: Production configuration with tuned timeouts (30s).
  - `supervisor/hooks/stop_hook.py`: Hook handler evaluating `model_stop`, querying state, running fast-path checks, and returning `{"decision": "continue", "reason": "..."}` or `{"decision": "allow"}`.
* **Dependencies**: Prompt 4 & Prompt 5.
* **Acceptance Criteria**:
  - [ ] Attempted agent completion with unverified code triggers the `Stop` hook.
  - [ ] Defects trigger `decision: "continue"`, resuming the agent automatically with repair directives without user prompt.
  - [ ] Hook completes within < 4 seconds, well below the 30s timeout ceiling.

---

### Prompt 7: Antigravity Skill, Slash Command `/supervisor` & Progressive Assets
* **Focus**: Build the native user-facing skill interface.
* **Deliverables**:
  - `.agents/skills/supervisor/SKILL.md`: Frontmatter with `name: supervisor`, comprehensive runbook, and progressive disclosure links.
  - `.agents/skills/supervisor/scripts/run_supervisor.py`: Executable launcher.
  - `.agents/skills/supervisor/references/`: Lifecycle, report schema, and state machine subdocs.
  - Workspace rules (`GEMINI.md`, `AGENTS.md`) finalized.
* **Dependencies**: Prompt 6.
* **Acceptance Criteria**:
  - [ ] Typing `/supervisor` in chat canvas or CLI loads the skill and executes review.
  - [ ] Supports `--report-only` and `--deep` flags.
  - [ ] Progressive disclosure ensures zero token overhead when skill is idle.

---

### Prompt 8: Independent AI Auditor Profile (`supervisor-auditor`)
* **Focus**: Implement Mode 2 deep review using native Antigravity subagent orchestration.
* **Deliverables**:
  - `supervisor/auditor/`: Auditor prompt profile and schema validator.
  - Subagent orchestrator calling `define_subagent` and `invoke_subagent` with `Workspace: "inherit"`.
  - Finding reconciler merging deterministic findings with AI auditor observations.
* **Dependencies**: Prompt 7.
* **Acceptance Criteria**:
  - [ ] Complex diffs (> 300 lines or security paths) trigger auditor subagent.
  - [ ] Auditor operates with zero write permissions (read-only).
  - [ ] Auditor findings conform strictly to `Finding` schema with file and line citations.

---

### Prompt 9: Cross-Platform Adapters & CLI Hardening
* **Focus**: Deliver adapters for Claude Code, OpenAI Codex, and standalone CI.
* **Deliverables**:
  - `supervisor/adapters/claude_code/`: Tool wrappers and `.clauderc` integration.
  - `supervisor/adapters/codex/`: Python agent loop wrapper.
  - `supervisor/adapters/ci/`: GitHub Actions workflow template and step summary formatter.
  - CLI polish: `supervisor review`, `verify`, `status`, `reset`.
* **Dependencies**: Prompt 5.
* **Acceptance Criteria**:
  - [ ] CLI runs identically on Windows, Linux, and macOS.
  - [ ] Standalone CI returns exit code 0 on clean code, 1 on defects, 2 on blockers.

---

### Prompt 10: End-to-End Verification, Benchmarks & Final Delivery
* **Focus**: Comprehensive verification of the entire system under realistic adversarial conditions.
* **Deliverables**:
  - Full end-to-end simulation test suite (syntax errors, security flaws, regressions, infinite loop traps, timeout handling).
  - Performance and token benchmark reports.
  - Finalized production documentation and user guide.
* **Dependencies**: All prior prompts.
* **Acceptance Criteria**:
  - [ ] Zero unverified completions across all test scenarios.
  - [ ] Autonomous multi-turn repair loop runs from Prompt to Fix to Verification Pass without human intervention.

