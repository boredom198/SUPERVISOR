"""Stable identifiers shared by findings across processes and review cycles."""
from __future__ import annotations

import hashlib


def stable_suffix(*parts: object, digits: int = 4) -> str:
    """Return a deterministic numeric suffix; unlike hash(), stable across Python runs."""
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return f"{value % (10 ** digits):0{digits}d}"
