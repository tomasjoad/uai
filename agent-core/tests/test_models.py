import os
from importlib import resources
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage

from agent_core.models import ModelRouter, UnknownProfileError

load_dotenv()

PROFILES = Path(str(resources.files("agent_core") / "config" / "model_profiles.yaml"))


def test_profiles_are_listed_without_secrets():
    router = ModelRouter.from_file(PROFILES)
    described = {p["name"]: p for p in router.describe()}
    assert described["remote-haiku"]["model"] == "claude-haiku-5-5"
    assert described["remote-haiku"]["default"] is True
    assert all(set(p) == {"name", "default", "description", "provider", "model"} for p in described.values())
    with pytest.raises(UnknownProfileError):
        router.get("nope")


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_haiku_profile_round_trips_a_tool_call():
    """Smoke test against the real API: tool call proposed, result accepted, final answer."""
    write_file = {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file in the user's sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    }
    model = ModelRouter.from_file(PROFILES).get("remote-haiku").bind_tools([write_file])
    messages = [HumanMessage("Create notes.txt in the sandbox containing the word hola.")]
    proposal = model.invoke(messages)
    assert proposal.tool_calls and proposal.tool_calls[0]["name"] == "write_file"

    result = ToolMessage("Wrote 4 characters to notes.txt", tool_call_id=proposal.tool_calls[0]["id"])
    final = model.invoke([*messages, proposal, result])
    assert final.response_metadata["stop_reason"] == "end_turn"
    assert final.text
