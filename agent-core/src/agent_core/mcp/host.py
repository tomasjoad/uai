"""MCP host: starts the local servers from mcp_servers.json and exposes their tools.

Each server is a stdio subprocess with one session kept open for the lifetime of
the core. The LLM only proposes tool calls; the orchestrator runs them through the
consent gate and then invokes the tools held here.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from agent_core.consent import ResourceSpec

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolEntry:
    server: str
    tool: BaseTool
    resource: ResourceSpec | None


class McpHost:
    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._entries: dict[str, ToolEntry] = {}

    @classmethod
    async def start(cls, config_path: Path, placeholders: dict[str, str]) -> McpHost:
        host = cls()
        servers = _load_config(config_path, placeholders)
        client = MultiServerMCPClient({name: server["connection"] for name, server in servers.items()})
        for name, server in servers.items():
            try:
                session = await host._stack.enter_async_context(client.session(name))
                tools = await load_mcp_tools(session, server_name=name)
            except Exception:
                log.exception("MCP server %r failed to start; continuing without it", name)
                continue
            for tool in tools:
                if tool.name in host._entries:
                    log.warning("Tool %r from %r clashes with another server; skipped", tool.name, name)
                    continue
                host._entries[tool.name] = ToolEntry(name, tool, server["resource"])
            log.info("MCP server %r ready with tools: %s", name, ", ".join(t.name for t in tools))
        return host

    async def aclose(self) -> None:
        await self._stack.aclose()

    @property
    def tools(self) -> list[BaseTool]:
        return [entry.tool for entry in self._entries.values()]

    def entries(self) -> list[ToolEntry]:
        return list(self._entries.values())

    def get(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)


def _load_config(path: Path, placeholders: dict[str, str]) -> dict[str, dict[str, Any]]:
    def fill(value: str) -> str:
        for key, replacement in placeholders.items():
            value = value.replace(key, replacement)
        return value

    raw = json.loads(path.read_text(encoding="utf-8"))
    servers = {}
    for name, cfg in raw.get("servers", {}).items():
        if not cfg.get("enabled", True):
            continue
        connection: dict[str, Any] = {
            "transport": "stdio",
            "command": fill(cfg["command"]),
            "args": [fill(arg) for arg in cfg.get("args", [])],
        }
        if "env" in cfg:
            connection["env"] = {key: fill(value) for key, value in cfg["env"].items()}
        resource = cfg.get("resource")
        servers[name] = {
            "connection": connection,
            "resource": ResourceSpec(resource["arg"], Path(fill(resource["root"]))) if resource else None,
        }
    return servers
