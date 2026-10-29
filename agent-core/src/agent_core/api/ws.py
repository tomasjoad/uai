"""WebSocket channel: the main one, it streams tokens and drives the interactive gate.

Client -> server: {"type": "user_message", "content"} | {"type": "approval", "tool_call_id", "decision", "pattern"?}
Server -> client: token | tool_call | approval_required | tool_result | done | error

Browsers cannot set headers on a WebSocket, so the token is also accepted as the
`token` query parameter (access logging is off, so it is not written anywhere).
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent_core.api.security import bearer_token, token_matches
from agent_core.orchestrator import Event, SessionNotFound

router = APIRouter()


@router.websocket("/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, session_id: str) -> None:
    token = bearer_token(websocket.headers.get("authorization")) or websocket.query_params.get("token")
    if not token_matches(token, websocket.app.state.token):
        await websocket.close(code=1008)
        return
    orch = websocket.app.state.orchestrator
    try:
        await orch.get_state(session_id)
    except SessionNotFound:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                await websocket.send_json(Event("error", {"message": "Messages must be JSON"}).to_dict())
                continue
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "user_message" and isinstance(message.get("content"), str) and message["content"].strip():
                events = orch.start_turn(session_id, message["content"])
            elif kind == "approval":
                events = orch.resume_after_approval(
                    session_id, str(message.get("tool_call_id")), str(message.get("decision")), message.get("pattern")
                )
            else:
                await websocket.send_json(Event("error", {"message": "Unsupported message"}).to_dict())
                continue
            async for event in events:
                await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        pass
    except SessionNotFound:
        await websocket.close(code=1008)
