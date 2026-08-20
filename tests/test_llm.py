"""Tests for the LLM backend abstraction and settings persistence."""

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
            # Legacy Ollama variables.
            (
                {
                    "OLLAMA_HOST": "http://ollama:11434",
                    "OLLAMA_MODEL": "llama3.2:latest",
                    "OLLAMA_EMBED_MODEL": "nomic-embed-text:latest",
                },
                LLMSettings(
                    chat=EndpointConfig(
                        backend="ollama",
                        host="http://ollama:11434",
                        model="llama3.2:latest",
                    ),
                    embed=EndpointConfig(
                        backend="ollama",
                        host="http://ollama:11434",
                        model="nomic-embed-text:latest",
                    ),
                ),
            ),
            # Per-purpose variables.
            (
                {
                    "LLM_BACKEND": "openai",
                    "LLM_OPENAI_BASE_URL": "http://llamacpp:8080/v1",
                    "LLM_MODEL": "qwen2.5-7b",
                    "EMBED_HOST": "http://ollama:11434",
                },
                LLMSettings(
                    chat=EndpointConfig(
                        backend="openai",
                        host="http://llamacpp:8080/v1",
                        model="qwen2.5-7b",
                    ),
                    embed=EndpointConfig(backend="ollama", host="http://ollama:11434"),
                ),
            ),
            # Unknown backend falls back to ollama.
            ({"LLM_BACKEND": "groq"}, LLMSettings()),
        ],
    )
    def test_env_defaults(self, monkeypatch, env: dict, expected: LLMSettings) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert llm.env_defaults() == expected

    def test_load_without_file(self) -> None:
        assert llm.load_settings() == LLMSettings()

    def test_load_merges_saved_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://env-host")
        monkeypatch.setenv("OLLAMA_MODEL", "env-model")
        monkeypatch.setenv("OLLAMA_EMBED_MODEL", "env-embed-model")
        path = llm.settings_path()
        path.write_text(
            json.dumps({"chat": {"model": "saved-model"}, "embed": {}}),
            encoding="utf-8",
        )
        settings = llm.load_settings()
        # Saved value wins; env value survives for unset keys.
        assert settings.chat.model == "saved-model"
        assert settings.chat.host == "http://env-host"
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
        settings = LLMSettings(
            chat=EndpointConfig(backend="openai", host="http://x/v1", api_key="k")
        )
        llm.save_settings(settings)
        stored = document_query.get_setting(llm.SETTINGS_KEY)
        assert stored is not None
        assert stored["chat"]["api_key"] == "k"
        assert llm.load_settings().chat.api_key == "k"

        llm.clear_settings()
        assert document_query.get_setting(llm.SETTINGS_KEY) is None
        assert llm.load_settings().chat.api_key is None

    def test_accessors_respect_saved_backends_and_cache(self) -> None:
        llm.save_settings(
            LLMSettings(
                chat=EndpointConfig(backend="openai", host="http://x/v1"),
                embed=EndpointConfig(backend="ollama", host="http://o:11434"),
            )
        )
        assert isinstance(llm.get_chat_backend(), llm.OpenAIBackend)
        assert isinstance(llm.get_embed_backend(), llm.OllamaBackend)
        # Backends are cached until the cache is invalidated.
        first = llm.get_chat_backend()
        assert llm.get_chat_backend() is first
        llm.invalidate_backend_cache()
        assert llm.get_chat_backend() is not first


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class FakeOllamaClient:
    """Records calls and returns canned Ollama responses."""

    def __init__(self, embeddings: list[list[float]] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._embedding = embeddings[0] if embeddings else [0.1, 0.2]

    def chat(self, **kwargs) -> Any:
        self.calls.append(("chat", kwargs))
        return SimpleNamespace(message=SimpleNamespace(content='{"value": "ok"}'))

    def generate(self, **kwargs) -> Any:
        self.calls.append(("generate", kwargs))
        return SimpleNamespace(response='{"value": "ok"}')

    def embed(self, **kwargs) -> Any:
        self.calls.append(("embed", kwargs))
        return {"embeddings": [list(self._embedding) for _ in kwargs["input"]]}

    def list(self, timeout: float | None = None) -> Any:
        return {"models": [{"name": "m1:latest"}, {"name": "m2:latest"}]}


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


class TestOllamaBackend:
    def test_query_delegates_to_chat(self) -> None:
        client = FakeOllamaClient()
        backend = llm.OllamaBackend(
            EndpointConfig(model="default-model"), client=client
        )
        result = backend.query("prompt", OutModel, deterministic=True, seed=7)
        assert result.value == "ok"
        kind, kwargs = client.calls[0]
        assert kind == "chat"
        assert kwargs["model"] == "default-model"
        assert kwargs["options"] == {"temperature": 0, "seed": 7, "top_k": 1}
        # A per-call model override wins over the endpoint default.
        backend.query("p", OutModel, model_name="override")
        assert client.calls[1][1]["model"] == "override"

    def test_generate_uses_generate_endpoint(self) -> None:
        client = FakeOllamaClient()
        backend = llm.OllamaBackend(EndpointConfig(), client=client)
        result = backend.generate("p", OutModel)
        assert result.value == "ok"
        assert client.calls[0][0] == "generate"

    def test_embed(self) -> None:
        client = FakeOllamaClient()
        backend = llm.OllamaBackend(EndpointConfig(model="nomic"), client=client)
        assert backend.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
        kind, kwargs = client.calls[0]
        assert kind == "embed"
        assert kwargs["model"] == "nomic"

    def test_list_models(self) -> None:
        backend = llm.OllamaBackend(EndpointConfig(), client=FakeOllamaClient())
        assert backend.list_models() == ["m1:latest", "m2:latest"]

    @pytest.mark.parametrize(
        ("kwargs", "expected_options", "expected_messages"),
        [
            # Deterministic with a system prompt.
            (
                dict(system="s", deterministic=True, seed=3),
                {
                    "temperature": 0,
                    "seed": 3,
                    "top_k": 1,
                    "num_predict": llm.MAX_OUTPUT_TOKENS,
                },
                [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "p"},
                ],
            ),
            # Plain: user message only, capped at the default.
            (
                dict(),
                {"num_predict": llm.MAX_OUTPUT_TOKENS},
                [{"role": "user", "content": "p"}],
            ),
            (dict(max_tokens=100), {"num_predict": 100}, None),
            (dict(max_tokens=None), {}, None),  # unlimited
        ],
    )
    def test_complete(self, kwargs, expected_options, expected_messages) -> None:
        client = FakeOllamaClient()
        backend = llm.OllamaBackend(EndpointConfig(model="m"), client=client)
        # No JSON parsing: the raw model text is returned as-is.
        result = backend.complete("p", **kwargs)
        assert result == '{"value": "ok"}'
        kind, call_kwargs = client.calls[0]
        assert kind == "chat"
        assert "format" not in call_kwargs
        assert call_kwargs["options"] == expected_options
        if expected_messages is not None:
            assert call_kwargs["messages"] == expected_messages

    @pytest.mark.parametrize("thinking", [True, False])
    def test_thinking(self, thinking: bool) -> None:
        client = FakeOllamaClient()
        backend = llm.OllamaBackend(EndpointConfig(model="m"), client=client)
        backend.query("p", OutModel, thinking=thinking)
        backend.complete("p", thinking=thinking)
        for _, call_kwargs in client.calls:
            if thinking:
                assert "think" not in call_kwargs
            else:
                assert call_kwargs["think"] is False


class TestOpenAIBackend:
    @pytest.mark.parametrize("deterministic", [True, False])
    def test_query_appends_schema_and_validates(self, deterministic: bool) -> None:
        client = FakeOpenAIClient()
        backend = llm.OpenAIBackend(EndpointConfig(model="qwen"), client=client)
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

    def test_generate_maps_to_chat(self) -> None:
        client = FakeOpenAIClient()
        backend = llm.OpenAIBackend(EndpointConfig(model="qwen"), client=client)
        assert backend.generate("p", OutModel).value == "ok"
        assert client.chat_kwargs is not None

    def test_embed(self) -> None:
        client = FakeOpenAIClient()
        backend = llm.OpenAIBackend(EndpointConfig(model="default"), client=client)
        assert backend.embed(["x"]) == [[0.5, 0.6]]
        assert client.embed_kwargs["model"] == "default"
        # A per-call model override wins over the endpoint default.
        backend.embed(["x"], model="other")
        assert client.embed_kwargs["model"] == "other"

    def test_list_models(self) -> None:
        backend = llm.OpenAIBackend(EndpointConfig(), client=FakeOpenAIClient())
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
        backend = llm.OpenAIBackend(EndpointConfig(model="qwen"), client=client)
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
        backend = llm.OpenAIBackend(EndpointConfig(model="qwen"), client=client)
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
    def test_dispatch(self) -> None:
        assert isinstance(
            llm.build_backend(EndpointConfig(backend="openai", host="http://x")),
            llm.OpenAIBackend,
        )
        assert isinstance(
            llm.build_backend(EndpointConfig(backend="ollama")),
            llm.OllamaBackend,
        )
