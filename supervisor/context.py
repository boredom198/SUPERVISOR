"""Context collector for workspace files, git status, and task requirements."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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
    has_explicit_prompt: bool = True
    limitations: List[str] = field(default_factory=list)
    workspace_fingerprint: str = ""


class ContextCollector:
    """Collects task requirements and workspace changes safely and portably."""

    IGNORED_DIRS: Set[str] = {
        ".git",
        ".supervisor",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        ".tox",
        "dist",
        "build",
        ".idea",
        ".vscode",
        ".eggs",
    }

    MAX_FILE_BYTES: int = 1_000_000  # 1 MB per file
    MAX_WORKSPACE_FILES: int = 5000
    MAX_WORKSPACE_BYTES: int = 50_000_000
    TEXT_EXTENSIONS: Set[str] = {
        ".py",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".md",
        ".txt",
        ".sh",
        ".bash",
        ".ps1",
        ".html",
        ".css",
        ".sql",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".java",
        ".kt",
        ".xml", ".ini", ".cfg", ".properties", ".vue", ".svelte",
        ".php", ".rb", ".swift", ".dart", ".scala", ".ex", ".exs",
        ".mod", ".gradle", ".kts", ".csproj", ".sln", ".lock",
    }
    TEXT_FILENAMES: Set[str] = {".env", ".npmrc", ".pypirc", ".netrc", "dockerfile", "makefile"}

    @classmethod
    def is_text_file(cls, path: Path) -> bool:
        name = path.name.lower()
        return path.suffix.lower() in cls.TEXT_EXTENSIONS or name in cls.TEXT_FILENAMES or name.startswith(".env.")

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = (
            Path(workspace_root) if workspace_root else Path.cwd()
        ).resolve()

    def is_safe_path(self, target_path: Path) -> bool:
        """Verifies that a path does not escape the workspace root via directory traversal or symlinks."""
        try:
            resolved = target_path.resolve()
            resolved.relative_to(self.workspace_root)
            return True
        except Exception:
            return False

    def find_transcript_prompt(self, transcript_path: Optional[Path | str] = None) -> Optional[str]:
        """Attempts to discover the original user prompt from transcript.jsonl."""
        candidates: List[Path] = []
        if transcript_path:
            candidates.append(Path(transcript_path))

        user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        if user_profile:
            brain_dir = Path(user_profile) / ".gemini" / "antigravity" / "brain"
            if brain_dir.exists():
                try:
                    for sub in brain_dir.iterdir():
                        try:
                            tpath = sub / ".system_generated" / "logs" / "transcript.jsonl"
                            if tpath.is_file():
                                candidates.append(tpath)
                        except OSError:
                            continue
                except OSError:
                    pass

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        # Sort candidates by mtime descending (most recently updated first)
        candidates.sort(key=_safe_mtime, reverse=True)

        workspace_name = self.workspace_root.name.lower()
        workspace_str = str(self.workspace_root).lower()

        # Candidate transcripts must match this workspace (or be explicitly specified)
        for c in candidates:
            try:
                prompt = self._extract_prompt_from_file(c)
                if prompt:
                    if transcript_path and c == Path(transcript_path):
                        return prompt
                    try:
                        with c.open("r", encoding="utf-8", errors="ignore") as sample_file:
                            content_sample = sample_file.read(50000)
                        if workspace_name in content_sample.lower() or workspace_str in content_sample.lower():
                            return prompt
                    except OSError:
                        pass
            except Exception:
                continue

        return None

    def _extract_prompt_from_file(self, transcript_file: Path) -> Optional[str]:
        """Reads transcript.jsonl and finds the latest USER_INPUT message."""
        try:
            with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
                lines = deque(f, maxlen=2000)
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "USER_INPUT" and entry.get("content"):
                        return entry["content"]
                except (json.JSONDecodeError, KeyError):
                    continue
        except (OSError, UnicodeDecodeError):
            return None
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
                if len(req_text) > 8 and not req_text.startswith("http"):
                    requirements.append(req_text)
            elif any(
                line_str.lower().startswith(prefix)
                for prefix in ("must ", "should ", "ensure ", "verify ", "implement ", "create ")
            ):
                if len(line_str) > 12:
                    requirements.append(line_str)

        return requirements

    def inspect_git(self) -> Tuple[bool, List[str], List[str], str]:
        """Runs git status and git diff to capture changed and untracked files."""
        # In linked worktrees and submodules, .git is a file rather than a directory.
        is_git = (self.workspace_root / ".git").exists()
        if not is_git:
            return False, [], [], ""

        changed: List[str] = []
        untracked: List[str] = []
        diff_text = ""

        try:
            status_res = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "-uall"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if status_res.returncode == 0:
                # NUL-delimited porcelain output preserves spaces and special characters.
                entries = status_res.stdout.split("\0")
                idx = 0
                while idx < len(entries):
                    entry = entries[idx]
                    idx += 1
                    if not entry:
                        continue
                    prefix = entry[:2]
                    filepath = entry[3:] if len(entry) > 3 else ""
                    paths_for_entry = [filepath]
                    # Rename/copy records include a second NUL-delimited path.
                    if ("R" in prefix or "C" in prefix) and idx < len(entries) and entries[idx]:
                        paths_for_entry.append(entries[idx])
                        idx += 1
                    for candidate in paths_for_entry:
                        candidate = candidate.replace("\\", "/")
                        if not candidate:
                            continue
                        # Skip files inside ignored directories.
                        if any(part in candidate.split("/") for part in self.IGNORED_DIRS):
                            continue
                        if prefix.startswith("?"):
                            untracked.append(candidate)
                        else:
                            changed.append(candidate)

            # Get diff of modified/staged files
            diff_res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if diff_res.returncode == 0:
                diff_text = diff_res.stdout
            else:
                diff_res2 = subprocess.run(
                    ["git", "diff"],
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                diff_text = diff_res2.stdout
        except (subprocess.SubprocessError, OSError, TimeoutError):
            diff_text = ""

        return True, changed, untracked, diff_text

    def read_workspace_files(self, paths: Optional[List[str]] = None) -> Dict[str, str]:
        """Reads contents of specified files, or scans text files in workspace if paths is None."""
        contents: Dict[str, str] = {}
        total_bytes = 0

        if paths is not None:
            for p in paths:
                file_path = self.workspace_root / p
                if not self.is_safe_path(file_path):
                    continue
                if file_path.is_file():
                    if not self.is_text_file(file_path):
                        continue
                    try:
                        if file_path.stat().st_size <= self.MAX_FILE_BYTES and total_bytes + file_path.stat().st_size <= self.MAX_WORKSPACE_BYTES and len(contents) < self.MAX_WORKSPACE_FILES:
                            contents[p] = file_path.read_text(encoding="utf-8", errors="replace")
                            total_bytes += file_path.stat().st_size
                    except OSError:
                        contents[p] = ""
                elif file_path.is_dir():
                    for root, dirs, files in os.walk(str(file_path)):
                        dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
                        for f in files:
                            sub_path = Path(root) / f
                            if not self.is_text_file(sub_path):
                                continue
                            if not self.is_safe_path(sub_path):
                                continue
                            try:
                                if sub_path.is_file():
                                    size = sub_path.stat().st_size
                                    if size <= self.MAX_FILE_BYTES and total_bytes + size <= self.MAX_WORKSPACE_BYTES and len(contents) < self.MAX_WORKSPACE_FILES:
                                        rel = os.path.relpath(str(sub_path), str(self.workspace_root))
                                        contents[rel] = sub_path.read_text(encoding="utf-8", errors="replace")
                                        total_bytes += size
                            except OSError:
                                continue
            return contents

        # Safe directory traversal
        for root, dirs, files in os.walk(str(self.workspace_root)):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
            for file in files:
                fpath = Path(root) / file
                if not self.is_text_file(fpath):
                    continue
                if not self.is_safe_path(fpath):
                    continue
                try:
                    if fpath.is_file():
                        size = fpath.stat().st_size
                        if size <= self.MAX_FILE_BYTES and total_bytes + size <= self.MAX_WORKSPACE_BYTES and len(contents) < self.MAX_WORKSPACE_FILES:
                            rel = os.path.relpath(str(fpath), str(self.workspace_root))
                            contents[rel] = fpath.read_text(encoding="utf-8", errors="replace")
                            total_bytes += size
                except OSError:
                    continue

        return contents

    def collect(
        self,
        explicit_prompt: Optional[str] = None,
        transcript_path: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> WorkspaceContext:
        """Assembles full WorkspaceContext, explicitly noting missing context."""
        limitations: List[str] = []
        prompt = explicit_prompt or self.find_transcript_prompt(transcript_path)

        has_explicit = True
        if not prompt:
            prompt = "UNAVAILABLE"
            has_explicit = False
            limitations.append("Original task prompt unavailable; reviewing changes against general standards.")

        requirements = self.parse_requirements(prompt) if has_explicit else []

        task = TaskSpec(
            task_id=task_id or ("task_auto" if has_explicit else "task_unspecified"),
            prompt=prompt,
            requirements=requirements,
        )

        is_git, changed, untracked, diff_text = self.inspect_git()
        if not is_git:
            limitations.append("Workspace is not a Git repository; full workspace files inspected without revision diffs.")

        all_relevant = list(dict.fromkeys(changed + untracked))
        file_contents = self.read_workspace_files(all_relevant if all_relevant else None)
        fingerprint = self.compute_workspace_fingerprint()

        return WorkspaceContext(
            task=task,
            workspace_root=self.workspace_root,
            changed_files=changed,
            untracked_files=untracked,
            git_diff=diff_text,
            file_contents=file_contents,
            is_git_repo=is_git,
            has_explicit_prompt=has_explicit,
            limitations=limitations,
            workspace_fingerprint=fingerprint,
        )

    def compute_workspace_fingerprint(self) -> str:
        """Computes a deterministic hash of text-file paths, metadata, and contents.

        Detects changes across Git and non-Git repositories, including same-size edits
        whose modification timestamp was preserved.
        """
        import hashlib
        h = hashlib.sha256()
        try:
            for root, dirs, files in os.walk(str(self.workspace_root)):
                dirs[:] = sorted([d for d in dirs if d not in self.IGNORED_DIRS])
                for f in sorted(files):
                    fpath = Path(root) / f
                    if not self.is_safe_path(fpath):
                        continue
                    if not self.is_text_file(fpath):
                        continue
                    try:
                        st = fpath.stat()
                        rel = os.path.relpath(str(fpath), str(self.workspace_root))
                        h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
                        with fpath.open("rb") as file_obj:
                            for chunk in iter(lambda: file_obj.read(65536), b""):
                                h.update(chunk)
                    except OSError:
                        continue
        except OSError:
            pass
        return h.hexdigest()
