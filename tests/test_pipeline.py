"""Pipeline prompt tests with the LLM backend stubbed out.

The pipeline normally requires a live LLM server; these tests monkeypatch
``get_chat_backend`` so only the prompt construction is exercised.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from document_gen import pipeline
from document_gen.models import CompanyProfile, SyntheticCompany


class FakeChatBackend:
    """Records the company-generation prompts it is asked to complete."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def query(
        self, prompt, model, seed=0, deterministic=False, model_name=None, **kwargs
    ):
        # The document-types query uses a dynamic model with a ``documents``
        # field; return an empty list for it and only record the company
        # prompt.
        if "documents" in getattr(model, "model_fields", {}):
            return SimpleNamespace(documents=[])
        self.prompts.append(prompt)
        return SyntheticCompany(
            name="Acme Corp",
            industry="Hospitality",
            description="A fictional hospitality group.",
            headquarters="Austin, Texas",
            size="mid",
        )


@pytest.fixture()
def fake_backend(monkeypatch) -> FakeChatBackend:
    backend = FakeChatBackend()
    monkeypatch.setattr(pipeline, "get_chat_backend", lambda: backend)
    return backend


class TestCompanyPromptVariation:
    def test_distinct_seeds_yield_distinct_prompts(self, fake_backend) -> None:
        """Regression: greedy decoding (top_k=1) makes the output a pure
        function of the prompt, so identical prompts produced identical
        companies. The seed must be part of the prompt."""
        profiles = [
            pipeline.generate_company_profile(
                company=CompanyProfile(seed=seed),
                target_industry="Hospitality",
                log_output=False,
            )
            for seed in (1, 2, 3)
        ]
        prompts = fake_backend.prompts
        assert len(prompts) == 3
        assert len(set(prompts)) == 3, "prompts for different seeds must differ"
        for seed, prompt in zip((1, 2, 3), prompts, strict=True):
            assert f"generation seed {seed}" in prompt
        # The generated profiles recorded their own seed.
        assert [p.seed for p in profiles] == [1, 2, 3]

    def test_same_seed_yields_same_prompt(self, fake_backend) -> None:
        """Reproducibility: the same seed must produce the same prompt."""
        for _ in range(2):
            pipeline.generate_company_profile(
                company=CompanyProfile(seed=42),
                target_industry="Hospitality",
                log_output=False,
            )
        assert fake_backend.prompts[0] == fake_backend.prompts[1]

    def test_user_input_is_included_in_prompt(self, fake_backend) -> None:
        pipeline.generate_company_profile(
            company=CompanyProfile(seed=7),
            target_industry="Hospitality",
            log_output=False,
            user_input="a boutique hotel chain",
        )
        prompt = fake_backend.prompts[0]
        assert "a boutique hotel chain" in prompt
        assert "Hospitality" in prompt
