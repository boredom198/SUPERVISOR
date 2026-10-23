# SUPERVISOR State Storage & Report Schema Specification

## 1. Directory Structure (`.supervisor/`)

All SUPERVISOR session state, active configurations, reports, and lockfiles are maintained in a repository-local directory `.supervisor/`:

```text
<workspace_root>/.supervisor/
├── config.json               # OPTIONAL: User/project overrides (max_retries, timeouts, paths)
├── state.json                # MANDATORY: Atomic lifecycle state machine record
├── lock                      # RUNTIME: Concurrency lockfile preventing race conditions
├── repair_directives.md      # RUNTIME: Active handoff prompt for coding agent
├── latest_report.json        # RUNTIME: Pointer/copy of the most recent JSON report
├── latest_report.md          # RUNTIME: Pointer/copy of the most recent Markdown report
└── reports/                  # RUNTIME: Historical audit trail
    ├── report_cycle_1.json
    ├── report_cycle_1.md
    ├── report_cycle_2.json
    └── report_cycle_2.md
```

---

## 2. JSON Schemas

### A. State Schema (`state.json`)

The state file records the active supervision session, tracking cycle progression and history:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "SupervisorState",
  "type": "object",
  "required": ["schema_version", "state", "cycle", "max_retries", "created_at", "updated_at"],
  "properties": {
    "schema_version": { "type": "string", "enum": ["1.0.0"] },
    "state": {
      "type": "string",
      "enum": [
        "IMPLEMENTING",
        "REVIEW_PENDING",
        "REVIEWING",
        "FIXES_REQUIRED",
        "REPAIRING",
        "VERIFYING",
        "PASSED",
        "BLOCKED",
        "RETRY_LIMIT_REACHED"
      ]
    },
    "cycle": { "type": "integer", "minimum": 1 },
    "max_retries": { "type": "integer", "minimum": 1, "default": 3 },
    "task": {
      "type": "object",
      "required": ["task_id", "prompt"],
      "properties": {
        "task_id": { "type": "string" },
        "prompt": { "type": "string" },
        "requirements": { "type": "array", "items": { "type": "string" } },
        "context_files": { "type": "array", "items": { "type": "string" } },
        "created_at": { "type": "string", "format": "date-time" }
      }
    },
    "latest_report_id": { "type": ["string", "null"] },
    "history": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["timestamp", "from_state", "to_state", "cycle", "reason"],
        "properties": {
          "timestamp": { "type": "string", "format": "date-time" },
          "from_state": { "type": "string" },
          "to_state": { "type": "string" },
          "cycle": { "type": "integer" },
          "reason": { "type": "string" }
        }
      }
    },
    "created_at": { "type": "string", "format": "date-time" },
    "updated_at": { "type": "string", "format": "date-time" }
  }
}
```

---

### B. Finding Schema (`finding.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Finding",
  "type": "object",
  "required": [
    "finding_id",
    "severity",
    "category",
    "file_path",
    "description",
    "evidence",
    "why_it_matters",
    "recommended_correction",
    "verification_method",
    "status",
    "cycle"
  ],
  "properties": {
    "finding_id": { "type": "string" },
    "severity": { "type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] },
    "category": {
      "type": "string",
      "enum": [
        "BUG",
        "MISSING_REQUIREMENT",
        "REGRESSION",
        "SECURITY",
        "ERROR_HANDLING",
        "DEPENDENCY_CONFIG",
        "INTEGRATION",
        "TESTING",
        "PERFORMANCE",
        "SPEC_MISMATCH"
      ]
    },
    "file_path": { "type": "string" },
    "line_number": { "type": ["integer", "null"] },
    "description": { "type": "string" },
    "evidence": { "type": "string" },
    "why_it_matters": { "type": "string" },
    "recommended_correction": { "type": "string" },
    "verification_method": { "type": "string" },
    "status": {
      "type": "string",
      "enum": ["CONFIRMED", "SUSPECTED", "RECOMMENDED", "RESOLVED", "UNRESOLVED", "WONT_FIX"]
    },
    "cycle": { "type": "integer", "minimum": 1 }
  }
}
```

---

### C. Report Schema (`report.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "SupervisorReport",
  "type": "object",
  "required": ["report_id", "task_id", "cycle", "state", "findings", "tests_run", "summary", "created_at"],
  "properties": {
    "report_id": { "type": "string" },
    "task_id": { "type": "string" },
    "cycle": { "type": "integer" },
    "state": { "type": "string" },
    "findings": {
      "type": "array",
      "items": { "$ref": "#/definitions/Finding" }
    },
    "tests_run": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["command", "exit_code", "duration_seconds", "passed"],
        "properties": {
          "command": { "type": "string" },
          "exit_code": { "type": "integer" },
          "stdout": { "type": "string" },
          "stderr": { "type": "string" },
          "duration_seconds": { "type": "number" },
          "passed": { "type": "boolean" }
        }
      }
    },
    "changed_files": { "type": "array", "items": { "type": "string" } },
    "summary": { "type": "string" },
    "blocker_reason": { "type": ["string", "null"] },
    "created_at": { "type": "string", "format": "date-time" }
  }
}
```

---

## 3. Concurrency, Locking & Interruption Safety

### A. Process File Locking (`.supervisor/lock`):
* To prevent race conditions between an active agent turn, concurrent subagents, and the `Stop` lifecycle hook, all state modifications acquire an advisory file lock on `.supervisor/lock`.
* **Lock Contract**: Uses OS-level file locking (`msvcrt.locking` on Windows, `fcntl.flock` on Unix).
* **Stale Lock Cleanup**: Lock files record PID and timestamp. If a lock holder PID no longer exists or the lock age exceeds 120 seconds, the lock is safely reclaimed.

### B. Atomic Disk Writes:
* `state.json` and reports are written to a temporary file in the same directory (`state_<uuid>.tmp`) and renamed via `os.replace`. This guarantees that crashes or interrupts never corrupt the state file.

### C. Recovery from Interrupted Sessions:
* If an agent process crashes mid-repair, the next invocation checks `state.json`. If state was `REPAIRING`, the engine transitions gracefully to `REVIEW_PENDING` and prompts re-verification.

---

## 4. Git & Version Control Policy

### Filesystem Inclusion Rules:
* **Excluded by `.gitignore` (MANDATORY)**:
  ```text
  .supervisor/state.json
  .supervisor/lock
  .supervisor/latest_report.*
  .supervisor/repair_directives.md
  .supervisor/reports/
  ```
  Runtime state, reports, and lockfiles are local to the machine and must **never** be committed to version control.
* **Tracked in Version Control**:
  * `.supervisor/config.json` (if committing project-wide default limits or rules).
  * `.agents/hooks.json`
  * `.agents/skills/supervisor/`
  * `AGENTS.md` / `GEMINI.md`

---

## 5. Retention & Secret Redaction Policy

1. **Report Retention**: The engine retains the 10 most recent cycle reports (`report_cycle_<N>.*`). Historical reports beyond 10 cycles are pruned to prevent disk bloat.
2. **Secret Redaction**: Any text matching high-entropy secret patterns (e.g. AWS access keys, Bearer tokens, private key blocks) is replaced with `[REDACTED]` prior to serializing JSON or writing Markdown reports.
