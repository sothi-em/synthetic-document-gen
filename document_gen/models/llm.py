"""Configuration models for LLM backend endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EndpointConfig(BaseModel):
    """Connection settings for one LLM endpoint (chat or embedding).

    Attributes:
        backend: Which client to use. ``"ollama"`` talks to an Ollama
            server, ``"openai"`` to any OpenAI-compatible endpoint
            (llama.cpp, LM Studio, vLLM, OpenAI, ...).
        host: Ollama host (e.g. ``http://localhost:11434``) or OpenAI
            base URL (e.g. ``http://localhost:8080/v1``).
        api_key: API key for OpenAI-compatible endpoints. Optional for
            local servers such as llama.cpp.
        model: Default model ID for this endpoint.
    """

    backend: Literal["ollama", "openai"] = "ollama"
    host: str | None = None
    api_key: str | None = None
    model: str | None = None


class LLMSettings(BaseModel):
    """Per-purpose LLM endpoint configuration.

    The chat (LLM) and embedding endpoints are independent: each may use a
    different backend and server.
    """

    chat: EndpointConfig = Field(default_factory=EndpointConfig)
    embed: EndpointConfig = Field(default_factory=EndpointConfig)
