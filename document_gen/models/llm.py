"""Configuration models for LLM endpoints.

All LLM traffic goes through OpenAI-compatible endpoints (Ollama via its
``/v1`` route, llama.cpp, LM Studio, vLLM, OpenAI, ...) using the ``openai``
Python client.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EndpointConfig(BaseModel):
    """Connection settings for one LLM endpoint (chat or embedding).

    Attributes:
        host: OpenAI-compatible base URL (e.g. ``http://localhost:11434/v1``
            for Ollama, ``http://localhost:8080/v1`` for llama.cpp).
        api_key: API key for the endpoint. Optional for local servers such
            as llama.cpp.
        model: Default model ID for this endpoint.
    """

    host: str | None = None
    api_key: str | None = None
    model: str | None = None


class LLMSettings(BaseModel):
    """Per-purpose LLM endpoint configuration.

    The chat (LLM) and embedding endpoints are independent: each may point
    at a different server.
    """

    chat: EndpointConfig = Field(default_factory=EndpointConfig)
    embed: EndpointConfig = Field(default_factory=EndpointConfig)
