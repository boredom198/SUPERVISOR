# SUPERVISOR Command Line Interface (CLI) Specification

## 1. Overview & Invocation

The SUPERVISOR CLI is the cross-platform, zero-dependency executable entry point for the system. It can be invoked via:
```powershell
python -m supervisor <command> [options]
# OR when installed in PATH
supervisor <command> [options]
```

---

## 2. Global Exit Codes

The CLI returns standard POSIX exit codes to integrate cleanly with shell scripts, CI pipelines, and lifecycle hooks:

| Exit Code | Meaning | Condition |
| :---: | :--- | :--- |
| **`0`** | **SUCCESS / PASSED** | All checks passed; zero material findings remain. |
| **`1`** | **MATERIAL DEFECTS FOUND** | One or more Critical, High, or Medium findings require repair, or retry limit reached with unresolved defects. |
| **`2`** | **EXECUTION BLOCKED / ERROR** | External environment blocker, test command timeout, missing essential dependencies, or syntax crash. |
| **`3`** | **CLI USAGE ERROR** | Invalid arguments, unrecognized flags, or malformed configuration file. |

---

## 3. Subcommand Specifications

### A. `supervisor review`
Inspects the repository, validates syntax/AST, scans for credentials, executes test suites, and generates findings reports.

```powershell
supervisor review [OPTIONS]
```

#### Options:
* `--prompt`, `-p` `<string>`: Original task prompt. If omitted, extracts prompt from transcript or state file.
* `--task-id`, `-t` `<string>`: Explicit task identifier. Defaults to timestamp-based ID.
* `--test-cmd` `<string>`: Explicit test command to execute (overrides auto-discovery).
* `--report-only`: Evaluates codebase and writes report **without** updating state to `FIXES_REQUIRED` or generating repair directives.
* `--deep`: Enables Mode 2 review (invokes `supervisor-auditor` if supported).
* `--fast`: Fast-path review. Skips test execution and runs only AST and security scans (< 2s).
* `--max-retries` `<int>`: Maximum repair attempts before halting (default: `3`).
* `--format` `[text|markdown|json]`: Output format on stdout (default: `markdown`).
* `--output`, `-o` `<path>`: Custom file destination for the generated report.

---

### B. `supervisor verify`
Executes Stage 7 post-repair verification. Re-evaluates previously flagged findings, checks for regressions, and reruns test suites.

```powershell
supervisor verify [OPTIONS]
```

#### Options:
* `--test-cmd` `<string>`: Explicit test command.
* `--fast`: Fast-path verification.
* `--format` `[text|markdown|json]`: Output format on stdout.

---

### C. `supervisor status`
Displays the current lifecycle state, active cycle count, retry limits, and active findings.

```powershell
supervisor status [OPTIONS]
```

#### Options:
* `--format` `[text|json]`: Output format (default: `text`).

#### Example Output:
```text
SUPERVISOR Status:
- State: FIXES_REQUIRED
- Cycle: 1 / 3
- Task ID: task_20261023_1845
- Material Findings (2):
  [HIGH] BUG-0042: Index out of range (src/auth/token.py:15)
  [CRITICAL] SEC-0001: eval() call detected (src/calc.py:4)
- Repair Directives: .supervisor/repair_directives.md
```

---

### D. `supervisor init`
Initializes a new supervision session and records task prompt and requirements into `.supervisor/state.json`.

```powershell
supervisor init "<task prompt>" [OPTIONS]
```

#### Options:
* `--max-retries` `<int>`: Default: `3`.
* `--task-id` `<string>`: Custom task ID.

---

### E. `supervisor reset`
Clears session state, deletes active lockfiles, and resets the lifecycle to `IMPLEMENTING`.

```powershell
supervisor reset
```

---

### F. `supervisor report`
Outputs the latest generated report to stdout or opens it in a viewer.

```powershell
supervisor report [--cycle <N>] [--format json|markdown]
```

---

## 4. Edge-Case Behaviors

### A. Execution Outside a Git Repository
* If `.git` is absent, the CLI logs an informational warning.
* Instead of running `git diff`, it falls back to inspecting all workspace files (excluding virtual environments and caches).
* It does not crash or exit with error; it performs full AST and security validation on available source files.

### B. Execution with Zero Workspace Changes
* If `git status` reports a clean working tree and no files were modified, the CLI reports `PASSED` with a note:
  `"No modified or untracked files detected in workspace. Verification PASSED."`

### C. Execution when Original Task Prompt is Unavailable
* If no prompt is passed and transcript logs are not found, the CLI sets `task_id: "task_auto"` and `prompt: "UNAVAILABLE"`.
* It evaluates the diff against general syntax, security, error handling, and test requirements.
* It does **not** hallucinate requirements or invent imaginary acceptance criteria.
