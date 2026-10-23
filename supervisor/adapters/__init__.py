"""Platform adapters package for SUPERVISOR."""

from __future__ import annotations

from supervisor.adapters.antigravity import AntigravityAdapter
from supervisor.adapters.codex import CodexAdapter

__all__ = ["AntigravityAdapter", "CodexAdapter"]
