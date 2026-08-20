"""Tests for the LLM-driven PDF report generation pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from document_gen import document_query
from document_gen.models import (
    FIGURE_KINDS,
    CompanyProfile,
    DocumentPlan,
    DocumentType,
    SyntheticCompany,
)
from document_gen import document_pdf


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


class TestContentFiguresInstruction:
    """The <figures> instruction: fewer figures, prose around each one."""

    def test_no_kinds(self) -> None:
        assert (
            document_pdf._content_figures_instruction([])
            == "None. Do not include any figures."
        )

    def test_full_doc_counts_and_prose_rules(self) -> None:
        text = document_pdf._content_figures_instruction(["bar", "line"])
        assert "1-2 figures" in text
        assert "introduce each figure" in text
        assert "interpreting the chart" in text
        assert "back-to-back" in text

    def test_quick_doc_counts_and_prose_rules(self) -> None:
        text = document_pdf._content_figures_instruction(["bar"], quick=True)
        assert "at most 1 figure" in text
        assert "back-to-back" in text


class FakeBackend:
    """Canned chat backend: plan for stage 0, markdown for stage 1, HTML for stage 2.

    The markdown carries one fenced figure block so the deterministic
    heuristic extraction succeeds and no LLM figure call is needed;
    :meth:`query` serves the report plan (or the figure-extraction
    fallback) and records calls.
    """

    #: Plan returned for the stage-0 report-plan call.
    PLAN = DocumentPlan(
        include_toc=False,
        toc_reason="Short report; no TOC needed.",
        design_direction="Modern minimal identity with a navy header band.",
        palette=["#1F3A5F", "#7A9CC6", "#FFFFFF"],
        typography="sans-serif headings, serif body",
        layout_style="modern minimal",
    )

    MARKDOWN = (
        "# Title\n\n## Section\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```chart\n"
        "type: bar\n"
        "title: Sample figure\n"
        "data:\n"
        "A, 1\n"
        "B, 2\n"
        "```\n"
    )
    HTML = (
        "<!DOCTYPE html><html><head>"
        "<style>@page { size: Letter; margin: 1in; } "
        "body { width: 900px; font-size: 10pt; }</style>"
        "</head><body><h1>Title</h1><p>{{FIGURE_1}}</p></body></html>"
    )
    #: Specs returned by :meth:`query` (the LLM figure-extraction fallback).
    QUERY_FIGURES: list[dict[str, Any]] = [
        {
            "kind": "line",
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
        return self.MARKDOWN if system is None else self.HTML

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


def _stub_pdf(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace WeasyPrint rendering with a plain file write."""
    written: dict[str, Any] = {}

    def fake_html_to_pdf(html: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 fake")
        written["html"] = html
        return path

    monkeypatch.setattr(document_pdf, "html_to_pdf", fake_html_to_pdf)
    return written


def _check_trace(
    artifact: document_pdf.DocumentArtifact, backend: FakeBackend, company_id: int
) -> None:
    """Assert the aggregated per-stage trace is complete and DB-stored."""
    trace = artifact.gen_tracing
    assert trace is not None
    stages = trace["stages"]
    assert set(stages) == {"plan", "markdown", "figures", "html", "pdf"}
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

    # Figures stage: heuristic parsed the fenced block; no LLM fallback.
    assert stages["figures"]["requested_kinds"] == ["bar"]
    assert len(stages["figures"]["heuristic_specs"]) == 1
    assert stages["figures"]["llm"] is None
    assert stages["figures"]["specs"][0]["title"] == "Sample figure"

    # HTML stage: system prompt, prompt (embedding the markdown), raw and
    # sanitized LLM output (sanitized before base64 figure injection).
    assert stages["html"]["system_prompt"] is not None
    assert FakeBackend.MARKDOWN in stages["html"]["prompt"]
    assert stages["html"]["raw_output"] == FakeBackend.HTML
    assert "size: A4 portrait" in stages["html"]["sanitized_html"]
    assert "data:image/png;base64," not in stages["html"]["sanitized_html"]

    # PDF stage: the written file.
    assert Path(stages["pdf"]["path"]) == artifact.pdf_path
    assert stages["pdf"]["size_bytes"] > 0


def test_trace_persistence_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = _save_company()
    monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: FakeBackend())
    _stub_pdf(monkeypatch)

    # Default: the trace is built and returned on the artifact…
    artifact = document_pdf.generate_document_pdf(
        company_id, "Onboarding Guide", output_dir=tmp_path
    )
    assert artifact.gen_tracing is not None
    # …but the record carries no gen_tracing field.
    record = document_query.list_documents(company_id=company_id)[0]
    assert "gen_tracing" not in record

    # Flagged: the trace is stored on the record.
    artifact = document_pdf.generate_document_pdf(
        company_id, "Onboarding Guide", output_dir=tmp_path, gen_tracing=True
    )
    record = document_query.list_documents(company_id=company_id)[0]
    assert record["gen_tracing"] == artifact.gen_tracing


# ---------------------------------------------------------------------------
# Output directory resolution
# ---------------------------------------------------------------------------


class TestResolveOutputDir:
    def test_precedence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Unconfigured -> None; env var -> env default.
        assert document_pdf.resolve_output_dir() is None
        monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "env"))
        assert document_pdf.resolve_output_dir() == tmp_path / "env"
        # A saved setting wins over the env var…
        document_query.set_setting(
            document_pdf.DOCUMENTS_SETTINGS_KEY,
            {"output_dir": str(tmp_path / "saved")},
        )
        assert document_pdf.resolve_output_dir() == tmp_path / "saved"
        # …but a blank saved value falls back to the env default.
        document_query.set_setting(
            document_pdf.DOCUMENTS_SETTINGS_KEY, {"output_dir": "   "}
        )
        assert document_pdf.resolve_output_dir() == tmp_path / "env"


# ---------------------------------------------------------------------------
# Report type resolution
# ---------------------------------------------------------------------------


class TestResolveDocumentType:
    def test_name_and_index_resolution(self) -> None:
        company = document_query.get_company(_save_company())
        # Case-insensitive name match…
        report = document_pdf.resolve_document_type(company, "onboarding guide")
        assert report.name == "Onboarding Guide"
        # …with a zero-based index fallback.
        report = document_pdf.resolve_document_type(company, "1")
        assert report.name == "Quarterly Earnings"

    @pytest.mark.parametrize("report", ["Balance Sheet", "5"])
    def test_unknown_raises(self, report: str) -> None:
        company = document_query.get_company(_save_company())
        with pytest.raises(ValueError, match="not found"):
            document_pdf.resolve_document_type(company, report)

    def test_no_reports_raises(self) -> None:
        with pytest.raises(ValueError, match="no document types"):
            document_pdf.resolve_document_type({"reports": []}, "Onboarding Guide")


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------


class TestSanitizeReportHtml:
    def test_injects_page_rule_when_no_style(self) -> None:
        doc = "<!DOCTYPE html><html><head></head><body><h1>t</h1></body></html>"
        result = document_pdf.sanitize_document_html(doc)
        assert "@page { size: A4 portrait; margin: 2cm; }" in result

    def test_overrides_conflicting_page_size(self) -> None:
        doc = (
            "<html><head><style>@page { size: Letter; margin: 1in; } "
            "body { margin: 0; }</style></head><body></body></html>"
        )
        result = document_pdf.sanitize_document_html(doc)
        assert "size: A4 portrait" in result
        assert "size: Letter" not in result
        assert "margin: 1in" not in result
        assert "body { margin: 0; }" in result

    def test_preserves_nested_margin_boxes(self) -> None:
        doc = (
            "<html><head><style>"
            "@page { size: A4; @bottom-center { content: counter(page); } }"
            "</style></head><body></body></html>"
        )
        result = document_pdf.sanitize_document_html(doc)
        assert "@bottom-center" in result
        assert "content: counter(page)" in result
        assert "size: A4 portrait" in result

    def test_strips_code_fences_and_prose(self) -> None:
        doc = (
            "Here is the document:\n```html\n"
            "<!DOCTYPE html><html><head></head><body><h1>t</h1></body></html>\n"
            "```\nHope that helps!"
        )
        result = document_pdf.sanitize_document_html(doc)
        assert "```" not in result
        assert "Hope that helps" not in result
        assert result.startswith("<!DOCTYPE html>")
        assert result.endswith("</html>")

    def test_replaces_fixed_px_widths(self) -> None:
        doc = (
            "<html><head><style>body { width: 900px; } "
            "table { width: 800px; } h1 { width: 5em; }</style>"
            "</head><body></body></html>"
        )
        result = document_pdf.sanitize_document_html(doc)
        assert "900px" not in result
        assert "800px" not in result
        assert "width: 100%" in result
        assert "5em" in result  # non-px widths untouched

    def test_valid_document_passes_through(self) -> None:
        doc = (
            "<!DOCTYPE html><html><head>"
            "<style>@page { size: A4 portrait; margin: 2cm; }</style>"
            "</head><body><h1>t</h1></body></html>"
        )
        result = document_pdf.sanitize_document_html(doc)
        assert result.count("size: A4 portrait") == 1


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestGenerateReportPdf:
    def test_end_to_end(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        written = _stub_pdf(monkeypatch)

        artifact = document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            user_input="focus on Q3",
            output_dir=tmp_path,
            figure_kinds=["bar"],
            gen_tracing=True,
        )

        assert artifact.company_id == company_id
        assert artifact.report_name == "Onboarding Guide"
        assert artifact.markdown == FakeBackend.MARKDOWN
        assert artifact.pdf_path.exists()
        assert artifact.pdf_path.parent == tmp_path
        # File name comes from the markdown's document title ("# Title").
        assert artifact.pdf_path.name == "title.pdf"

        # Stage 1: no system prompt; stage 2: hardcoded system prompt.
        assert len(backend.calls) == 2
        assert backend.calls[0]["system"] is None
        assert "focus on Q3" in backend.calls[0]["prompt"]
        assert "Acme Corp" in backend.calls[0]["prompt"]
        # The markdown draft must carry a concise document title.
        assert "concise document name" in backend.calls[0]["prompt"]
        assert backend.calls[1]["system"] is not None
        assert FakeBackend.MARKDOWN in backend.calls[1]["prompt"]
        # Non-deterministic (server sampling defaults), seeded; thinking
        # on by default.
        assert all(call["deterministic"] is False for call in backend.calls)
        assert all(call["seed"] == 42 for call in backend.calls)
        assert all(call["thinking"] is True for call in backend.calls)
        assert all(call["thinking"] is True for call in backend.query_calls)
        # Stage 1 markdown and stage 2 HTML+CSS are both capped.
        assert (
            backend.calls[0]["max_tokens"] == document_pdf.MARKDOWN_MAX_TOKENS == 8192
        )
        assert backend.calls[1]["max_tokens"] == document_pdf.HTML_MAX_TOKENS == 12000

        # Stage 0: exactly one structured call (the report plan); the
        # heuristic parsed the fenced figure block so no figure-extraction
        # call is needed, and the HTML prompt only carries the lightweight
        # placeholder.
        assert len(backend.query_calls) == 1
        assert backend.query_calls[0]["model"] is DocumentPlan
        assert len(artifact.figures) == 1
        assert artifact.figures[0].title == "Sample figure"
        assert "{{FIGURE_1}}" in backend.calls[1]["prompt"]
        # The rendered figure is injected after the LLM step.
        assert "data:image/png;base64," in written["html"]
        assert "{{FIGURE" not in written["html"]

        # Sanitization applied to the rendered HTML.
        assert "size: A4 portrait" in written["html"]
        assert "900px" not in written["html"]

        # The per-stage trace is returned on the artifact and stored on
        # the record as ``gen_tracing``.
        _check_trace(artifact, backend, company_id)

    def test_quick_doc_cuts_token_caps_by_80_percent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        document_pdf.generate_document_pdf(
            company_id, "Onboarding Guide", output_dir=tmp_path, quick_doc=True
        )

        assert (
            backend.calls[0]["max_tokens"]
            == int(
                document_pdf.MARKDOWN_MAX_TOKENS * document_pdf.QUICK_DOC_TOKEN_FRACTION
            )
            == 1638
        )
        assert (
            backend.calls[1]["max_tokens"]
            == int(document_pdf.HTML_MAX_TOKENS * document_pdf.QUICK_DOC_TOKEN_FRACTION)
            == 2400
        )
        # Quick docs also disable model thinking on every LLM call.
        assert all(call["thinking"] is False for call in backend.calls)
        assert all(call["thinking"] is False for call in backend.query_calls)

    def test_quick_doc_uses_quick_prompt_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from document_gen.prompts import quick_document_html_system_prompt

        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=["bar"],
            quick_doc=True,
        )

        # Stage 0: the quick plan prompt (no TOC / minimal design), not
        # the full design-identity prompt.
        plan_prompt = backend.query_calls[0]["prompt"]
        assert "planning a **quick** company document" in plan_prompt
        assert "do NOT need a TOC" in plan_prompt
        assert "Quick doc (short, fast document): yes" not in plan_prompt

        # Stage 1: the quick content prompt — no TOC instruction, no
        # full-prompt formatting rules.
        markdown_prompt = backend.calls[0]["prompt"]
        assert "drafting a **quick** company document" in markdown_prompt
        assert "No table of contents" in markdown_prompt
        assert "concise document name" not in markdown_prompt

        # Stage 2: the quick HTML system prompt (minimal styling), not
        # the full document-designer system prompt.
        assert backend.calls[1]["system"] == quick_document_html_system_prompt
        html_prompt = backend.calls[1]["prompt"]
        assert "use only the first color from the design brief" in html_prompt

    def test_quick_doc_figure_fallback_uses_quick_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from document_gen.prompts import quick_document_figures_prompt

        company_id = _save_company()
        backend = FakeBackend()
        backend.MARKDOWN = "# Title\n\n## Section\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=["bar"],
            quick_doc=True,
        )

        # The LLM figure-extraction fallback used the quick figures
        # prompt (at most 1 figure), not the full one.
        figures_prompt = backend.query_calls[1]["prompt"]
        assert "at most 1 figure" in figures_prompt
        assert quick_document_figures_prompt.startswith("Document content (markdown)")

    @pytest.mark.parametrize(
        ("figure_kinds", "expected_plan_figures"),
        [(None, "none"), (["bar", "line"], "bar, line")],
    )
    def test_figures_instruction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        figure_kinds: list[str] | None,
        expected_plan_figures: str,
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=figure_kinds,
        )

        # The plan prompt always states which figures are requested.
        assert (
            f"Figures to include: {expected_plan_figures}"
            in backend.query_calls[0]["prompt"]
        )
        if figure_kinds is None:
            # No figure kinds requested -> no figures anywhere, and no
            # extraction attempted at all (heuristic or LLM).
            assert "Do not include any figures" in backend.calls[0]["prompt"]
            assert "Do not include any figures" in backend.calls[1]["prompt"]
            assert len(backend.query_calls) == 1
            assert backend.query_calls[0]["model"] is DocumentPlan
        else:
            # The markdown prompt asks for fenced figure blocks limited to
            # the requested kinds; the HTML prompt lists the extracted
            # figures with placeholder tokens.
            assert "```chart" in backend.calls[0]["prompt"]
            assert "only these kinds: bar, line" in backend.calls[0]["prompt"]
            assert "Figures to include" in backend.calls[1]["prompt"]
            assert "{{FIGURE_1}}" in backend.calls[1]["prompt"]
            # The heuristic covered only the bar block, so the LLM
            # fallback was asked for the missing kind.
            assert len(backend.query_calls) == 2
            assert backend.query_calls[0]["model"] is DocumentPlan
            assert "Allowed figure types\nline" in backend.query_calls[1]["prompt"]

    @pytest.mark.parametrize(
        ("markdown", "query_figures", "figure_kinds", "allowed_text", "expected_kinds"),
        [
            # No fenced blocks: the heuristic finds nothing, so every
            # requested kind goes to the LLM fallback.
            (
                "# Title\n\n## Section\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
                FakeBackend.QUERY_FIGURES,
                list(FIGURE_KINDS),
                "bar, line, area, pie, scatter, histogram",
                ["line"],
            ),
            # Heuristic parsed the bar block; only the uncovered kind
            # (pie) is sent to the LLM fallback, and its result is merged
            # after the heuristic specs.
            (
                FakeBackend.MARKDOWN,
                [
                    {
                        "kind": "pie",
                        "title": "Mix",
                        "labels": ["A", "B"],
                        "series": [{"name": "V", "values": [3.0, 7.0]}],
                    }
                ],
                ["bar", "pie"],
                "pie",
                ["bar", "pie"],
            ),
        ],
    )
    def test_llm_fallback_for_uncovered_kinds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        markdown: str,
        query_figures: list[dict[str, Any]],
        figure_kinds: list[str],
        allowed_text: str,
        expected_kinds: list[str],
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        backend.MARKDOWN = markdown
        backend.QUERY_FIGURES = query_figures
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        written = _stub_pdf(monkeypatch)

        artifact = document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=figure_kinds,
        )

        # Exactly one structured LLM figure-extraction call, after the plan.
        assert len(backend.query_calls) == 2
        assert backend.query_calls[0]["model"] is DocumentPlan
        assert markdown in backend.query_calls[1]["prompt"]
        assert allowed_text in backend.query_calls[1]["prompt"]
        assert backend.query_calls[1]["deterministic"] is False
        assert backend.query_calls[1]["seed"] == 42
        assert [f.kind for f in artifact.figures] == expected_kinds
        assert "{{FIGURE_1}}" in backend.calls[1]["prompt"]
        # The fallback figure is embedded into the final HTML.
        assert "data:image/png;base64," in written["html"]

        # The figures trace captures the LLM fallback prompt and output.
        llm_trace = artifact.gen_tracing["stages"]["figures"]["llm"]
        assert llm_trace is not None
        assert markdown in llm_trace["prompt"]
        assert len(llm_trace["output"]) == 1

    def test_no_figures_when_llm_fallback_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        backend.MARKDOWN = "# Title\n\nNo tables here.\n"
        backend.QUERY_FIGURES = []
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        written = _stub_pdf(monkeypatch)

        artifact = document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=list(FIGURE_KINDS),
        )

        assert len(backend.query_calls) == 2
        assert backend.query_calls[0]["model"] is DocumentPlan
        assert artifact.figures == []
        assert "Do not include any figures" in backend.calls[1]["prompt"]
        assert "data:image/png" not in written["html"]

    def test_figures_instruction_with_kinds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        document_pdf.generate_document_pdf(
            company_id,
            "Onboarding Guide",
            output_dir=tmp_path,
            figure_kinds=["bar", "line"],
        )

        # The markdown prompt asks for fenced figure blocks limited to the
        # requested kinds; the HTML prompt lists the extracted figures
        # with placeholder tokens.
        assert "```chart" in backend.calls[0]["prompt"]
        assert "only these kinds: bar, line" in backend.calls[0]["prompt"]
        assert "Figures to include" in backend.calls[1]["prompt"]
        assert "{{FIGURE_1}}" in backend.calls[1]["prompt"]
        # The heuristic covered only the bar block, so the LLM fallback
        # was asked for the missing kind and its line figure was merged.
        assert len(backend.query_calls) == 2
        assert backend.query_calls[0]["model"] is DocumentPlan
        assert "Allowed figure types\nline" in backend.query_calls[1]["prompt"]

    def test_error_cases(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: FakeBackend())
        _stub_pdf(monkeypatch)
        with pytest.raises(ValueError, match="not found"):
            document_pdf.generate_document_pdf(999, "Onboarding Guide")
        company_id = _save_company()
        with pytest.raises(ValueError, match="output directory"):
            document_pdf.generate_document_pdf(company_id, "Onboarding Guide")

    def test_output_dir_override_and_collision_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: FakeBackend())
        _stub_pdf(monkeypatch)
        # The explicit output_dir wins over the configured default.
        target = tmp_path / "custom"
        first = document_pdf.generate_document_pdf(
            company_id, "Onboarding Guide", output_dir=target
        )
        assert first.pdf_path.parent == target
        # A name collision gets a numeric suffix.
        second = document_pdf.generate_document_pdf(
            company_id, "Onboarding Guide", output_dir=target
        )
        assert first.pdf_path != second.pdf_path
        assert second.pdf_path.name.endswith("_1.pdf")


# ---------------------------------------------------------------------------
# Document title / file name
# ---------------------------------------------------------------------------


class TestDocumentTitle:
    @pytest.mark.parametrize(
        ("markdown", "expected"),
        [
            (
                "# Q3 Guest Departure Summary\n\n## Section\n",
                "Q3 Guest Departure Summary",
            ),
            ("## Section only\n", None),  # subheadings do not count
            ("```\n# Not a title\n```\n\n# Real Title\n", "Real Title"),
            ("just text\n", None),
        ],
    )
    def test_document_title(self, markdown: str, expected: str | None) -> None:
        assert document_pdf._document_title(markdown) == expected

    def test_file_name_falls_back_to_report_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = _save_company()
        backend = FakeBackend()
        backend.MARKDOWN = "No heading here, just prose.\n"
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)

        artifact = document_pdf.generate_document_pdf(
            company_id, "Onboarding Guide", output_dir=tmp_path
        )
        assert artifact.pdf_path.name == "onboarding_guide.pdf"


# ---------------------------------------------------------------------------
# Stage 0: report plan (TOC decision + design brief)
# ---------------------------------------------------------------------------


class TestDocumentPlanStage:
    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        backend: FakeBackend,
        **kwargs: Any,
    ) -> FakeBackend:
        company_id = _save_company()
        monkeypatch.setattr(document_pdf, "get_chat_backend", lambda: backend)
        _stub_pdf(monkeypatch)
        document_pdf.generate_document_pdf(
            company_id, "Onboarding Guide", output_dir=tmp_path, **kwargs
        )
        return backend

    @pytest.mark.parametrize("quick_doc", [False, True])
    def test_plan_prompt_carries_generation_flags(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        quick_doc: bool,
    ) -> None:
        backend = self._run(
            tmp_path,
            monkeypatch,
            FakeBackend(),
            quick_doc=quick_doc,
            figure_kinds=["bar"],
        )
        plan_prompt = backend.query_calls[0]["prompt"]
        assert "Figures to include: bar" in plan_prompt
        if quick_doc:
            # The quick plan prompt (no TOC / minimal design) has no
            # <quick_doc> slot.
            assert "planning a **quick** company document" in plan_prompt
            assert "do NOT need a TOC" in plan_prompt
            assert "Quick doc (short, fast document)" not in plan_prompt
        else:
            assert "Quick doc (short, fast document): no" in plan_prompt
            assert "name: Onboarding Guide" in plan_prompt
            assert "Acme Corp" in plan_prompt
        assert backend.query_calls[0]["deterministic"] is False
        assert backend.query_calls[0]["seed"] == 42

    @pytest.mark.parametrize(
        ("include_toc", "expected"),
        [
            (False, "Do **not** include a table of contents"),
            (True, "Start with a **table of contents**"),
        ],
    )
    def test_plan_toc_is_passed_to_markdown_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        include_toc: bool,
        expected: str,
    ) -> None:
        backend = FakeBackend()  # canned plan has include_toc=False
        if include_toc:
            backend.PLAN = DocumentPlan(
                include_toc=True,
                toc_reason="Long multi-section guide.",
                design_direction="Classic regulatory look.",
                palette=["#111111", "#444444", "#EEEEEE"],
                typography="serif headings, serif body",
                layout_style="corporate",
            )
        self._run(tmp_path, monkeypatch, backend)
        assert expected in backend.calls[0]["prompt"]

    def test_design_brief_is_passed_to_html_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = self._run(tmp_path, monkeypatch, FakeBackend())
        html_prompt = backend.calls[1]["prompt"]
        assert "Design brief" in html_prompt
        assert "Modern minimal identity with a navy header band." in html_prompt
        assert "#1F3A5F, #7A9CC6, #FFFFFF" in html_prompt
        assert "sans-serif headings, serif body" in html_prompt
        assert "Layout style: modern minimal" in html_prompt

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
        self._run(tmp_path, monkeypatch, backend)

        # Default plan: TOC included, neutral design brief in the HTML prompt.
        assert "Start with a **table of contents**" in backend.calls[0]["prompt"]
        assert "professional, neutral" in backend.calls[1]["prompt"].lower()
        # The figure-extraction path (here: none needed) still works.
        assert len(backend.calls) == 2
