"""Tests for the in-process LLM agent in ``document_gen.agent``."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from document_gen import agent, llm, server
from document_gen.models import EndpointConfig, LLMSettings

pytest.importorskip("fastapi")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _delta(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=_delta(content, tool_calls))])


def _tc(index, id=None, name=None, arguments=None):
    """One streaming tool-call delta."""
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeCompletions:
    """Canned streaming responses; one script per ``create`` call.

    A script is a list of chunks, or a callable ``(kwargs) -> list[chunk]``
    for dynamic scripts (e.g. reading a confirmation id from the history).
    """

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        script = self.scripts.pop(0)
        chunks = script(kwargs) if callable(script) else script

        async def gen():
            for chunk in chunks:
                yield chunk

        return gen()


class FakeAsyncClient:
    def __init__(self, scripts):
        self.chat = SimpleNamespace(completions=FakeCompletions(scripts))


def _install_client(monkeypatch, scripts) -> FakeAsyncClient:
    """Point the agent at a fake AsyncOpenAI with canned settings."""
    client = FakeAsyncClient(scripts)
    monkeypatch.setattr(
        llm,
        "load_settings",
        lambda: LLMSettings(
            chat=EndpointConfig(host="http://fake/v1", model="fake-model"),
            embed=EndpointConfig(host="http://fake/v1", model="fake-embed"),
        ),
    )
    monkeypatch.setattr(llm, "build_async_client", lambda config: client)
    return client


async def _collect(agen):
    return [frame async for frame in agen]


def _run(monkeypatch, scripts, message="hello", session_id="s1", screen=None):
    client = _install_client(monkeypatch, scripts)
    frames = asyncio.run(_collect(agent.run_agent(session_id, message, screen)))
    return client, frames


def _events(frames):
    """Parse SSE frames into ``(event, data)`` pairs."""
    events = []
    for frame in frames:
        lines = frame.split("\n")
        assert lines[0].startswith("event: "), frame
        assert lines[1].startswith("data: "), frame
        events.append(
            (lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :]))
        )
    return events


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class TestAgentLoop:
    def test_plain_text_turn(self, monkeypatch) -> None:
        client, frames = _run(monkeypatch, [[_chunk("Hello"), _chunk("!")]])
        assert all(frame.endswith("\n\n") for frame in frames)
        events = _events(frames)
        assert events == [
            ("token", {"text": "Hello"}),
            ("token", {"text": "!"}),
            ("done", {"text": "Hello!"}),
        ]
        # The session kept the user + assistant messages.
        session = agent._get_session("s1")
        assert [m["role"] for m in session.messages] == ["user", "assistant"]
        assert session.messages[1]["content"] == "Hello!"
        # The model id from the settings was used.
        assert client.chat.completions.calls[0]["model"] == "fake-model"

    def test_screen_prefix_in_user_message(self, monkeypatch) -> None:
        client, _ = _run(
            monkeypatch,
            [[_chunk("ok")]],
            message="ok",
            screen={"activeTab": "companies", "selectedCompanyId": 2},
        )
        messages = client.chat.completions.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        user = messages[-1]["content"]
        assert user.startswith("<screen>\n")
        assert '"activeTab": "companies"' in user
        assert "selectedCompanyId" in user
        assert user.endswith("ok")

    def test_tool_call_reassembly_and_execution(self, monkeypatch) -> None:
        # The tool-call arguments JSON is split across two chunks.
        script = [
            [
                _chunk(
                    tool_calls=[
                        _tc(0, id="call_1", name="list_industries", arguments='{"in')
                    ]
                ),
                _chunk(tool_calls=[_tc(0, arguments='dustries": []}')]),
            ],
            [_chunk("Done.")],
        ]
        monkeypatch.setattr(
            server, "list_industries", lambda: ["Agriculture", "Energy"]
        )
        _, frames = _run(monkeypatch, script)
        events = _events(frames)
        assert (
            "tool",
            {"name": "list_industries", "phase": "start", "args": {"industries": []}},
        ) in events
        assert (
            "tool",
            {
                "name": "list_industries",
                "phase": "end",
                "result": ["Agriculture", "Energy"],
            },
        ) in events
        assert events[-1] == ("done", {"text": "Done."})
        # The tool result was fed back into the history.
        session = agent._get_session("s1")
        tool_msg = session.messages[-2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"
        assert json.loads(tool_msg["content"]) == ["Agriculture", "Energy"]

    def test_session_history_persists_across_turns(self, monkeypatch) -> None:
        _run(monkeypatch, [[_chunk("first")]], message="first", session_id="s2")
        client, _ = _run(
            monkeypatch, [[_chunk("second")]], message="second", session_id="s2"
        )
        messages = client.chat.completions.calls[0]["messages"]
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "first"
        assert messages[2]["content"] == "first"  # assistant reply of turn 1
        assert messages[3]["content"] == "second"

    def test_tool_http_error_becomes_error_text(
        self, monkeypatch, clean_settings
    ) -> None:
        script = [
            [
                _chunk(
                    tool_calls=[
                        _tc(
                            0,
                            id="call_1",
                            name="get_company",
                            arguments='{"company_id": 99}',
                        )
                    ]
                )
            ],
            [_chunk("Not found.")],
        ]
        _, frames = _run(monkeypatch, script)
        events = _events(frames)
        assert (
            "tool",
            {
                "name": "get_company",
                "phase": "end",
                "result": "ERROR: Company not found",
            },
        ) in events
        assert events[-1] == ("done", {"text": "Not found."})

    def test_ui_tool_emits_ui_event(self, monkeypatch) -> None:
        script = [
            [
                _chunk(
                    tool_calls=[
                        _tc(
                            0,
                            id="call_1",
                            name="ui",
                            arguments='{"action": {"type": "navigate_tab", "tab": "companies"}}',
                        )
                    ]
                )
            ],
            [_chunk("Navigated.")],
        ]
        _, frames = _run(monkeypatch, script)
        events = _events(frames)
        assert (
            "ui",
            {"type": "navigate_tab", "tab": "companies"},
        ) in events
        assert (
            "tool",
            {"name": "ui", "phase": "end", "result": "OK"},
        ) in events
        assert events[-1] == ("done", {"text": "Navigated."})

    def test_unconfigured_chat_endpoint(self, monkeypatch) -> None:
        monkeypatch.setattr(
            llm,
            "load_settings",
            lambda: LLMSettings(
                chat=EndpointConfig(host=None, model=None),
                embed=EndpointConfig(host=None, model=None),
            ),
        )
        frames = asyncio.run(_collect(agent.run_agent("s9", "hi", None)))
        assert _events(frames) == [
            (
                "error",
                {
                    "message": "chat endpoint not configured (host and model are required)"
                },
            ),
        ]

    def test_busy_session_rejected(self, monkeypatch) -> None:
        _install_client(monkeypatch, [[_chunk("x")]])

        async def t():
            agent._BUSY.add("s3")
            try:
                return [frame async for frame in agent.run_agent("s3", "hi", None)]
            finally:
                agent._BUSY.discard("s3")

        frames = asyncio.run(t())
        assert _events(frames) == [("error", {"message": "already running"})]


# ---------------------------------------------------------------------------
# Destructive propose/confirm
# ---------------------------------------------------------------------------


class TestProposeConfirm:
    def test_propose_never_executes(self, monkeypatch, clean_settings) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            server,
            "delete_company",
            lambda company_id: calls.append(company_id) or {"deleted": True},
        )
        proposal = agent._tool_delete_company({"company_id": 3})
        assert "confirmation_id" in proposal
        assert "company 3" in proposal["summary"]
        assert calls == []

    def test_confirm_executes_stored_action(self, monkeypatch, clean_settings) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            server,
            "delete_company",
            lambda company_id: calls.append(company_id) or {"deleted": True},
        )
        proposal = agent._tool_delete_company({"company_id": 3})
        result = agent._tool_confirm_action(
            {"confirmation_id": proposal["confirmation_id"]}
        )
        assert result == {"deleted": True}
        assert calls == [3]
        # The proposal is consumed: a second confirm fails.
        with pytest.raises(agent.ToolError, match="expired or unknown"):
            agent._tool_confirm_action({"confirmation_id": proposal["confirmation_id"]})

    def test_confirm_unknown_id(self) -> None:
        with pytest.raises(agent.ToolError, match="expired or unknown"):
            agent._tool_confirm_action({"confirmation_id": "nope"})

    def test_confirm_expired_id(self, monkeypatch, clean_settings) -> None:
        monkeypatch.setattr(agent, "CONFIRMATION_TTL", -1)
        proposal = agent._tool_delete_company({"company_id": 3})
        with pytest.raises(agent.ToolError, match="expired or unknown"):
            agent._tool_confirm_action({"confirmation_id": proposal["confirmation_id"]})

    def test_propose_then_confirm_full_loop(self, monkeypatch, clean_settings) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            server,
            "delete_company",
            lambda company_id: calls.append(company_id) or {"deleted": True},
        )

        def confirm_from_history(kwargs):
            for msg in kwargs["messages"]:
                if msg.get("role") == "tool" and "confirmation_id" in msg["content"]:
                    cid = json.loads(msg["content"])["confirmation_id"]
                    return [
                        _chunk(
                            tool_calls=[
                                _tc(
                                    0,
                                    id="call_2",
                                    name="confirm_action",
                                    arguments=json.dumps({"confirmation_id": cid}),
                                )
                            ]
                        )
                    ]
            raise AssertionError("no confirmation id in history")

        script = [
            [
                _chunk(
                    tool_calls=[
                        _tc(
                            0,
                            id="call_1",
                            name="delete_company",
                            arguments='{"company_id": 3}',
                        )
                    ]
                )
            ],
            confirm_from_history,
            [_chunk("Deleted.")],
        ]
        _, frames = _run(monkeypatch, script)
        events = _events(frames)
        # The destructive tool only proposed; confirm_action executed it.
        proposal = next(
            data
            for event, data in events
            if event == "tool"
            and data["name"] == "delete_company"
            and data["phase"] == "end"
        )
        assert set(proposal["result"]) == {"confirmation_id", "summary"}
        assert proposal["result"]["summary"] == (
            "Delete company 3 and all its document types and documents."
        )
        assert (
            "tool",
            {"name": "confirm_action", "phase": "end", "result": {"deleted": True}},
        ) in events
        assert events[-1] == ("done", {"text": "Deleted."})
        assert calls == [3]


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------


class TestToolHandlers:
    def test_generate_pdf_builds_download_urls(self, monkeypatch) -> None:
        monkeypatch.setattr(
            server,
            "generate_company_pdf",
            lambda company_id, request: {
                "id": "j1",
                "status": "running",
                "total": 1,
            },
        )
        monkeypatch.setattr(
            server,
            "job_status",
            lambda job_id: SimpleNamespace(
                status="done",
                result={"documents": [{"pdf": "a.pdf", "report": "Annual Report"}]},
            ),
        )
        result = agent._tool_generate_pdf({"company_id": 7, "report": "Annual Report"})
        assert result == [
            {
                "pdf": "a.pdf",
                "report": "Annual Report",
                "url": "/api/companies/7/pdf/a.pdf",
            },
        ]

    def test_ui_tool_rejects_unknown_tab(self) -> None:
        with pytest.raises(agent.ToolError, match="unknown tab"):
            agent._tool_ui({"action": {"type": "navigate_tab", "tab": "nope"}})

    def test_unknown_tool_returns_error_text(self) -> None:
        result = asyncio.run(agent._run_tool("no_such_tool", {}))
        assert result == "ERROR: unknown tool 'no_such_tool'"

    def test_tool_schemas_are_openai_shaped(self) -> None:
        assert len(agent.TOOLS) == 32
        for name, tool in agent.TOOLS.items():
            entry = tool.to_openai()
            assert entry["type"] == "function"
            assert entry["function"]["name"] == name
            assert entry["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Server routes
# ---------------------------------------------------------------------------


class TestAgentRoutes:
    @pytest.fixture(autouse=True)
    def no_env_file(self, monkeypatch) -> None:
        """The app lifespan must not load the developer's real ``.env``."""
        monkeypatch.setattr(server, "load_dotenv", lambda *a, **k: None)

    def test_health_ok_when_configured(self, monkeypatch, clean_settings) -> None:
        _install_client(monkeypatch, [])
        from fastapi.testclient import TestClient

        response = TestClient(server.app).get("/api/agent/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_health_unavailable_when_unconfigured(self, clean_settings) -> None:
        from fastapi.testclient import TestClient

        response = TestClient(server.app).get("/api/agent/health")
        assert response.status_code == 503

    def test_chat_streams_sse(self, monkeypatch, clean_settings) -> None:
        _install_client(monkeypatch, [[_chunk("Hi "), _chunk("there.")]])
        from fastapi.testclient import TestClient

        with TestClient(server.app) as client:
            response = client.post(
                "/api/agent/chat",
                json={"sessionId": "web-1", "message": "hello", "screen": {}},
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert _events([f for f in response.text.split("\n\n") if f]) == [
            ("token", {"text": "Hi "}),
            ("token", {"text": "there."}),
            ("done", {"text": "Hi there."}),
        ]

    def test_agent_config_route_removed(self, clean_settings) -> None:
        from fastapi.testclient import TestClient

        response = TestClient(server.app).get("/api/agent-config")
        assert response.status_code == 404
