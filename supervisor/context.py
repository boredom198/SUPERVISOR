"""Context collector for workspace changes, git diffs, and prompt/transcript requirements."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from supervisor.models import TaskSpec


@dataclass
class WorkspaceContext:
    """Captured context of the current workspace and task."""

    task: TaskSpec
    workspace_root: Path
    changed_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)
    git_diff: str = ""
    file_contents: Dict[str, str] = field(default_factory=dict)
    is_git_repo: bool = False


class ContextCollector:
    """Collects task requirements and workspace changes."""

    IGNORED_DIRS = {
        ".git",
        ".supervisor",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
    }

    def __init__(self, workspace_root: Path | str | None = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    def find_transcript_prompt(self, transcript_path: Path | str | None = None) -> Optional[str]:
        """Attempts to discover the original user prompt from transcript.jsonl."""
        candidates = []
        if transcript_path:
            candidates.append(Path(transcript_path))

        # Check standard Antigravity appData paths
        app_data = os.environ.get("APPDATA", "")
        user_profile = os.environ.get("USERPROFILE", "")
        home_dir = Path.home()

        search_roots = [
            home_dir / ".gemini" / "antigravity" / "brain",
            Path(user_profile) / ".gemini" / "antigravity" / "brain",
        ]

        for root in search_roots:
            if root.exists():
                for sub in root.iterdir():
                    tpath = sub / ".system_generated" / "logs" / "transcript.jsonl"
                    if tpath.exists():
                        candidates.append(tpath)

        for c in candidates:
            if c.exists():
                prompt = self._extract_prompt_from_file(c)
                if prompt:
                    return prompt
        return None

    def _extract_prompt_from_file(self, transcript_file: Path) -> Optional[str]:
        """Reads transcript.jsonl and finds the latest USER_INPUT message."""
        try:
            with open(transcript_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "USER_INPUT" and entry.get("content"):
                        return entry["content"]
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def parse_requirements(self, prompt: str) -> List[str]:
        """Extracts bulleted/numbered requirements and imperative sentences from prompt."""
        requirements: List[str] = []
        lines = prompt.splitlines()

        for line in lines:
            line_str = line.strip()
            # Match markdown bullets (*, -, +) or numbered lists (1., 2.)
            m = re.match(r"^(\*|-|\+|\d+\.)\s+(.+)$", line_str)
            if m:
                req_text = m.group(2).strip()
                if len(req_text) > 10 and not req_text.startswith("http"):
                    requirements.append(req_text)
            elif any(
                line_str.lower().startswith(prefix)
                for prefix in ("must ", "should ", "ensure ", "verify ", "implement ", "create ")
            ):
                if len(line_str) > 15:
                    requirements.append(line_str)

        return requirements

    def inspect_git(self) -> Tuple[bool, List[str], List[str], str]:
        """Runs git status and git diff to capture changed and untracked files."""
        is_git = (self.workspace_root / ".git").exists()
        if not is_git:
            return False, [], [], ""

        changed: List[str] = []
        untracked: List[str] = []
        diff_text = ""

        try:
            # Check git status
            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if status_res.returncode == 0:
                for line in status_res.stdout.splitlines():
                    if not line.strip():
                        continue
                    prefix = line[:2]
                    filepath = line[3:].strip()
                    if prefix.startswith("?"):
                        untracked.append(filepath)
                    else:
                        changed.append(filepath)

            # Get diff of modified/staged files
            diff_res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_res.returncode == 0:
                diff_text = diff_res.stdout
            else:
                # If HEAD does not exist yet (fresh repo)
                diff_res2 = subprocess.run(
                    ["git", "diff"],
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                diff_text = diff_res2.stdout
        except Exception:
            pass

        return True, changed, untracked, diff_text

    def read_workspace_files(self, paths: Optional[List[str]] = None) -> Dict[str, str]:
        """Reads contents of specified files, or all relevant files in workspace if None."""
        contents: Dict[str, str] = {}

        if paths is not None:
            for p in paths:
                file_path = self.workspace_root / p
                if file_path.is_file():
                    try:
                        contents[p] = file_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
            return contents

        # Scan workspace recursively, respecting ignored directories
        for root, dirs, files in os.walk(str(self.workspace_root)):
            dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
            for file in files:
                rel = os.path.relpath(os.path.join(root, file), str(self.workspace_root))
                # Skip binary files or large files > 1MB
                fpath = Path(root) / file
                try:
                    if fpath.stat().st_size <= 1_000_000:
                        contents[rel] = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        return contents

    def collect(
        self,
        explicit_prompt: Optional[str] = None,
        transcript_path: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> WorkspaceContext:
        """Assembles full WorkspaceContext."""
        prompt = explicit_prompt or self.find_transcript_prompt(transcript_path) or ""
        requirements = self.parse_requirements(prompt) if prompt else []

        task = TaskSpec(
            task_id=task_id or "task_default",
            prompt=prompt,
            requirements=requirements,
        )

        is_git, changed, untracked, diff_text = self.inspect_git()
        all_relevant = list(dict.fromkeys(changed + untracked))
        file_contents = self.read_workspace_files(all_relevant if all_relevant else None)

        return WorkspaceContext(
            task=task,
            workspace_root=self.workspace_root,
            changed_files=changed,
            untracked_files=untracked,
            git_diff=diff_text,
            file_contents=file_contents,
            is_git_repo=is_git,
        )
