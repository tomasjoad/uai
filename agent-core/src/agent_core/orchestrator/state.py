"""Agent state carried by the LangGraph checkpointer, one thread per session.

Scopes: working (one turn: iteration, decisions, halt_reason), session (messages,
model_profile) and long-term (the user profile), which lives outside this state,
in the profile graph (F2).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    model_profile: str
    retrieved_context: list[str]  # F2: facts from the profile graph
    iteration: int
    max_iterations: int
    # tool_call_id -> {"decision": auto|once|always|reject|deny, "reason": ...}, set by the gate.
    decisions: dict[str, dict[str, Any]]
    halt_reason: str | None  # "max_iterations" | "refusal"
