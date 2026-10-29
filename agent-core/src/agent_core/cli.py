"""Minimal terminal client for a running core: chat plus the approval gate."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from websockets.asyncio.client import connect

from agent_core.settings import Settings

CHOICES = {"o": "once", "a": "always", "r": "reject"}


async def chat(settings: Settings, token: str, session_id: str | None, profile: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    if session_id is None:
        async with httpx.AsyncClient(base_url=settings.base_url, headers=headers) as http:
            response = await http.post("/sessions", json={"profile": profile})
            response.raise_for_status()
            session_id = response.json()["session_id"]
    print(f"Session {session_id} - type /exit to quit.")

    uri = f"ws://{settings.host}:{settings.port}/sessions/{session_id}/stream"
    async with connect(uri, additional_headers=headers) as websocket:
        while True:
            text = (await asyncio.to_thread(input, "\nyou> ")).strip()
            if text in ("/exit", "/quit"):
                return
            if text:
                await websocket.send(json.dumps({"type": "user_message", "content": text}))
                await _print_turn(websocket)


async def _print_turn(websocket) -> None:
    print("agent> ", end="", flush=True)
    while True:
        event = json.loads(await websocket.recv())
        kind, data = event["type"], event["data"]
        if kind == "token":
            print(data, end="", flush=True)
        elif kind == "tool_call":
            print(f"\n  -> {data['name']}({json.dumps(data['args'], ensure_ascii=False)})")
        elif kind == "approval_required":
            decision = await _ask(data)
            await websocket.send(
                json.dumps({"type": "approval", "tool_call_id": data["tool_call_id"], "decision": decision})
            )
        elif kind == "tool_result":
            preview = data["content"].replace("\n", " ")[:120]
            print(f"  <- {'ok' if data['ok'] else 'error'}: {preview}")
        elif kind == "error":
            print(f"\n[error] {data['message']}")
            return
        elif kind == "done":
            if data.get("halt_reason"):
                print(f"\n[stopped: {data['halt_reason']}]", end="")
            print()
            return


async def _ask(data: dict) -> str:
    prompt = f"  ? Allow {data['name']}? [o]nce / [a]lways / [r]eject: "
    while True:
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()[:1]
        if answer in CHOICES:
            return CHOICES[answer]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="resume an existing session id")
    parser.add_argument("--profile", help="model profile for a new session")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.token_path.exists():
        raise SystemExit(f"No API token at {settings.token_path}. Start the core first: uv run agent-core")
    token = settings.token_path.read_text(encoding="utf-8").strip()
    try:
        asyncio.run(chat(settings, token, args.session, args.profile))
    except (KeyboardInterrupt, EOFError):
        pass
