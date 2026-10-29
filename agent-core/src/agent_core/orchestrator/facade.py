"""Orchestrator facade: the only entry point the API layer uses.

It hides LangGraph completely. Callers get neutral events and plain dicts, so the
orchestrator can be replaced (e.g. a Rust core for offline mobile) without
touching the API or the shell.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from agent_core.consent import DECISIONS, ConsentEngine
from agent_core.mcp.host import McpHost
from agent_core.models import ModelRouter
from agent_core.orchestrator.graph import message_text
from agent_core.storage import Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    """One item of the turn stream; mirrors the WebSocket contract."""

    type: str  # token | tool_call | approval_required | tool_result | done | error
    data: Any

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


class SessionNotFound(KeyError):
    pass


class UnknownProfile(ValueError):
    pass


class Orchestrator:
    def __init__(
        self,
        *,
        graph: CompiledStateGraph,
        store: Store,
        router: ModelRouter,
        consent: ConsentEngine,
        mcp: McpHost,
        max_iterations: int,
    ) -> None:
        self._graph = graph
        self._store = store
        self._router = router
        self._consent = consent
        self._mcp = mcp
        self._max_iterations = max_iterations
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Answers collected so far for a paused batch of tool calls, per session.
        # Kept in memory: after a restart the client simply answers again.
        self._answers: dict[str, dict[str, dict[str, Any]]] = {}

    # Sessions

    async def create_session(self, profile: str | None = None) -> dict[str, Any]:
        profile = profile or self._router.default
        if not self._router.has(profile):
            raise UnknownProfile(profile)
        session_id = uuid.uuid4().hex
        await self._store.create_session(session_id, profile)
        return {"session_id": session_id, "model_profile": profile}

    async def get_state(self, session_id: str) -> dict[str, Any]:
        session = await self._require(session_id)
        snapshot = await self._graph.aget_state(_config(session_id))
        values = snapshot.values or {}
        return {
            "session_id": session_id,
            "model_profile": session["model_profile"],
            "iteration": values.get("iteration", 0),
            "message_count": len(values.get("messages", [])),
            "pending_approvals": self._pending(session_id, snapshot),
        }

    async def delete_session(self, session_id: str) -> None:
        await self._require(session_id)
        await self._graph.checkpointer.adelete_thread(session_id)
        await self._store.delete_session(session_id)
        self._answers.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": entry.tool.name,
                "server": entry.server,
                "description": entry.tool.description,
                "requires_approval": not await self._consent.always_allowed(entry.tool.name),
            }
            for entry in self._mcp.entries()
        ]

    # Turns

    async def start_turn(self, session_id: str, content: str) -> AsyncIterator[Event]:
        session = await self._require(session_id)
        lock = self._locks[session_id]
        if lock.locked():
            yield Event("error", {"message": "Session is busy with another turn"})
            return
        async with lock:
            snapshot = await self._graph.aget_state(_config(session_id))
            if snapshot.interrupts:
                yield Event("error", {"message": "Resolve the pending approvals before sending a new message"})
                return
            graph_input = {
                "messages": [HumanMessage(content)],
                "model_profile": session["model_profile"],
                "retrieved_context": [],
                "iteration": 0,
                "max_iterations": self._max_iterations,
                "decisions": {},
                "halt_reason": None,
            }
            async for event in self._run(session_id, graph_input):
                yield event

    async def resume_after_approval(
        self, session_id: str, tool_call_id: str, decision: str, pattern: str | None = None
    ) -> AsyncIterator[Event]:
        """Record one answer; once the whole paused batch is answered, resume the turn."""
        await self._require(session_id)
        if decision not in DECISIONS:
            yield Event("error", {"message": f"decision must be one of {', '.join(DECISIONS)}"})
            return
        lock = self._locks[session_id]
        if lock.locked():
            yield Event("error", {"message": "Session is busy with another turn"})
            return
        async with lock:
            snapshot = await self._graph.aget_state(_config(session_id))
            pending_ids = [item["tool_call_id"] for item in self._pending(session_id, snapshot)]
            if tool_call_id not in pending_ids:
                yield Event("error", {"message": f"No pending approval for tool call {tool_call_id}"})
                return
            answers = self._answers.setdefault(session_id, {})
            answers[tool_call_id] = {"decision": decision, "pattern": pattern}
            if any(call_id not in answers for call_id in pending_ids):
                return  # wait for the rest of the batch
            resume = {call_id: answers[call_id] for call_id in pending_ids}
            del self._answers[session_id]
            async for event in self._run(session_id, Command(resume=resume)):
                yield event

    async def _run(self, session_id: str, graph_input: Any) -> AsyncIterator[Event]:
        config = _config(session_id)
        paused = False
        try:
            async for mode, payload in self._graph.astream(
                graph_input, config, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    chunk, metadata = payload
                    if metadata.get("langgraph_node") == "reason" and (text := message_text(chunk.content)):
                        yield Event("token", text)
                    continue
                for node, update in payload.items():
                    if node == "__interrupt__":
                        paused = True
                        for pause in update:
                            for item in pause.value["approvals"]:
                                yield Event("approval_required", item)
                    elif node == "reason" and update:
                        for call in update["messages"][-1].tool_calls:
                            yield Event("tool_call", await self._describe_call(call))
                    elif node == "act" and update:
                        for message in update["messages"]:
                            yield Event(
                                "tool_result",
                                {
                                    "id": message.tool_call_id,
                                    "ok": message.status != "error",
                                    "content": message_text(message.content),
                                },
                            )
        except Exception as exc:
            log.exception("Turn failed for session %s", session_id)
            yield Event("error", {"message": f"{type(exc).__name__}: {exc}"})
            return
        if paused:
            return
        values = (await self._graph.aget_state(config)).values
        yield Event("done", {"reply": _last_reply(values["messages"]), "halt_reason": values.get("halt_reason")})

    async def _describe_call(self, call: dict[str, Any]) -> dict[str, Any]:
        entry = self._mcp.get(call["name"])
        requires_approval = False
        if entry is not None:
            verdict = await self._consent.evaluate(call["name"], call["args"], entry.resource)
            requires_approval = verdict.outcome == "ask"
        return {"id": call["id"], "name": call["name"], "args": call["args"], "requires_approval": requires_approval}

    def _pending(self, session_id: str, snapshot: StateSnapshot) -> list[dict[str, Any]]:
        answers = self._answers.get(session_id, {})
        return [
            {**item, "decision": answers.get(item["tool_call_id"], {}).get("decision")}
            for pause in snapshot.interrupts
            for item in pause.value["approvals"]
        ]

    async def _require(self, session_id: str) -> dict[str, Any]:
        session = await self._store.get_session(session_id)
        if session is None:
            raise SessionNotFound(session_id)
        return session


def _config(session_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": session_id}}


def _last_reply(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message_text(message.content)
    return ""
