"""Lifecycle and loop state management for SUPERVISOR."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from supervisor.models import LifecycleState, SupervisorReport, TaskSpec


def is_pid_alive(pid: Optional[int]) -> bool:
    """Checks whether a process with the given PID is currently alive."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            res = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(res and exit_code.value == STILL_ACTIVE)
        except Exception:
            try:
                proc = subprocess.run(
                    ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return str(pid) in proc.stdout
            except Exception:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


@dataclass
class SupervisorState:
    """Persistent state of a supervisor session."""

    state: LifecycleState = LifecycleState.IMPLEMENTING
    cycle: int = 1
    max_retries: int = 3
    task: Optional[TaskSpec] = None
    latest_report: Optional[SupervisorReport] = None
    active_pid: Optional[int] = None
    workspace_fingerprint: str = ""
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
            "active_pid": self.active_pid,
            "workspace_fingerprint": self.workspace_fingerprint,
            "history": self.history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SupervisorState:
        max_retries_val = max(1, int(d.get("max_retries", 3)))
        cycle_val = max(1, int(d.get("cycle", 1)))
        if cycle_val > max_retries_val:
            cycle_val = max_retries_val
        return cls(
            state=LifecycleState(d.get("state", LifecycleState.IMPLEMENTING.value)),
            cycle=cycle_val,
            max_retries=max_retries_val,
            task=TaskSpec.from_dict(d["task"]) if d.get("task") else None,
            latest_report=(
                SupervisorReport.from_dict(d["latest_report"])
                if d.get("latest_report")
                else None
            ),
            active_pid=d.get("active_pid"),
            workspace_fingerprint=d.get("workspace_fingerprint", ""),
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
        if state.max_retries > 0 and state.cycle > state.max_retries:
            state.cycle = state.max_retries
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
        max_retries = max(1, int(max_retries))
        tid = task_id or f"task_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
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
        elif report.overall_status.value == "VERIFICATION_INCOMPLETE":
            curr.state = LifecycleState.VERIFICATION_INCOMPLETE
        elif report.overall_status.value == "BLOCKED":
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

    def is_review_active(
        self, state: Optional[SupervisorState] = None
    ) -> Tuple[bool, bool]:
        """Evaluates whether a review is currently active and whether it is abandoned.

        Returns:
            Tuple[in_progress: bool, is_abandoned: bool]
        """
        curr = state or self.load()
        if curr.state != LifecycleState.REVIEWING:
            return False, False

        # If active_pid was recorded, verify process liveness
        if curr.active_pid:
            if curr.active_pid == os.getpid():
                # Current process is running the review
                return True, False
            alive = is_pid_alive(curr.active_pid)
            if alive:
                return True, False
            else:
                # Process died or was killed without clearing reviewing state
                return True, True

        # Fallback if no PID recorded: use age threshold (safe bound: 60s)
        try:
            state_time = datetime.fromisoformat(curr.updated_at)
            age = (datetime.now(timezone.utc) - state_time).total_seconds()
            if age > 60:
                return True, True
        except Exception:
            return True, True

        return True, False

    def has_unreviewed_changes(
        self,
        latest_report: Optional[SupervisorReport],
        current_fingerprint: str,
        git_changed: List[str],
        git_untracked: List[str],
    ) -> bool:
        """Determines if the workspace has changed since the latest report."""
        if not latest_report:
            return bool(git_changed or git_untracked or current_fingerprint)
        # Check git status modifications
        if git_changed or git_untracked:
            return True
        # Check workspace fingerprint for non-git workspaces or staged file modifications
        rep_fp = getattr(latest_report, "workspace_fingerprint", None)
        if rep_fp and current_fingerprint and rep_fp != current_fingerprint:
            return True
        return False

