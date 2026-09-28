"""Shared pytest fixtures for the document-gen test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from document_gen import document_query, llm

#: Environment variables that influence the default LLM settings.
LLM_ENV_VARS = (
    "LLM_HOST",
    "LLM_API_KEY",
    "LLM_MODEL",
    "EMBED_HOST",
    "EMBED_API_KEY",
    "EMBED_MODEL",
)


@pytest.fixture()
def clean_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate LLM settings: fresh in-memory store, no LLM env vars, fresh cache."""
    monkeypatch.setenv("LLM_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("TINYDB_PATH", raising=False)
    document_query.reset_db()
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Prevent the project's real .env from being re-read.
    monkeypatch.setattr(llm, "load_dotenv", lambda *args, **kwargs: None)
    # Reset the per-process legacy-settings migration guard.
    llm._legacy_migration_done = False
    llm.invalidate_backend_cache()
    yield tmp_path
    document_query.reset_db()
    llm.invalidate_backend_cache()
