"""Consent engine: decides whether a proposed action runs, needs approval, or is blocked.

One engine for every outbound flow: MCP actions now, A2A delegation (F5) and the
data broker (F7) later. A rule is a (tool_name, resource_pattern) pair; the
pattern is an fnmatch glob over the resource path relative to the sandbox root,
and "*" / "**" match any call to the tool. Paths outside the sandbox are denied
before rules are consulted, so no rule can ever allow them.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_core.sandbox import OutsideSandboxError, relative_in_root
from agent_core.storage import Rule, Store

Outcome = Literal["allow", "ask", "deny"]
DECISIONS = ("once", "always", "reject")
WILDCARDS = frozenset({"*", "**"})


@dataclass(frozen=True)
class ResourceSpec:
    """Which tool argument holds the path a rule is matched against."""

    arg: str
    root: Path


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    resource: str | None = None
    rule_id: int | None = None
    reason: str | None = None


class ConsentEngine:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def evaluate(self, tool_name: str, args: dict[str, Any], resource: ResourceSpec | None) -> Verdict:
        target = None
        if resource is not None and resource.arg in args:
            try:
                target = relative_in_root(resource.root, str(args[resource.arg]))
            except OutsideSandboxError as exc:
                return Verdict("deny", reason=str(exc))
        for rule in await self._store.active_rules(tool_name):
            if _matches(rule.pattern, target):
                return Verdict("allow", target, rule.id)
        return Verdict("ask", target)

    async def remember(self, tool_name: str, pattern: str = "**", *, session_id: str | None = None) -> int:
        rule_id = await self._store.add_rule(tool_name, pattern)
        await self._store.record(
            "rule_added", session_id=session_id, tool_name=tool_name, rule_id=rule_id, detail=pattern
        )
        return rule_id

    async def revoke(self, rule_id: int) -> bool:
        revoked = await self._store.revoke_rule(rule_id)
        if revoked:
            await self._store.record("rule_revoked", rule_id=rule_id)
        return revoked

    async def rules(self) -> list[Rule]:
        return await self._store.active_rules()

    async def always_allowed(self, tool_name: str) -> bool:
        return any(rule.pattern in WILDCARDS for rule in await self._store.active_rules(tool_name))


def _matches(pattern: str, target: str | None) -> bool:
    if pattern in WILDCARDS:
        return True
    return target is not None and fnmatch.fnmatch(target, pattern)
