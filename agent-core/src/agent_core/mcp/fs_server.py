"""Filesystem MCP server confined to one root folder (the user's sandbox).

Runs as a stdio subprocess of the core: `python -m agent_core.mcp.fs_server --root <dir>`.
It enforces the sandbox itself, so the consent gate is not the only barrier.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from agent_core.sandbox import resolve_in_root

MAX_READ_BYTES = 256 * 1024


def build_server(root: Path) -> FastMCP:
    root = root.resolve()
    server = FastMCP("filesystem")

    def display(path: Path) -> str:
        return Path(os.path.relpath(path, root)).as_posix()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_directory(path: str = ".") -> str:
        """List the files and folders in a sandbox directory. Paths are relative to the sandbox root."""
        target = resolve_in_root(root, path)
        if not target.is_dir():
            raise ValueError(f"Not a directory: {path}")
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        return "\n".join(f"[DIR] {e.name}" if e.is_dir() else f"[FILE] {e.name}" for e in entries) or "(empty)"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def read_file(path: str) -> str:
        """Read a UTF-8 text file from the sandbox."""
        target = resolve_in_root(root, path)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        with target.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES + 1)
        text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_READ_BYTES:
            text += f"\n[truncated at {MAX_READ_BYTES} bytes]"
        return text

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a UTF-8 text file in the sandbox. Missing parent folders are created."""
        target = resolve_in_root(root, path)
        if target == root or target.is_dir():
            raise ValueError(f"Not a file path: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {display(target)}"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    def create_directory(path: str) -> str:
        """Create a folder in the sandbox, including missing parents."""
        target = resolve_in_root(root, path)
        target.mkdir(parents=True, exist_ok=True)
        return f"Created {display(target)}"

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    build_server(args.root).run("stdio")


if __name__ == "__main__":
    main()
