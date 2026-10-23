# Antigravity Persistent Instructions & Rules Research

## 1. Executive Definition
In Google Antigravity, **Rules** are persistent guidelines, constraints, and operational protocols injected directly into the agent's context window. They instruct the agent on how to behave, which conventions to follow, and what boundaries must not be crossed.

- **Primary Documentation Source**: `https://antigravity.google/docs/rules/`
- **Internal Specification Reference**: Built-in skill `agy-customizations` (`builtin/skills/agy-customizations/docs/rules.md`)

---

## 2. Rule Locations & Formats

Antigravity discovers rules across three distinct hierarchical scopes:

```text
├── Global (~/.gemini/)
│   ├── AGENTS.md / GEMINI.md               # Standalone unconditional global rules
│   └── config/
│       ├── AGENTS.md / GEMINI.md
│       └── rules/*.md                      # Modular global rules (supports YAML frontmatter)
├── Workspace Root (<workspace_root>/)
│   ├── AGENTS.md / GEMINI.md               # Root project rules (unconditional)
│   └── .agents/
│       ├── AGENTS.md / GEMINI.md
│       └── rules/*.md                      # Modular workspace rules
└── Subdirectory Scopes (<workspace_root>/pkg/service/)
    └── AGENTS.md / GEMINI.md               # Directory-specific rules
```

### Supported File Formats:
1. **Standalone Rules (`AGENTS.md` / `GEMINI.md`)**:
   - Plain Markdown format without required YAML frontmatter.
   - Always active within their filesystem scope.
2. **Modular Rules (`rules/*.md`)**:
   - Markdown files supporting optional YAML frontmatter:
     ```yaml
     ---
     trigger: always_on      # or "model_decision"
     description: "Enforces strict typing and error boundary rules."
     ---
     ```

---

## 3. Activation Modes & Discovery Behavior

Antigravity applies rules dynamically based on file interaction:

### A. Directory Tree Walking (Hierarchical Discovery)
Whenever the agent reads, opens, edits, or inspects a file at `/workspace/src/auth/token.py`, Antigravity traverses the directory tree upward from the file's containing folder to the workspace repository root:
1. `/workspace/src/auth/AGENTS.md` (checked first)
2. `/workspace/src/AGENTS.md` (checked second)
3. `/workspace/AGENTS.md` (checked third)
4. Global rules in `~/.gemini/` (applied base)

### B. Activation Triggers
* **`always_on`**: Loaded into the model context unconditionally for every prompt and turn within that directory scope.
* **`model_decision`**: Progressive disclosure mode. Only the rule's title and `description` are indexed; the full body is loaded only when the model determines relevance to the user prompt.

---

## 4. Precedence, Conflict Resolution, and Budgets

### A. Cumulative Rule Merging
Antigravity rules are **cumulative rather than replacement-based**. The agent aggregates all discovered rules into a composite prompt block.

### B. Precedence Hierarchy (Highest to Lowest):
1. **Leaf Directory Rules**: Rules closest to the affected file take precedence over parent directories.
2. **Workspace Root Rules**: Project-wide guidelines (`<workspace>/AGENTS.md`).
3. **Declared JSON Configs**: Customizations specified in `plugins.json` or `rules.json`.
4. **Global Configuration Rules**: User-wide rules in `~/.gemini/config/`.

### C. Deduplication
Rules are deduplicated by their canonical, resolved filesystem path. A rule file discovered through multiple traversal paths is injected at most once per conversation turn.

### D. Hard Context Budgets:
* **Per-File Cap**: `24 KB` (`24,000` bytes). Rule files exceeding this cap are truncated on clean line boundaries.
* **Aggregate Token Budget**: `20,000` tokens (`defaultRulesBudget`). If discovered always-on rules exceed this budget, over-budget rules are demoted from full inline text to file-path pointers that the agent must view on demand.

---

## 5. Can Persistent Instructions Enforce SUPERVISOR?

A critical architectural distinction must be made between **instructional influence** (probabilistic model prompting) and **deterministic enforcement** (runtime interception).

| Supervision Requirement | Enforceable via Rules Alone? | Behavior & Limitation |
| :--- | :--- | :--- |
| **1. Every coding task must undergo review** | ⚠️ **PARTIAL (Probabilistic)** | The rule commands the model to run `python -m supervisor review` before completing. However, the model may hallucinate completion or skip the step if token limits or context drifts occur. |
| **2. Coding agent must not finish before review** | ❌ **UNENFORCEABLE by Rules** | Rules cannot physically intercept or prevent the model from emitting a `model_stop` token or closing the turn. |
| **3. Findings automatically passed for repair** | ⚠️ **PARTIAL (Prompted)** | Rules instruct the agent to immediately apply fixes without waiting for user prompts. Well-trained models comply, but there is no runtime loop guarantee. |
| **4. Coding agent must resume after review** | ❌ **UNENFORCEABLE by Rules** | If the agent completes its turn, a rule cannot wake the agent up. Resumption requires an external driver or lifecycle hook. |
| **5. Verification repeats after corrections** | ⚠️ **PARTIAL (Prompted)** | The agent is instructed to run `python -m supervisor verify`, but infinite loop prevention and retry ceilings must be enforced by code. |

---

## 6. Synthesis: The Role of Rules in SUPERVISOR

Rules are **necessary but insufficient** on their own:
1. **Primary Role**: Rules establish the behavioral contract, teaching the LLM its operating protocol and priming it to accept autonomous repair directives without prompting the user.
2. **Technical Backstop Required**: Hard guarantees (preventing premature termination, forcing loop re-entry, and enforcing retry limits) must be handled by Antigravity Lifecycle Hooks (`hooks.json`) and the core state orchestrator.
