"""Lifecycle and loop state management for SUPERVISOR."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from supervisor.models import LifecycleState, SupervisorReport, TaskSpec


@dataclass
class SupervisorState:
    """Persistent state of a supervisor session."""

    state: LifecycleState = LifecycleState.IMPLEMENTING
    cycle: int = 1
    max_retries: int = 3
    task: Optional[TaskSpec] = None
    latest_report: Optional[SupervisorReport] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "cycle": self.cycle,
            "max_retries": self.max_retries,
            "task": self.task.to_dict() if self.task else None,
            "latest_report": (
                self.latest_report.to_dict() if self.latest_report else None
            ),
            "history": self.history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SupervisorState:
        return cls(
            state=LifecycleState(d.get("state", LifecycleState.IMPLEMENTING.value)),
            cycle=d.get("cycle", 1),
            max_retries=d.get("max_retries", 3),
            task=TaskSpec.from_dict(d["task"]) if d.get("task") else None,
            latest_report=(
                SupervisorReport.from_dict(d["latest_report"])
                if d.get("latest_report")
                else None
            ),
            history=d.get("history", []),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=d.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )


class StateManager:
    """Manages atomic reads/writes to .supervisor/state.json to prevent race conditions."""

    DEFAULT_DIR = Path(".supervisor")
    STATE_FILE = "state.json"

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()
        self.supervisor_dir = self.workspace_root / self.DEFAULT_DIR
        self.state_path = self.supervisor_dir / self.STATE_FILE

    def load(self) -> SupervisorState:
        """Loads state from disk or returns a fresh state if nonexistent."""
        if not self.state_path.exists():
            return SupervisorState()
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SupervisorState.from_dict(data)
        except Exception:
            # Fallback to fresh state if corrupted
            return SupervisorState()

    def save(self, state: SupervisorState) -> None:
        """Atomically saves state to disk."""
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(state.to_dict(), indent=2)

        # Atomic write via tempfile in same dir
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.supervisor_dir), prefix="state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp_path, str(self.state_path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def initialize_task(
        self,
        prompt: str,
        task_id: Optional[str] = None,
        requirements: Optional[List[str]] = None,
        max_retries: int = 3,
    ) -> SupervisorState:
        """Initializes a new task in Stage 1."""
        tid = task_id or f"task_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        task = TaskSpec(
            task_id=tid,
            prompt=prompt,
            requirements=requirements or [],
        )
        state = SupervisorState(
            state=LifecycleState.IMPLEMENTING,
            cycle=1,
            max_retries=max_retries,
            task=task,
            latest_report=None,
            history=[],
        )
        self.save(state)
        return state

    def transition_to(
        self, new_state: LifecycleState, reason: str = ""
    ) -> SupervisorState:
        """Transitions to a new state and records in history."""
        curr = self.load()
        old_state = curr.state
        curr.state = new_state
        curr.history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_state": old_state.value,
                "to_state": new_state.value,
                "cycle": curr.cycle,
                "reason": reason,
            }
        )
        self.save(curr)
        return curr

    def advance_cycle_or_limit(self) -> SupervisorState:
        """Increments the repair cycle or sets RETRY_LIMIT_REACHED if max_retries exceeded."""
        curr = self.load()
        if curr.cycle >= curr.max_retries:
            curr.state = LifecycleState.RETRY_LIMIT_REACHED
            curr.history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "to_state": LifecycleState.RETRY_LIMIT_REACHED.value,
                    "cycle": curr.cycle,
                    "reason": f"Retry limit reached ({curr.cycle}/{curr.max_retries}).",
                }
            )
        else:
            curr.cycle += 1
            curr.state = LifecycleState.REPAIRING
            curr.history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "to_state": LifecycleState.REPAIRING.value,
                    "cycle": curr.cycle,
                    "reason": f"Advancing to repair cycle {curr.cycle} of {curr.max_retries}.",
                }
            )
        self.save(curr)
        return curr

    def update_report(self, report: SupervisorReport) -> SupervisorState:
        """Updates the latest report and adjusts state based on findings and retry limit."""
        curr = self.load()
        curr.latest_report = report
        if report.blocker_reason:
            curr.state = LifecycleState.BLOCKED
        elif not report.has_material_issues:
            curr.state = LifecycleState.PASSED
        else:
            if curr.cycle >= curr.max_retries:
                curr.state = LifecycleState.RETRY_LIMIT_REACHED
            else:
                curr.state = LifecycleState.FIXES_REQUIRED
        self.save(curr)
        return curr
