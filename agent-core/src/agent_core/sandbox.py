"""Path confinement shared by the consent gate and the filesystem MCP server.

Both sides resolve paths the same way, so a path that the gate approves is the
path the server touches, and neither accepts anything outside the root.
"""

from __future__ import annotations

import os
from pathlib import Path


class OutsideSandboxError(ValueError):
    pass


def resolve_in_root(root: Path, raw: str) -> Path:
    """Resolve `raw` (relative to `root`, or absolute) and require it to stay inside `root`.

    `resolve()` collapses `..` and follows symlinks/junctions of existing parts, so
    escapes through either are caught. Comparison is case-insensitive on Windows.
    """
    root = root.resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_within(resolved, root):
        raise OutsideSandboxError(f"Path is outside the sandbox: {raw}")
    return resolved


def relative_in_root(root: Path, raw: str) -> str:
    """Like `resolve_in_root`, but returns the POSIX path relative to the root ("." for the root)."""
    resolved = resolve_in_root(root, raw)
    return Path(os.path.relpath(resolved, root.resolve())).as_posix()


def _is_within(path: Path, root: Path) -> bool:
    p, r = os.path.normcase(str(path)), os.path.normcase(str(root))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # different drives on Windows
        return False
