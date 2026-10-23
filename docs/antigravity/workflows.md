# Antigravity Workflows Research & Deprecation Analysis

## 1. Executive Definition & Deprecation Status
In earlier iterations of Antigravity (and Gemini Code Assist), **Workflows** were standalone Markdown files (`.md`) describing sequenced instructions for the agent to follow. 

> [!WARNING]
> **OFFICIAL DEPRECATION STATUS**:
> As of modern Google Antigravity, **legacy workflows (`.agents/workflows/*.md` and `workflows.json`) are officially deprecated**. They are superseded by the **Agent Skills** standard (`.agents/skills/<name>/SKILL.md`).
> Official migration tool provided by platform: `/migrate-workflows`.

- **Primary Documentation Source**: `https://antigravity.google/docs/ide/workflows/`
- **Internal Migration Skill Reference**: `builtin/skills/migrate-workflows/SKILL.md`

---

## 2. Legacy Workflow Mechanics

### A. File Storage & Formats
* **Workspace Workflows**: `<workspace>/.agents/workflows/*.md` (or legacy `<workspace>/.agent/workflows/*.md`)
* **Global Workflows**: `~/.gemini/config/workflows/*.md`
* **Workflow Manifests**: `workflows.json`

### B. Execution Model
Legacy workflows functioned as linear prompt expansions. When invoked by typing `/<workflow-name>`, the contents of the `.md` file were pasted into the conversation context as a multi-step user prompt.

---

## 3. Detailed Answers to Workflow Questions

| Question | Assessment | Evidence & Technical Rationale |
| :--- | :--- | :--- |
| **1. How are workflows defined?** | Single `.md` file with optional metadata. | Documented in `builtin/skills/migrate-workflows`. |
| **2. Where are workflow files stored?** | `.agents/workflows/` or `~/.gemini/config/workflows/`. | Documented in `migrate-workflows`. |
| **3. How are workflows invoked?** | Manually via slash command `/<name>`. | Documented. |
| **4. Can workflows call other workflows?** | ❌ **No**. | Workflows lack a nesting or call instruction stack. |
| **5. Can workflows execute a sequence of instructions?** | ⚠️ **Probabilistic text only**. | They provide numbered markdown steps, relying on model compliance. |
| **6. Can they initiate a review-and-repair cycle?** | ⚠️ **Text prompting only**. | They can prompt the model to review and repair, but cannot orchestrate loop state. |
| **7. Can a workflow run automatically after a task?** | ❌ **No**. | Workflows have **no automated trigger mechanism**. They require explicit user invocation. |
| **8. Can workflows resume the coding agent?** | ❌ **No**. | Once the agent yields turn control, a workflow cannot resume it. |
| **9. Can workflows prevent premature completion?** | ❌ **No**. | Workflows lack lifecycle interception primitives. |
| **10. What happens at the final step?** | The model terminates its turn (`model_stop`). | No built-in verification gate is triggered. |
| **11. Do workflows differ across surfaces?** | Deprecated across all surfaces (IDE, App, CLI). | Migration to skills is enforced universally. |
| **12. Is workflow support changing/replaced?** | **YES. Replaced by Agent Skills (`SKILL.md`).** | Explicitly declared in official release notes and `/migrate-workflows`. |

---

## 4. Why Agent Skills Replaced Workflows

According to the official migration specification (`migrate-workflows`):

1. **Progressive Context Loading**: Monolithic workflow markdown files dumped all instructions into context upfront. Skills only index lightweight frontmatter (`name` and `description`) and load the body on-demand.
2. **First-Class Directory Bundles**: Workflows were restricted to a single `.md` file. Skills permit bundling executable scripts (`scripts/`), validation schemas (`resources/`), and sub-specifications (`references/`).
3. **Cross-Platform Industry Standard**: Complies with the open `agentskills.io` standard supported across modern agent ecosystems.

---

## 5. Architectural Decision for SUPERVISOR

**Do NOT build SUPERVISOR as a legacy workflow.**
Designing SUPERVISOR as a workflow (`.agents/workflows/supervisor.md`) would violate current platform standards, incur immediate technical debt, and prevent multi-file bundling. SUPERVISOR must strictly be packaged as an **Agent Skill** (`.agents/skills/supervisor/SKILL.md`) backed by lifecycle hooks.

