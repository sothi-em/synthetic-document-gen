"""Tests for the LLM backend and settings persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from document_gen import document_query, llm
from document_gen.models import EndpointConfig, LLMSettings


class OutModel(BaseModel):
    """Trivial structured-output model for backend tests."""

    value: str


@pytest.fixture(autouse=True)
def isolated_settings(clean_settings: Path):
    """Isolate settings for every test in this module (see conftest)."""
    yield clean_settings


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            # No env vars: bare defaults.
            ({}, LLMSettings()),
            # Ollama via its OpenAI-compatible /v1 route.
            (
                {
                    "LLM_HOST": "http://ollama:11434/v1",
                    "LLM_MODEL": "llama3.2:latest",
                    "EMBED_HOST": "http://ollama:11434/v1",
                    "EMBED_MODEL": "nomic-embed-text:latest",
                },
                LLMSettings(
                    chat=EndpointConfig(
                        host="http://ollama:11434/v1",
                        model="llama3.2:latest",
                    ),
                    embed=EndpointConfig(
                        host="http://ollama:11434/v1",
                        model="nomic-embed-text:latest",
                    ),
                ),
            ),
            # Per-purpose variables, independent servers.
            (
                {
                    "LLM_HOST": "http://llamacpp:8080/v1",
                    "LLM_API_KEY": "secret",
                    "LLM_MODEL": "qwen2.5-7b",
                    "EMBED_HOST": "http://ollama:11434/v1",
                },
                LLMSettings(
                    chat=EndpointConfig(
                        host="http://llamacpp:8080/v1",
                        api_key="secret",
                        model="qwen2.5-7b",
                    ),
                    embed=EndpointConfig(host="http://ollama:11434/v1"),
                ),
            ),
        ],
    )
    def test_env_defaults(self, monkeypatch, env: dict, expected: LLMSettings) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert llm.env_defaults() == expected

    def test_load_without_file(self) -> None:
        assert llm.load_settings() == LLMSettings()

    def test_load_merges_saved_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_HOST", "http://env-host/v1")
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setenv("EMBED_MODEL", "env-embed-model")
        path = llm.settings_path()
        path.write_text(
            json.dumps({"chat": {"model": "saved-model"}, "embed": {}}),
            encoding="utf-8",
        )
        settings = llm.load_settings()
        # Saved value wins; env value survives for unset keys.
        assert settings.chat.model == "saved-model"
        assert settings.chat.host == "http://env-host/v1"
        assert settings.embed.model == "env-embed-model"

    def test_legacy_migration_runs_once_per_process(self) -> None:
        path = llm.settings_path()
        path.write_text(
            json.dumps({"chat": {"model": "legacy-model"}, "embed": {}}),
            encoding="utf-8",
        )
        # First load migrates the legacy file into the settings store.
        assert llm.load_settings().chat.model == "legacy-model"
        llm.clear_settings()
        # The per-process migration guard keeps the cleared state from
        # being re-seeded by the still-present legacy file.
        assert llm.load_settings().chat.model is None
        assert document_query.get_setting(llm.SETTINGS_KEY) is None

    def test_save_and_clear(self) -> None:
        settings = LLMSettings(chat=EndpointConfig(host="http://x/v1", api_key="k"))
        llm.save_settings(settings)
        stored = document_query.get_setting(llm.SETTINGS_KEY)
        assert stored is not None
        assert stored["chat"]["api_key"] == "k"
        assert llm.load_settings().chat.api_key == "k"

        llm.clear_settings()
        assert document_query.get_setting(llm.SETTINGS_KEY) is None
        assert llm.load_settings().chat.api_key is None

    def test_accessors_use_saved_endpoints_and_cache(self) -> None:
        llm.save_settings(
            LLMSettings(
                chat=EndpointConfig(host="http://chat/v1"),
                embed=EndpointConfig(host="http://embed/v1"),
            )
        )
        assert isinstance(llm.get_chat_backend(), llm.Backend)
        assert isinstance(llm.get_embed_backend(), llm.Backend)
        # Backends are cached until the cache is invalidated.
        first = llm.get_chat_backend()
        assert llm.get_chat_backend() is first
        llm.invalidate_backend_cache()
        assert llm.get_chat_backend() is not first


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class FakeOpenAIClient:
    """Records calls and returns canned OpenAI responses."""

    def __init__(self) -> None:
        self.chat_kwargs: dict[str, Any] | None = None
        self.embed_kwargs: dict[str, Any] | None = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create_chat)
        )
        self.embeddings = SimpleNamespace(create=self._create_embed)
        self.models = SimpleNamespace(list=self._list_models)

    def _create_chat(self, **kwargs) -> Any:
        self.chat_kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"value": "ok"}'))
            ]
        )

    def _create_embed(self, **kwargs) -> Any:
        self.embed_kwargs = kwargs
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.5, 0.6]) for _ in kwargs["input"]]
        )

    def _list_models(self, timeout: float | None = None) -> Any:
        return SimpleNamespace(
            data=[SimpleNamespace(id="a-7b"), SimpleNamespace(id="b-13b")]
        )


class TestBackend:
    @pytest.mark.parametrize("deterministic", [True, False])
    def test_query_appends_schema_and_validates(self, deterministic: bool) -> None:
        client = FakeOpenAIClient()
        backend = llm.Backend(EndpointConfig(model="qwen"), client=client)
        result = backend.query("make it", OutModel, deterministic=deterministic, seed=3)
        assert result.value == "ok"
        kwargs = client.chat_kwargs
        assert kwargs["model"] == "qwen"
        prompt = kwargs["messages"][0]["content"]
        assert "make it" in prompt
        assert "schema" in prompt
        assert "value" in prompt  # schema content included
        if deterministic:
            assert kwargs["temperature"] == 0
            assert kwargs["seed"] == 3
        else:
            assert "temperature" not in kwargs
            assert "seed" not in kwargs

    def test_embed(self) -> None:
        client = FakeOpenAIClient()
        backend = llm.Backend(EndpointConfig(model="default"), client=client)
        assert backend.embed(["x"]) == [[0.5, 0.6]]
        assert client.embed_kwargs["model"] == "default"
        # A per-call model override wins over the endpoint default.
        backend.embed(["x"], model="other")
        assert client.embed_kwargs["model"] == "other"

    def test_list_models(self) -> None:
        backend = llm.Backend(EndpointConfig(), client=FakeOpenAIClient())
        assert backend.list_models() == ["a-7b", "b-13b"]

    @pytest.mark.parametrize(
        ("kwargs", "expected_messages", "expect_max_tokens"),
        [
            # Deterministic with a system prompt.
            (
                dict(system="s", deterministic=True, seed=3),
                [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "p"},
                ],
                llm.MAX_OUTPUT_TOKENS,
            ),
            # Plain: user message only, no sampling options.
            (dict(), [{"role": "user", "content": "p"}], llm.MAX_OUTPUT_TOKENS),
            (dict(max_tokens=100), None, 100),
            (dict(max_tokens=None), None, "absent"),  # unlimited
        ],
    )
    def test_complete(self, kwargs, expected_messages, expect_max_tokens) -> None:
        client = FakeOpenAIClient()
        backend = llm.Backend(EndpointConfig(model="qwen"), client=client)
        # No schema appended, no JSON repair: raw text returned as-is.
        result = backend.complete("p", **kwargs)
        assert result == '{"value": "ok"}'
        call_kwargs = client.chat_kwargs
        assert call_kwargs["model"] == "qwen"
        if expected_messages is not None:
            assert call_kwargs["messages"] == expected_messages
        if expect_max_tokens == "absent":
            assert "max_tokens" not in call_kwargs
        else:
            assert call_kwargs["max_tokens"] == expect_max_tokens

    @pytest.mark.parametrize("thinking", [True, False])
    def test_thinking(self, thinking: bool) -> None:
        client = FakeOpenAIClient()
        backend = llm.Backend(EndpointConfig(model="qwen"), client=client)
        for call in (
            lambda: backend.query("p", OutModel, thinking=thinking),
            lambda: backend.complete("p", thinking=thinking),
        ):
            call()
            if thinking:
                assert "extra_body" not in client.chat_kwargs
            else:
                assert client.chat_kwargs["extra_body"] == llm.THINKING_EXTRA_BODY


class TestChatTimeout:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(llm.CHAT_TIMEOUT_ENV, raising=False)
        assert llm.chat_timeout() == llm.DEFAULT_CHAT_TIMEOUT == 300.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.CHAT_TIMEOUT_ENV, "600")
        assert llm.chat_timeout() == 600.0

    def test_env_float_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(llm.CHAT_TIMEOUT_ENV, "45.5")
        assert llm.chat_timeout() == 45.5

    def test_blank_or_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("", "  ", "not-a-number", "-5", "0"):
            monkeypatch.setenv(llm.CHAT_TIMEOUT_ENV, value)
            assert llm.chat_timeout() == llm.DEFAULT_CHAT_TIMEOUT


class TestBuildBackend:
    def test_build(self) -> None:
        assert isinstance(
            llm.build_backend(EndpointConfig(host="http://x/v1")), llm.Backend
        )
