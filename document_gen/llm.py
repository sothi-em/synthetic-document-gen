"""LLM access via OpenAI-compatible endpoints.

All LLM traffic (chat and embeddings) goes through OpenAI-compatible
endpoints — Ollama via its ``/v1`` route, llama.cpp, LM Studio, vLLM,
OpenAI, ... — using the ``openai`` Python client.

The chat (LLM) and embedding endpoints are configured independently via
:func:`load_settings`. Environment variables provide the defaults; saved
settings (written from the web UI, see :func:`save_settings`) override
them. Saved settings live in the TinyDB ``user_settings`` collection under
the ``"llm"`` key (see :mod:`document_gen.document_query`) and may contain
API keys.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import json_repair
from dotenv import load_dotenv

from document_gen import document_query
from document_gen.models.llm import EndpointConfig, LLMSettings

PURPOSES = ("chat", "embed")

#: Default timeout (seconds) for chat/completion requests.
DEFAULT_CHAT_TIMEOUT = 300.0

#: Environment variable overriding the chat request timeout (seconds).
CHAT_TIMEOUT_ENV = "LLM_TIMEOUT"


def chat_timeout() -> float:
    """Return the timeout (seconds) for chat/completion requests.

    The :data:`CHAT_TIMEOUT_ENV` environment variable wins when set to a
    positive number; otherwise :data:`DEFAULT_CHAT_TIMEOUT` (5 minutes)
    is used. Blank or malformed values fall back to the default.

    Returns:
        The effective timeout in seconds.
    """
    raw = os.getenv(CHAT_TIMEOUT_ENV)
    if raw and raw.strip():
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_CHAT_TIMEOUT
        if value > 0:
            return value
    return DEFAULT_CHAT_TIMEOUT


#: Output-token cap for free-text document generation
#: (:meth:`Backend.complete`).
#: Capping the response keeps long documents from stalling generation.
MAX_OUTPUT_TOKENS = 8142

#: Key under which LLM settings are stored in the ``user_settings``
#: TinyDB collection.
SETTINGS_KEY = "llm"

#: Extra request body sent to OpenAI-compatible servers to disable
#: reasoning/thinking output (supported by vLLM, llama.cpp, ...).
#: Used when a backend method is called with ``thinking=False``.
THINKING_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


def settings_path() -> Path:
    """Return the legacy settings file path.

    ``LLM_SETTINGS_PATH`` env override; defaults to
    ``data/llm-settings.json``. New settings are stored in the TinyDB
    ``user_settings`` collection; this file is only read once to migrate
    pre-existing settings (see :func:`load_settings`).
    """
    return Path(os.getenv("LLM_SETTINGS_PATH", "data/llm-settings.json"))


def env_defaults() -> LLMSettings:
    """Build the default settings from environment variables.

    Per-purpose variables: ``LLM_HOST`` / ``LLM_API_KEY`` / ``LLM_MODEL``
    for the chat endpoint and ``EMBED_HOST`` / ``EMBED_API_KEY`` /
    ``EMBED_MODEL`` for the embedding endpoint. ``*_HOST`` is an
    OpenAI-compatible base URL (e.g. ``http://localhost:11434/v1`` for
    Ollama, ``http://localhost:8080/v1`` for llama.cpp).

    Returns:
        An ``LLMSettings`` populated from the environment.
    """
    load_dotenv()

    def _endpoint(prefix: str) -> EndpointConfig:
        return EndpointConfig(
            host=os.getenv(f"{prefix}_HOST") or None,
            api_key=os.getenv(f"{prefix}_API_KEY") or None,
            model=os.getenv(f"{prefix}_MODEL") or None,
        )

    return LLMSettings(chat=_endpoint("LLM"), embed=_endpoint("EMBED"))


#: Per-process guard so the legacy settings file is imported at most once
#: per process (matters when the DB is in-memory and the file outlives
#: the setting, e.g. after :func:`clear_settings`).
_legacy_migration_done = False


def _migrate_legacy_settings_file() -> dict[str, Any] | None:
    """Import the pre-TinyDB settings file, if present and unimported.

    Runs at most once per process (see :data:`_legacy_migration_done`);
    subsequent calls return ``None`` even if the file is still present.

    Returns:
        The saved settings dict (now also stored under the ``"llm`` key
        in the ``user_settings`` collection), or ``None`` when no legacy
        file exists, it cannot be parsed, or the migration already ran.
    """
    global _legacy_migration_done
    if _legacy_migration_done:
        return None
    path = settings_path()
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as filereader:
            saved = json.load(filereader)
    except (OSError, json.JSONDecodeError):
        return None
    _legacy_migration_done = True
    document_query.set_setting(SETTINGS_KEY, saved)
    return saved


def load_settings() -> LLMSettings:
    """Load effective settings: env defaults overridden by saved settings.

    Saved settings are read from the TinyDB ``user_settings`` collection
    (``"llm"`` key). If none exist yet but a legacy settings file is
    present, it is imported into the collection first.

    Returns:
        The merged ``LLMSettings`` in effect.
    """
    settings = env_defaults()
    saved = document_query.get_setting(SETTINGS_KEY)
    if saved is None:
        saved = _migrate_legacy_settings_file()
    if saved is None:
        return settings
    for purpose in PURPOSES:
        saved_purpose = saved.get(purpose)
        if not saved_purpose:
            continue
        merged = settings.model_dump()[purpose]
        for key, value in saved_purpose.items():
            if value is not None:
                merged[key] = value
        setattr(settings, purpose, EndpointConfig(**merged))
    return settings


def save_settings(settings: LLMSettings) -> None:
    """Persist *settings* under the ``"llm"`` key in ``user_settings``.

    Args:
        settings: The full settings to store (both purposes).
    """
    document_query.set_setting(SETTINGS_KEY, settings.model_dump())
    invalidate_backend_cache()


def clear_settings() -> None:
    """Delete the saved LLM settings, falling back to env defaults."""
    document_query.delete_setting(SETTINGS_KEY)
    invalidate_backend_cache()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class Backend:
    """LLM backend for an OpenAI-compatible endpoint (``openai`` client)."""

    def __init__(self, config: EndpointConfig, client: Any = None) -> None:
        """Wrap an ``openai.OpenAI`` client.

        Args:
            config: Endpoint settings (base URL, API key, default model).
            client: Optional ``openai.OpenAI`` override (used in tests).
        """
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=config.host,
                api_key=config.api_key or "not-set",
                timeout=chat_timeout(),
            )
        self._config = config
        self._client = client

    def _model(self, model_name: str | None) -> str | None:
        return model_name or self._config.model

    def query(
        self,
        prompt: str,
        model: Any,
        deterministic: bool = False,
        seed: int = 0,
        model_name: str | None = None,
        thinking: bool = True,
    ) -> Any:
        """Structured completion via ``chat.completions``.

        OpenAI-compatible servers do not all support native JSON-schema
        output, so the schema is appended to the prompt and the response
        is repaired with ``json_repair``.

        Args:
            prompt: The prompt text to send.
            model: A Pydantic model describing the expected output.
            deterministic: When ``True``, the request runs at temperature 0 with
                the given seed; when ``False``, no sampling options are
                sent and the inference server's defaults apply.
            seed: Random seed used when ``deterministic`` is ``True``.
            model_name: Optional model ID override.
            thinking: When ``False``, disable model thinking/reasoning
                (sent as ``chat_template_kwargs.enable_thinking``).
        """
        options: dict[str, Any] = (
            {"temperature": 0, "seed": seed} if deterministic else {}
        )
        schema = model.model_json_schema()
        full_prompt = (
            f"{prompt}\n\nRespond with valid JSON matching this schema:\n"
            f"{json.dumps(schema)}"
        )
        response = self._client.chat.completions.create(
            model=self._model(model_name),
            messages=[{"role": "user", "content": full_prompt}],
            **({"extra_body": THINKING_EXTRA_BODY} if not thinking else {}),
            **options,
        )
        raw_response = response.choices[0].message.content or ""
        repaired = json_repair.loads(raw_response)
        return model.model_validate(repaired)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        deterministic: bool = False,
        seed: int = 0,
        model_name: str | None = None,
        max_tokens: int | None = MAX_OUTPUT_TOKENS,
        thinking: bool = True,
    ) -> str:
        """Free-text completion via ``chat.completions``.

        Unlike :meth:`query`, no JSON schema is appended: the raw text
        (e.g. markdown or HTML) is returned as-is.

        Args:
            prompt: The user prompt text.
            system: Optional system prompt prepended to the conversation.
            deterministic: When ``True``, the request runs at temperature 0 with
                the given seed; when ``False``, no sampling options are
                sent and the inference server's defaults apply.
            seed: Random seed used when ``deterministic`` is ``True``.
            model_name: Optional model ID override.
            max_tokens: Output-token cap sent as ``max_tokens``; pass
                ``None`` to leave the model default in force.
            thinking: When ``False``, disable model thinking/reasoning
                (sent as ``chat_template_kwargs.enable_thinking``).

        Returns:
            The model's raw text response.
        """
        options: dict[str, Any] = (
            {"temperature": 0, "seed": seed} if deterministic else {}
        )
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=self._model(model_name),
            messages=messages,
            **({"extra_body": THINKING_EXTRA_BODY} if not thinking else {}),
            **options,
        )
        return response.choices[0].message.content or ""

    def embed(self, texts: list[str], model: str | None = None) -> list[Any]:
        """Embed *texts* via ``embeddings.create``.

        Args:
            texts: The texts to embed.
            model: Optional embedding model ID override.
        """
        response = self._client.embeddings.create(model=self._model(model), input=texts)
        return [item.embedding for item in response.data]

    def list_models(self, timeout: float | None = None) -> list[str]:
        """List model IDs exposed by the endpoint.

        Args:
            timeout: Optional per-call timeout override (seconds); used
                by short connectivity probes so a downed server fails
                fast instead of waiting out the client timeout.
        """
        response = self._client.models.list(timeout=timeout)
        return [entry.id for entry in response.data]


def build_backend(config: EndpointConfig) -> Backend:
    """Instantiate the backend for *config*."""
    return Backend(config)


def build_async_client(config: EndpointConfig) -> Any:
    """Build an ``openai.AsyncOpenAI`` client for *config*.

    Used by the in-process agent (see :mod:`document_gen.agent`) for
    streaming chat completions with tools.

    Args:
        config: Endpoint settings (base URL, API key, default model).
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        base_url=config.host,
        api_key=config.api_key or "not-set",
        timeout=chat_timeout(),
    )


# ---------------------------------------------------------------------------
# Accessors (cached; invalidated on save/clear)
# ---------------------------------------------------------------------------

_cache: dict[str, Backend] = {}
_cache_lock = threading.Lock()


def _get_backend(purpose: str) -> Backend:
    with _cache_lock:
        if purpose not in _cache:
            settings = load_settings()
            config = settings.chat if purpose == "chat" else settings.embed
            _cache[purpose] = build_backend(config)
        return _cache[purpose]


def get_chat_backend() -> Backend:
    """Return the cached backend for the chat (LLM) endpoint."""
    return _get_backend("chat")


def get_embed_backend() -> Backend:
    """Return the cached backend for the embedding endpoint."""
    return _get_backend("embed")


def invalidate_backend_cache() -> None:
    """Drop cached backends so the next access re-reads the settings."""
    with _cache_lock:
        _cache.clear()
