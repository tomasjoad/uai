"""REST control API. Synchronous turns here are for CLI/curl testing; the gate
works the same way: a turn that needs approval returns `awaiting_approval`, and
POST .../approvals/{tool_call_id} resumes it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from agent_core import __version__
from agent_core.api.security import bearer_token, token_matches
from agent_core.consent import ConsentEngine
from agent_core.orchestrator import Event, Orchestrator


class CreateSession(BaseModel):
    profile: str | None = None


class UserMessage(BaseModel):
    content: str = Field(min_length=1)


class Approval(BaseModel):
    decision: Literal["once", "always", "reject"]
    pattern: str | None = None  # only for "always"; defaults to the whole sandbox ("**")


async def require_token(request: Request) -> None:
    if not token_matches(bearer_token(request.headers.get("authorization")), request.app.state.token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API token")


def orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def consent(request: Request) -> ConsentEngine:
    return request.app.state.consent


public = APIRouter()
router = APIRouter(dependencies=[Depends(require_token)])


@public.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "default_model_profile": request.app.state.models.default}


@router.get("/profiles")
async def profiles(request: Request) -> list[dict[str, Any]]:
    return request.app.state.models.describe()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(body: CreateSession | None = None, orch: Orchestrator = Depends(orchestrator)):
    return await orch.create_session(body.profile if body else None)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, orch: Orchestrator = Depends(orchestrator)):
    return await orch.get_state(session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, orch: Orchestrator = Depends(orchestrator)) -> None:
    await orch.delete_session(session_id)


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: UserMessage, orch: Orchestrator = Depends(orchestrator)):
    return await _collect(orch, session_id, orch.start_turn(session_id, body.content))


@router.post("/sessions/{session_id}/approvals/{tool_call_id}")
async def approve(session_id: str, tool_call_id: str, body: Approval, orch: Orchestrator = Depends(orchestrator)):
    events = orch.resume_after_approval(session_id, tool_call_id, body.decision, body.pattern)
    return await _collect(orch, session_id, events)


@router.get("/mcp/tools")
async def tools(orch: Orchestrator = Depends(orchestrator)):
    return await orch.list_tools()


@router.get("/rules")
async def rules(engine: ConsentEngine = Depends(consent)):
    return [rule.__dict__ for rule in await engine.rules()]


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_rule(rule_id: int, engine: ConsentEngine = Depends(consent)) -> None:
    if not await engine.revoke(rule_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active rule with that id")


@router.get("/trail")
async def trail(request: Request, session_id: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    return await request.app.state.store.trail(session_id, limit)


async def _collect(orch: Orchestrator, session_id: str, events: AsyncIterator[Event]) -> dict[str, Any]:
    tokens: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    errors: list[str] = []
    done: dict[str, Any] | None = None
    async for event in events:
        match event.type:
            case "token":
                tokens.append(event.data)
            case "tool_call":
                tool_calls.append(event.data)
            case "tool_result":
                tool_results.append(event.data)
            case "error":
                errors.append(event.data["message"])
            case "done":
                done = event.data
    state = await orch.get_state(session_id)
    if errors:
        turn_status = "error"
    elif state["pending_approvals"]:
        turn_status = "awaiting_approval"
    else:
        turn_status = "completed"
    return {
        "status": turn_status,
        "reply": done["reply"] if done else "".join(tokens),
        "halt_reason": done["halt_reason"] if done else None,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "pending_approvals": state["pending_approvals"],
        "errors": errors,
    }
