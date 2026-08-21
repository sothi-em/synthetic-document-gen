"""Tests for the LLM-driven PNG image document generation pipeline."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from document_gen import document_pdf, document_png, document_query
from document_gen.models import (
    CompanyProfile,
    DistressOptions,
    DocumentPlan,
    DocumentType,
    FigureExtraction,
    SyntheticCompany,
)

# 1x1 transparent PNG (enough for the base64 figure embedding).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh in-memory store; no DOCUMENTS_DIR / settings interference."""
    monkeypatch.setenv("LLM_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("TINYDB_PATH", raising=False)
    monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
    document_query.reset_db()
    yield tmp_path
    document_query.reset_db()


def _save_company() -> int:
    """Store a company with two report types and return its doc_id."""
    profile = SyntheticCompany(
        name="Acme Corp",
        industry="Finance",
        description="A fictional finance company.",
        headquarters="Boston, Massachusetts",
        size="mid",
    )
    company = CompanyProfile(
        profile=profile,
        reports=[
            DocumentType(
                name="Onboarding Guide",
                category="Guide",
                purpose="New hire orientation",
            ),
            DocumentType(
                name="Quarterly Earnings",
                category="Investor",
                purpose="Quarterly release",
            ),
        ],
        seed=42,
    )
    return document_query.save_company(company)


class FakeBackend:
    """Canned chat backend: plan for the structured call, markdown + HTML
    for the two completion calls (the HTML call carries a system prompt).

    :meth:`query` serves the document plan (or the figure-extraction
    fallback, by model type) and records calls; :meth:`complete` serves
    the markdown draft (no system prompt) or the HTML document (system
    prompt set).
    """

    #: Plan returned for the stage-0 document-plan call.
    PLAN = DocumentPlan(
        include_toc=False,
        toc_reason="Single-page image document: no TOC.",
        design_direction="Clean fintech look with navy header bands.",
        palette=["#1F3A5F", "#7A9CC6", "#FFFFFF"],
        typography="serif headings, sans-serif body",
        layout_style="modern minimal",
    )

    MARKDOWN = "# Q3 Sales Image\n\n## Section\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"

    HTML = (
        "<!DOCTYPE html><html><head><title>Q3</title></head>"
        "<body><h1>Q3 Sales Image</h1><p>Hello</p></body></html>"
    )

    #: Specs returned by :meth:`query` (the LLM figure-extraction fallback).
    QUERY_FIGURES: list[dict[str, Any]] = [
        {
            "kind": "bar",
            "title": "Trend",
            "labels": ["2021", "2022"],
            "series": [{"name": "Revenue", "values": [100, 120]}],
        }
    ]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        deterministic: bool = False,
        seed: int = 0,
        model_name: str | None = None,
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "deterministic": deterministic,
                "seed": seed,
                "model_name": model_name,
                "max_tokens": max_tokens,
                "thinking": thinking,
            }
        )
        # The HTML call carries the system prompt; the markdown call does not.
        return self.HTML if system is not None else self.MARKDOWN

    def query(
        self,
        prompt: str,
        model: Any,
        deterministic: bool = False,
        seed: int = 0,
        model_name: str | None = None,
        thinking: bool = True,
    ) -> Any:
        self.query_calls.append(
            {
                "prompt": prompt,
                "model": model,
                "deterministic": deterministic,
                "seed": seed,
                "model_name": model_name,
                "thinking": thinking,
            }
        )
        if model is DocumentPlan:
            return self.PLAN
        return model(figures=self.QUERY_FIGURES)


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: FakeBackend,
    **kwargs: Any,
) -> document_png.ImageArtifact:
    """Run the pipeline with *backend* canned in and return the artifact."""
    company_id = _save_company()
    monkeypatch.setattr(document_png, "get_chat_backend", lambda: backend)
    return document_png.generate_document_image(
        company_id, "Onboarding Guide", output_dir=tmp_path, **kwargs
    )


def _check_trace(
    artifact: document_png.ImageArtifact, backend: FakeBackend, company_id: int
) -> None:
    """Assert the aggregated per-stage trace is complete and DB-stored."""
    trace = artifact.gen_tracing
    assert trace is not None
    stages = trace["stages"]
    # Stage order: plan -> markdown -> figures -> html -> png -> distress.
    assert list(stages) == [
        "plan",
        "markdown",
        "figures",
        "html",
        "png",
        "distress",
    ]
    assert trace["company_id"] == company_id
    assert trace["report"] == "Onboarding Guide"
    assert "started_at" in trace and "finished_at" in trace
    assert trace["total_elapsed_s"] >= 0

    # Plan stage: rendered prompt + structured output (no fallback).
    assert "Acme Corp" in stages["plan"]["prompt"]
    assert stages["plan"]["output"] == FakeBackend.PLAN.model_dump(mode="json")
    assert stages["plan"]["used_default_fallback"] is False

    # Markdown stage: prompt and raw LLM output.
    assert "focus on Q3" in stages["markdown"]["prompt"]
    assert stages["markdown"]["output"] == FakeBackend.MARKDOWN

    # Figures stage: no kinds requested -> nothing extracted.
    assert stages["figures"]["requested_kinds"] == []
    assert stages["figures"]["specs"] == []
    assert stages["figures"]["llm"] is None

    # HTML stage: prompts, raw output, and the sanitized HTML.
    assert FakeBackend.MARKDOWN in stages["html"]["prompt"]
    assert "Clean fintech look with navy header bands." in stages["html"]["prompt"]
    assert stages["html"]["raw_output"] == FakeBackend.HTML
    assert stages["html"]["sanitized_html"] == artifact.html
    # The system prompt carries the page size and the no-furniture rules.
    assert "A4 portrait" in stages["html"]["system_prompt"]
    assert "one page" in stages["html"]["system_prompt"]

    # Png stage: the written file.
    assert Path(stages["png"]["path"]) == artifact.png_path
    assert stages["png"]["size_bytes"] > 0

    # Distress stage: generated images are left undistressed by
    # default (perfect render); the pass only runs when explicitly
    # enabled.
    assert stages["distress"]["enabled"] is False
    assert stages["distress"]["seed"] is None
    assert stages["distress"]["options"]["enabled"] is False


def test_trace_persistence_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = _save_company()
    monkeypatch.setattr(document_png, "get_chat_backend", lambda: FakeBackend())

    # Default: the trace is built and returned on the artifact…
    artifact = document_png.generate_document_image(
        company_id, "Onboarding Guide", output_dir=tmp_path
    )
    assert artifact.gen_tracing is not None
    # …but the record carries no gen_tracing field.
    record = document_query.list_documents(company_id=company_id)[0]
    assert "gen_tracing" not in record

    # Flagged: the trace is stored on the record.
    artifact = document_png.generate_document_image(
        company_id, "Onboarding Guide", output_dir=tmp_path, gen_tracing=True
    )
    record = document_query.list_documents(company_id=company_id)[0]
    assert record["gen_tracing"] == artifact.gen_tracing


# ---------------------------------------------------------------------------
# save_original_png
# ---------------------------------------------------------------------------


class TestSaveOriginalPng:
    def test_copy_is_byte_identical(self, tmp_path: Path) -> None:
        src = tmp_path / "foo.png"
        src.write_bytes(b"fake-png-bytes")
        original = document_png.save_original_png(src)
        assert original == tmp_path / "foo_original.png"
        assert original.read_bytes() == src.read_bytes()

    def test_source_is_left_untouched(self, tmp_path: Path) -> None:
        src = tmp_path / "foo.png"
        src.write_bytes(b"fake-png-bytes")
        document_png.save_original_png(src)
        assert src.read_bytes() == b"fake-png-bytes"

    def test_collision_gets_numeric_suffix(self, tmp_path: Path) -> None:
        src = tmp_path / "foo.png"
        src.write_bytes(b"fake-png-bytes")
        (tmp_path / "foo_original.png").write_bytes(b"existing")
        original = document_png.save_original_png(src)
        assert original.name == "foo_original_1.png"
        assert original.read_bytes() == src.read_bytes()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestGenerateDocumentImage:
    def test_end_to_end(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_png, "get_chat_backend", lambda: backend)

        artifact = document_png.generate_document_image(
            company_id,
            "Onboarding Guide",
            user_input="focus on Q3",
            output_dir=tmp_path,
            gen_tracing=True,
        )

        assert artifact.company_id == company_id
        assert artifact.report_name == "Onboarding Guide"
        assert artifact.markdown == FakeBackend.MARKDOWN
        assert artifact.png_path.exists()
        assert artifact.png_path.parent == tmp_path
        # File name comes from the markdown's document title.
        assert artifact.png_path.name == "q3_sales_image.png"
        assert artifact.png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

        # Two chat calls (markdown, HTML) and one structured call (plan).
        assert len(backend.calls) == 2
        assert backend.calls[0]["system"] is None
        assert "focus on Q3" in backend.calls[0]["prompt"]
        assert "Acme Corp" in backend.calls[0]["prompt"]
        assert backend.calls[1]["system"] is not None
        assert "A4 portrait" in backend.calls[1]["system"]
        assert len(backend.query_calls) == 1
        assert backend.query_calls[0]["model"] is DocumentPlan
        # Non-deterministic (server sampling defaults), seeded; thinking
        # on (image documents have no quick-doc variant).
        assert all(call["deterministic"] is False for call in backend.calls)
        assert all(call["seed"] == 42 for call in backend.calls)
        assert all(call["thinking"] is True for call in backend.calls)
        assert all(call["thinking"] is True for call in backend.query_calls)
        # The markdown draft uses the full token cap.
        assert (
            backend.calls[0]["max_tokens"] == document_png.MARKDOWN_MAX_TOKENS == 8192
        )
        # The design brief (plan) reached the HTML prompt.
        assert "modern minimal" in backend.calls[1]["prompt"]
        assert "None. Do not include any figures." in backend.calls[1]["prompt"]

        # A4 aspect (default): the sanitized HTML carries the A4 rule.
        assert "@page { size: A4 portrait; margin: 2cm; }" in artifact.html
        assert artifact.gen_tracing["a4_aspect"] is True

        _check_trace(artifact, backend, company_id)

    def test_a4_aspect_false_uses_content_sized_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        artifact = _run(tmp_path, monkeypatch, backend, a4_aspect=False)

        # The sanitized HTML asks for a content-sized page…
        assert "@page { size: auto; margin: 2cm; }" in artifact.html
        assert artifact.gen_tracing["a4_aspect"] is False
        # …and the HTML system prompt says so too.
        assert "content-sized (auto)" in backend.calls[1]["system"]
        assert artifact.png_path.exists()

    def test_distress_stage_in_trace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clean_dir = tmp_path / "clean"
        distressed_dir = tmp_path / "distressed"
        backend = FakeBackend()
        monkeypatch.setattr(document_png, "get_chat_backend", lambda: backend)
        company_id = _save_company()

        clean = document_png.generate_document_image(
            company_id, "Onboarding Guide", output_dir=clean_dir
        )
        distressed = document_png.generate_document_image(
            company_id,
            "Onboarding Guide",
            output_dir=distressed_dir,
            distress=DistressOptions(enabled=True, seed=7),
        )

        # The distress pass changed the PNG (same render, different bytes).
        assert clean.png_path.read_bytes() != distressed.png_path.read_bytes()
        # The trace stores the options + seed so the PNG is reproducible
        # from the trace alone.
        stage = distressed.gen_tracing["stages"]["distress"]
        assert stage["enabled"] is True
        assert stage["seed"] == 7
        assert stage["options"] == DistressOptions(enabled=True, seed=7).model_dump(
            mode="json"
        )
        assert stage["elapsed_s"] >= 0

    def test_traced_distress_preserves_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        artifact = _run(
            tmp_path,
            monkeypatch,
            backend,
            distress=DistressOptions(enabled=True, seed=7),
            gen_tracing=True,
        )
        stage = artifact.gen_tracing["stages"]["distress"]
        original = Path(stage["original_path"])
        # The preserved original sits next to the document…
        assert original.parent == artifact.png_path.parent
        assert original.name == f"{artifact.png_path.stem}_original.png"
        assert original.is_file()
        # …is byte-identical to the untouched render (not the distressed
        # file), and the record carries the trace.
        assert original.read_bytes() != artifact.png_path.read_bytes()
        records = document_query.list_documents(company_id=artifact.company_id)
        assert records[0]["gen_tracing"]["stages"]["distress"]["original_path"] == (
            str(original)
        )

    def test_untraced_distress_saves_no_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        artifact = _run(
            tmp_path,
            monkeypatch,
            backend,
            distress=DistressOptions(enabled=True, seed=7),
        )
        stage = artifact.gen_tracing["stages"]["distress"]
        assert "original_path" not in stage
        assert not list(tmp_path.glob("*_original.png"))

    def test_traced_render_is_clean_but_stays_editable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Generated images are left undistressed by default; tracing
        # preserves the fresh clean render as the original, so the image
        # stays editable in the live distress editor.
        backend = FakeBackend()
        artifact = _run(tmp_path, monkeypatch, backend, gen_tracing=True)
        stage = artifact.gen_tracing["stages"]["distress"]
        assert stage["enabled"] is False
        original = Path(stage["original_path"])
        assert original.is_file()
        # No distress pass ran: the persisted document file is the
        # clean render (identical to the stored original).
        assert original.read_bytes() == artifact.png_path.read_bytes()

    def test_distress_seed_falls_back_to_company_seed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        artifact = _run(
            tmp_path,
            monkeypatch,
            backend,
            distress=DistressOptions(enabled=True),
        )
        # No explicit seed: the company seed (42) is recorded.
        assert artifact.gen_tracing["stages"]["distress"]["seed"] == 42

    def test_figure_extraction_heuristic_and_llm_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "document_gen.figures.render_figure_png", lambda spec: _TINY_PNG
        )
        backend = FakeBackend()
        # No fenced blocks: every requested kind goes to the LLM fallback.
        artifact = _run(
            tmp_path,
            monkeypatch,
            backend,
            figure_kinds=["bar", "line"],
            gen_tracing=True,
        )

        # Plan + one figure-extraction call (the missing kinds).
        assert [call["model"] for call in backend.query_calls] == [
            DocumentPlan,
            FigureExtraction,
        ]
        assert backend.query_calls[1]["prompt"].count(backend.MARKDOWN) >= 1
        assert "bar, line" in backend.query_calls[1]["prompt"]
        assert [f.kind for f in artifact.figures] == ["bar"]
        # The extracted figure is listed in the HTML prompt with its
        # placeholder token.
        assert "Figure 1: Trend (bar)" in backend.calls[1]["prompt"]
        assert "{{FIGURE_1}}" in backend.calls[1]["prompt"]
        # The figure PNG is embedded in the final HTML (base64 data URI).
        assert "data:image/png;base64," in artifact.html
        # The figures trace captures the LLM fallback.
        llm_trace = artifact.gen_tracing["stages"]["figures"]["llm"]
        assert llm_trace is not None
        assert len(llm_trace["output"]) == 1

    def test_plan_failure_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        original_query = backend.query

        def failing_query(prompt: str, model: Any, **kwargs: Any) -> Any:
            if model is DocumentPlan:
                raise RuntimeError("LLM down")
            return original_query(prompt, model, **kwargs)

        backend.query = failing_query  # type: ignore[method-assign]
        artifact = _run(tmp_path, monkeypatch, backend)

        # The default plan is used and flagged in the trace…
        assert artifact.gen_tracing["stages"]["plan"]["used_default_fallback"] is True
        default = document_pdf._DEFAULT_DOCUMENT_PLAN  # noqa: SLF001
        assert artifact.gen_tracing["stages"]["plan"]["output"] == (
            default.model_dump(mode="json")
        )
        # …and its design brief reached the HTML prompt (the failed plan
        # call is not recorded, so the HTML call is the only chat call
        # after the markdown).
        assert "Professional, neutral company document styling." in (
            backend.calls[1]["prompt"]
        )
        # The run still completes.
        assert artifact.png_path.exists()

    def test_html_failure_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        original_complete = backend.complete

        def failing_complete(prompt: str, system: str | None = None, **kwargs: Any):
            if system is not None:
                raise RuntimeError("LLM down")
            return original_complete(prompt, system, **kwargs)

        backend.complete = failing_complete  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="LLM down"):
            _run(tmp_path, monkeypatch, backend)

    def test_error_cases(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(document_png, "get_chat_backend", lambda: FakeBackend())
        with pytest.raises(ValueError, match="not found"):
            document_png.generate_document_image(999, "Onboarding Guide")
        company_id = _save_company()
        with pytest.raises(ValueError, match="output directory"):
            document_png.generate_document_image(company_id, "Onboarding Guide")

    def test_output_dir_override_and_collision_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        monkeypatch.setattr(document_png, "get_chat_backend", lambda: FakeBackend())
        # The explicit output_dir wins over the configured default.
        target = tmp_path / "custom"
        first = document_png.generate_document_image(
            company_id, "Onboarding Guide", output_dir=target
        )
        assert first.png_path.parent == target
        # A name collision gets a numeric suffix.
        second = document_png.generate_document_image(
            company_id, "Onboarding Guide", output_dir=target
        )
        assert first.png_path != second.png_path
        assert second.png_path.name.endswith("_1.png")

    def test_file_name_falls_back_to_report_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = FakeBackend()
        backend.MARKDOWN = "No heading here, just prose.\n"
        artifact = _run(tmp_path, monkeypatch, backend)
        assert artifact.png_path.name == "onboarding_guide.png"
