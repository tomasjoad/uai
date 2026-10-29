import pytest
from langchain_core.messages import AIMessage, ToolMessage
from starlette.websockets import WebSocketDisconnect

from conftest import call, proposes


def test_api_requires_the_token(harness):
    with harness([]) as h:
        assert h.client.get("/health").status_code == 200
        assert h.client.get("/profiles", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert h.client.get("/profiles").status_code == 200


def test_foreign_hosts_and_origins_are_rejected(harness):
    with harness([]) as h:
        assert h.client.get("/health", headers={"Host": "evil.example"}).status_code == 403
        assert h.client.get("/health", headers={"Origin": "https://evil.example"}).status_code == 403
        session_id = h.new_session()
        with pytest.raises(WebSocketDisconnect) as rejected:
            with h.client.websocket_connect(f"/sessions/{session_id}/stream", headers=h.auth):
                pass  # relative URL -> Host "testserver"
        assert rejected.value.reason == "Host not allowed"
        with pytest.raises(WebSocketDisconnect) as rejected:
            with h.client.websocket_connect(h.ws_url(session_id), headers={**h.auth, "Origin": "https://evil.example"}):
                pass
        assert rejected.value.reason == "Origin not allowed"
        with pytest.raises(WebSocketDisconnect) as rejected:
            with h.client.websocket_connect(h.ws_url(session_id), headers={"Authorization": "Bearer wrong"}):
                pass
        assert rejected.value.code == 1008
        token = h.auth["Authorization"].removeprefix("Bearer ")
        with h.client.websocket_connect(f"{h.ws_url(session_id)}?token={token}", headers={"Authorization": ""}):
            pass


def test_session_lifecycle(harness):
    with harness([]) as h:
        assert h.client.post("/sessions", json={"profile": "nope"}).status_code == 400
        assert h.client.get("/sessions/missing").status_code == 404
        session_id = h.new_session()
        state = h.client.get(f"/sessions/{session_id}").json()
        assert state["model_profile"] == "remote-haiku"
        assert state["pending_approvals"] == []
        assert h.client.delete(f"/sessions/{session_id}").status_code == 204
        assert h.client.get(f"/sessions/{session_id}").status_code == 404


def test_always_creates_a_rule_that_can_be_revoked(harness):
    script = [
        proposes(call("write_file", {"path": "notes/hello.txt", "content": "hola"}, "c1")),
        AIMessage("Guardado."),
        proposes(call("write_file", {"path": "notes/bye.txt", "content": "adios"}, "c2")),
        AIMessage("Guardado otra vez."),
        proposes(call("write_file", {"path": "x.txt", "content": "x"}, "c3")),
    ]
    with harness(script) as h:
        session_id = h.new_session()

        paused = h.say(session_id, "Guarda 'hola' en notes/hello.txt")
        assert paused["status"] == "awaiting_approval"
        assert paused["tool_calls"][0]["requires_approval"] is True
        assert [p["tool_call_id"] for p in paused["pending_approvals"]] == ["c1"]
        assert not (h.sandbox / "notes" / "hello.txt").exists()

        done = h.answer(session_id, "c1", "always")
        assert done["status"] == "completed"
        assert done["reply"] == "Guardado."
        assert done["tool_results"][0]["ok"] is True
        assert (h.sandbox / "notes" / "hello.txt").read_text(encoding="utf-8") == "hola"

        [rule] = h.client.get("/rules").json()
        assert (rule["tool_name"], rule["pattern"]) == ("write_file", "**")

        auto = h.say(session_id, "Ahora adios")
        assert auto["status"] == "completed"
        assert auto["tool_calls"][0]["requires_approval"] is False
        assert (h.sandbox / "notes" / "bye.txt").exists()

        decisions = [e for e in h.client.get("/trail", params={"session_id": session_id}).json() if e["kind"] == "decision"]
        assert [(e["decision"], e["rule_id"]) for e in reversed(decisions)] == [("always", rule["id"]), ("auto", rule["id"])]

        assert h.client.delete(f"/rules/{rule['id']}").status_code == 204
        assert h.client.get("/mcp/tools").json()
        again = h.say(session_id, "Y otra")
        assert again["status"] == "awaiting_approval"


def test_rejected_and_out_of_sandbox_calls_reach_the_model_as_errors(harness):
    script = [
        proposes(
            call("write_file", {"path": "../escape.txt", "content": "x"}, "c1"),
            call("write_file", {"path": "ok.txt", "content": "y"}, "c2"),
        ),
        AIMessage("Vale, no lo hago."),
    ]
    with harness(script) as h:
        session_id = h.new_session()
        paused = h.say(session_id, "Escribe dos ficheros")
        # The escape is denied by policy without asking; only c2 needs an answer.
        assert [p["tool_call_id"] for p in paused["pending_approvals"]] == ["c2"]

        done = h.answer(session_id, "c2", "reject")
        assert done["status"] == "completed"
        results = {r["id"]: r for r in done["tool_results"]}
        assert not results["c1"]["ok"] and "outside the sandbox" in results["c1"]["content"]
        assert not results["c2"]["ok"] and "rejected" in results["c2"]["content"]
        assert not (h.sandbox / "ok.txt").exists()
        assert not (h.settings.data_dir / "escape.txt").exists()

        last_prompt = h.model.calls[-1]
        tool_messages = [m for m in last_prompt if isinstance(m, ToolMessage)]
        assert [m.status for m in tool_messages] == ["error", "error"]


def test_websocket_turn_with_a_batch_of_approvals(harness):
    script = [
        proposes(
            call("write_file", {"path": "a.txt", "content": "A"}, "c1"),
            call("create_directory", {"path": "docs"}, "c2"),
        ),
        AIMessage("Hecho todo."),
    ]
    with harness(script) as h:
        session_id = h.new_session()
        with h.client.websocket_connect(h.ws_url(session_id), headers=h.auth) as ws:
            ws.send_json({"type": "user_message", "content": "Haz dos cosas"})
            first = [ws.receive_json() for _ in range(4)]
            assert [e["type"] for e in first] == ["tool_call", "tool_call", "approval_required", "approval_required"]

            ws.send_json({"type": "approval", "tool_call_id": "c1", "decision": "once"})
            ws.send_json({"type": "approval", "tool_call_id": "c2", "decision": "once"})
            rest = []
            while not rest or rest[-1]["type"] not in ("done", "error"):
                rest.append(ws.receive_json())

        assert [e["type"] for e in rest if e["type"] == "tool_result"] == ["tool_result", "tool_result"]
        assert all(e["data"]["ok"] for e in rest if e["type"] == "tool_result")
        assert "".join(e["data"] for e in rest if e["type"] == "token") == "Hecho todo."
        assert rest[-1] == {"type": "done", "data": {"reply": "Hecho todo.", "halt_reason": None}}
        assert (h.sandbox / "a.txt").exists() and (h.sandbox / "docs").is_dir()


def test_new_messages_wait_for_pending_approvals(harness):
    with harness([proposes(call("list_directory", {"path": "."}, "c1"))]) as h:
        session_id = h.new_session()
        assert h.say(session_id, "Lista")["status"] == "awaiting_approval"
        blocked = h.say(session_id, "Otra cosa")
        assert blocked["status"] == "error"
        assert "pending approvals" in blocked["errors"][0]
        assert h.answer(session_id, "missing", "once")["status"] == "error"


def test_iteration_limit_stops_the_loop(harness):
    with harness([proposes(call("list_directory", {"path": "."}, "c1"))], max_iterations=1) as h:
        session_id = h.new_session()
        h.say(session_id, "Lista")
        done = h.answer(session_id, "c1", "once")
        assert done["status"] == "completed"
        assert done["halt_reason"] == "max_iterations"


def test_a_pending_approval_survives_a_restart(harness):
    with harness([proposes(call("create_directory", {"path": "kept"}, "c1"))]) as h:
        session_id = h.new_session()
        assert h.say(session_id, "Crea kept")["status"] == "awaiting_approval"

    with harness([AIMessage("Listo.")]) as h:
        state = h.client.get(f"/sessions/{session_id}").json()
        assert [p["tool_call_id"] for p in state["pending_approvals"]] == ["c1"]
        done = h.answer(session_id, "c1", "once")
        assert done["reply"] == "Listo."
        assert (h.sandbox / "kept").is_dir()
