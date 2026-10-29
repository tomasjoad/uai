from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent_core.main import create_app
from agent_core.models import ModelRouter
from agent_core.settings import Settings


class ScriptedModel(BaseChatModel):
    """Replies from a fixed script and records every prompt it receives."""

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any):
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def proposes(*tool_calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(tool_calls))


class Harness:
    """The full app (real SQLite, real filesystem MCP subprocess) with a scripted model."""

    def __init__(self, settings: Settings, script: list[AIMessage]) -> None:
        self.settings = settings
        self.model = ScriptedModel(script=script)
        router = ModelRouter({"remote-haiku": {"provider": "anthropic", "model": "claude-haiku-5-5"}}, "remote-haiku")
        router.register("remote-haiku", self.model)
        self.client = TestClient(create_app(settings, models=router), base_url="http://127.0.0.1:8765")

    def __enter__(self) -> Harness:
        self.client.__enter__()
        self.auth = {"Authorization": f"Bearer {self.client.app.state.token}"}
        self.client.headers.update(self.auth)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.__exit__(*exc)

    @property
    def sandbox(self):
        return self.settings.sandbox_dir

    def ws_url(self, session_id: str) -> str:
        # TestClient ignores base_url for WebSockets; spell out the loopback host.
        return f"ws://127.0.0.1:8765/sessions/{session_id}/stream"

    def new_session(self) -> str:
        return self.client.post("/sessions").json()["session_id"]

    def say(self, session_id: str, content: str) -> dict[str, Any]:
        response = self.client.post(f"/sessions/{session_id}/messages", json={"content": content})
        assert response.status_code == 200, response.text
        return response.json()

    def answer(self, session_id: str, call_id: str, decision: str) -> dict[str, Any]:
        response = self.client.post(f"/sessions/{session_id}/approvals/{call_id}", json={"decision": decision})
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def harness(tmp_path):
    def make(script: list[AIMessage], **settings: Any) -> Harness:
        return Harness(Settings(data_dir=tmp_path / "data", **settings), script)

    return make
