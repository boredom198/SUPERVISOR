# SUPERVISOR Platform Integration Contract & Portability Architecture

## 1. Portability Philosophy: Shared Core + Platform Adapters

SUPERVISOR enforces an absolute architectural boundary:
* **The Shared Core (`supervisor/`)** contains 100% of the review logic, AST parsers, security rules, test runners, finding reconciler, state management, and report formatters. It has **zero dependencies** on Antigravity or any proprietary vendor SDK.
* **Platform Adapters (`supervisor/adapters/<platform>/`)** translate host-specific events, lifecycle hooks, slash commands, and agent communication channels into standardized Core interface calls.

```mermaid
flowchart TD
    subgraph Host Environments
        AG[Google Antigravity]
        CC[Claude Code]
        CD[OpenAI Codex Harness]
        GD[GitLab Duo CLI]
        CI[Standalone CI / Pre-commit]
    end

    subgraph Adapters
        AGA[Antigravity Adapter]
        CCA[Claude Code Adapter]
        CDA[Codex Adapter]
        GDA[GitLab Duo Adapter]
        CIA[CI Adapter]
    end

    subgraph Core Contract Interfaces
        IGC[ExecutionGatingContract]
        CEC[ContextExtractionContract]
        RDC[RepairDirectiveContract]
    end

    subgraph Shared Core Engine
        Engine[SUPERVISOR Shared Core]
    end

    AG --> AGA
    CC --> CCA
    CD --> CDA
    GD --> GDA
    CI --> CIA

    AGA & CCA & CDA & GDA & CIA --> IGC & CEC & RDC
    IGC & CEC & RDC --> Engine
```

---

## 2. Standard Adapter Interface Contracts

Any platform adapter must implement or map to the following three contracts:

### A. `ExecutionGatingContract`
Responsible for intercepting the completion event and deciding whether the agent is allowed to stop:

```python
class ExecutionGatingContract(abc.ABC):
    @abc.abstractmethod
    def intercept_completion(self, context_payload: dict) -> GatingDecision:
        """
        Evaluates whether the agent may conclude its turn.
        Returns:
            GatingDecision(
                allow_stop=True|False,
                continuation_payload=Optional[str],
                reason=str
            )
        """
        pass
```

### B. `ContextExtractionContract`
Responsible for retrieving the original task prompt, active files, and workspace metadata:

```python
class ContextExtractionContract(abc.ABC):
    @abc.abstractmethod
    def extract_task_prompt(self, host_context: dict) -> Optional[str]:
        """Discovers the original user prompt from host logs, transcripts, or env."""
        pass

    @abc.abstractmethod
    def get_workspace_root(self) -> Path:
        """Returns the canonical root directory of the workspace."""
        pass
```

### C. `RepairDirectiveContract`
Responsible for injecting actionable repair instructions back into the active agent:

```python
class RepairDirectiveContract(abc.ABC):
    @abc.abstractmethod
    def inject_repair_directive(self, directive_markdown: str) -> bool:
        """
        Passes repair instructions to the agent without waiting for user prompting.
        Returns True if autonomous handoff succeeded.
        """
        pass
```

---

## 3. Platform Capabilities & Adapter Assessment

| Platform | Automatic Triggering | Repair Handoff | Completion Enforcement | Implementation Mechanism | Support Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Google Antigravity** | `Stop` hook in `.agents/hooks.json` | `decision: "continue"` with `reason` string | **Hard Gating**: Hook blocks turn completion | Native lifecycle hook + Skill + AGENTS.md | **Documented & Tested** |
| **Claude Code** | Pre-prompt / bash tool wrappers or hooks | Tool output injection or stdin injection | **Hard Gating**: Non-zero exit code halts completion | CLI hook / custom tool wrapper | **Plausible (Requires Testing)** |
| **OpenAI Codex** | Harness wrapper script / loop runner | Appending to message list in API loop | **Hard Gating**: Orchestration harness controls API calls | Python loop wrapper (`codex_runner.py`) | **Documented & Verified** |
| **GitLab Duo CLI** | Pipeline trigger or Duo CLI hook | Duo chat message / pipeline notification | **Soft Gating**: Fails MR pipeline check | CI job / Git hook (`pre-push`) | **Plausible (MR Gate)** |
| **Standalone CI (GitHub Actions)**| `on: [push, pull_request]` | Annotations / PR comments / CLI failure | **Hard Gating**: Fails workflow exit code 1 | GitHub Actions runner `python -m supervisor review` | **Documented & Tested** |

---

## 4. Detailed Specification of Future Adapters

### A. Claude Code Adapter (`supervisor/adapters/claude_code/`)
* **Hook Mechanism**: Claude Code supports lifecycle shell integration and tool interception via project settings.
* **Handoff Mechanism**: Emits formatted repair markdown on stderr/stdout with exit code 1, forcing Claude Code to read the failure output and propose repairs.
* **Pre-requisite Research**: Verify exact JSON-RPC tool-use interception in the latest Claude Code CLI releases.

### B. OpenAI Codex Harness Adapter (`supervisor/adapters/codex/`)
* **Harness Model**: Since Codex is typically accessed programmatically via agent orchestration frameworks (e.g. OpenAI Assistants API or custom agent loops), the adapter acts as an execution decorator around `client.beta.threads.runs.create_and_poll`.
* **Handoff Mechanism**: If SUPERVISOR returns `FIXES_REQUIRED`, the decorator automatically appends the repair directive to the thread and triggers a new run.

### C. Standalone CI Adapter (`supervisor/adapters/ci/`)
* **Execution**: Executed directly in GitHub Actions or GitLab CI runners:
  ```yaml
  - name: Run SUPERVISOR Review
    run: python -m supervisor review --format markdown
  ```
* **Output**: Writes summary to `$GITHUB_STEP_SUMMARY` and posts PR review comments via GitHub API.
* **Gating**: Exits with status `1` on material defects, blocking PR merge.
