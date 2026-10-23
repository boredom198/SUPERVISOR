# Antigravity Documentation Sources & Citation Index

This index records all official public documentation, platform specifications, and internal built-in references consulted during SUPERVISOR research.

**Date of Access**: October 23, 2026

---

## 1. Official Documentation URLs

### A. Agent Skills
* **URL**: `https://antigravity.google/docs/skills`
* **Sections Consulted**:
  * *Skill Directory Structure & SKILL.md Specification*
  * *YAML Frontmatter Metadata Indexing*
  * *Progressive Disclosure Architecture*
  * *Slash Command Generation (`/<name>`)*
* **Key Excerpt / Evidence**:
  > "Skills are an open standard for extending agent capabilities. A skill is a folder containing a SKILL.md file with instructions that the agent can follow... To prevent context bloat, only the YAML frontmatter is indexed globally. The markdown body is loaded only when the skill is selected."
* **Source Limitation**: The web portal uses Astro/Starlight client-side rendering with Pagefind search; deep links resolve through canonical redirects.

### B. Workspace Rules & Instructions
* **URL**: `https://antigravity.google/docs/rules/` (and `https://antigravity.google/docs/rules-workflows`)
* **Sections Consulted**:
  * *Directory-Based Rules (`AGENTS.md` and `GEMINI.md`)*
  * *Hierarchical Discovery & Upward Directory Traversal*
  * *Cumulative Merging vs Replacement*
  * *Context Budgets & File Caps (24KB / 20k token limits)*
* **Key Excerpt / Evidence**:
  > "Rules in Antigravity are cumulative rather than replacement-based... Whenever Antigravity reads or edits a file, it walks up the directory tree from that file's folder to the workspace root, loading rules at each level."

### C. Lifecycle Hooks
* **URL**: `https://antigravity.google/docs/hooks`
* **Sections Consulted**:
  * *Hook Configuration (`hooks.json`)*
  * *Event Catalog (`SessionStart`, `PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, `Stop`)*
  * *Input & Output JSON Contracts (`stdin` / `stdout`)*
  * *Stop Hook Semantics & Continuation Directives*
* **Key Excerpt / Evidence**:
  > "The Stop event fires when the execution loop terminates... Returning `decision: 'continue'` blocks termination and re-enters the loop, injecting the `reason` field as a system message."

### D. Workflows & Migration
* **URL**: `https://antigravity.google/docs/ide/workflows/`
* **Sections Consulted**:
  * *Legacy Workflow Specifications (`.agents/workflows/*.md`)*
  * *Deprecation Notice & End-of-Life Announcement*
  * *Migration to Agent Skills (`SKILL.md`)*
* **Key Excerpt / Evidence**:
  > "Legacy single-file Markdown workflows (.md) are deprecated in favor of the open industry-standard Agent Skills (SKILL.md). Use `/migrate-workflows` to autonomously migrate existing workflows."

### E. Agent Overview, Subagents & Settings
* **URL**: `https://antigravity.google/docs/agent` and `https://antigravity.google/docs/agent-settings`
* **Sections Consulted**:
  * *Subagent Tooling (`invoke_subagent`, `define_subagent`, `send_message`, `manage_subagents`)*
  * *Isolation Modes (`inherit`, `branch`, `share`)*
  * *Tool Execution Policies & Sandbox Restrictions*
* **Key Excerpt / Evidence**:
  > "Subagents can either inherit the parent workspace (`inherit`), spin up an isolated Git worktree (`branch`), or share directory storage (`share`)... `send_message` facilitates active, targeted, bidirectional communication between agents mid-flight."

---

## 2. Platform Built-In Specifications (Local Machine)

The following authoritative specifications were directly inspected on the local workstation:

1. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\agy-customizations\docs\hooks.md`**:
   - Complete technical specification of hook events, JSON schemas, camelCase field encoding, and exit codes.
2. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\agy-customizations\docs\rules.md`**:
   - Specification of 24KB per-file limits and 20,000-token rules budget.
3. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\agy-customizations\docs\skills.md`**:
   - Specification of skill directory structure, frontmatter properties, and progressive disclosure rules.
4. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\agy-customizations\docs\plugins.md`**:
   - Specification of plugin bundling for skills, hooks, and rules.
5. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\migrate-workflows\SKILL.md`**:
   - Detailed rationale and deprecation documentation for legacy workflows.
6. **`C:\Users\DELL\.gemini\antigravity\builtin\skills\antigravity_guide\SKILL.md`**:
   - Comparative reference covering Antigravity CLI, IDE, App, and Python SDK.
