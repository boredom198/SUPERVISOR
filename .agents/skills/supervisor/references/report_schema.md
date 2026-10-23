# SUPERVISOR Report Schema Specification

## Finding Data Structure (Stage 5)

Each finding produced by SUPERVISOR contains the following fields:

| Field | Type | Description |
| :--- | :--- | :--- |
| `finding_id` | String | Unique finding identifier (e.g., `SEC-0042`, `SYNTAX-1029`, `TEST-FAIL-0012`) |
| `severity` | Enum | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO` |
| `category` | Enum | `BUG`, `MISSING_REQUIREMENT`, `REGRESSION`, `SECURITY`, `ERROR_HANDLING`, `DEPENDENCY_CONFIG`, `INTEGRATION`, `TESTING`, `PERFORMANCE`, `SPEC_MISMATCH` |
| `file_path` | String | Path to the affected file relative to workspace root |
| `line_number` | Integer (optional) | 1-indexed line number where the issue occurs |
| `description` | String | Concise, factual description of the identified defect |
| `evidence` | String | Concrete code snippet, error traceback, or command output |
| `why_it_matters` | String | Practical impact and consequences if left uncorrected |
| `recommended_correction`| String | Concrete actionable guidance for repairing the issue |
| `verification_method` | String | Specific test, command, or check that verifies resolution |
| `status` | Enum | `CONFIRMED`, `SUSPECTED`, `RECOMMENDED`, `RESOLVED`, `UNRESOLVED`, `WONT_FIX` |
| `cycle` | Integer | Supervision cycle number during which the finding was evaluated |

## Report Persistence Locations

- **Latest Report**: `.supervisor/latest_report.md` and `.supervisor/latest_report.json`
- **Cycle Reports**: `.supervisor/reports/report_cycle_<N>.md` and `.supervisor/reports/report_cycle_<N>.json`
- **Active Repair Directives**: `.supervisor/repair_directives.md`
