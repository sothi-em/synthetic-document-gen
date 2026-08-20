"""LLM-driven Excel (xlsx) workbook generation pipeline.

Combines a stored company profile, one of its document types, and an
optional free-text user input to produce an Excel workbook:

1. A small structured LLM call produces a workbook-level **plan**
   (sheet names, design direction, palette, table density, styling
   notes). On failure the pipeline falls back to a neutral default
   plan and continues.
2. The chat LLM drafts the workbook content as **markdown** focused on
   data tables (each markdown table becomes one Excel table).
3. Figures are extracted from the markdown: a deterministic heuristic
   parses the fenced figure blocks first; when the parsed figures do
   not cover every requested kind, a small structured LLM call
   extracts the missing figure specs (identical logic to the PDF
   pipeline). Extraction is skipped entirely in simple-sheets mode.
4. The chat LLM converts the markdown into an **ExcelDoc** JSON: table
   structure, positions, and styling. Domain-specific data values
   (line items, amounts, ratios, terms, notes) are transcribed
   verbatim from the markdown into the cells; only generic
   personal/contact columns (names, addresses, phone numbers, etc.)
   are left empty with a ``faker_field`` spec.
5. :func:`document_gen.generators.excel_gen.fill_table_values`
   deterministically fills the remaining empty cells (the generic
   personal/contact columns) from a random seed (Faker).
6. :func:`document_gen.generators.excel_gen.render_excel_doc` renders
   the workbook with openpyxl (deterministic); matplotlib figures are
   anchored in cells per the ``FigurePlacement`` specs.

When the **simple sheets** option is on, the figure kinds are forced to
empty, the prompts carry the simple-mode rules (no cover sheet, at most
4 sheets, 1-2 simple tables per sheet, no figures), and a post-check
warns (log only) when the LLM still returns 5+ sheets or figure
placements. The **glossary** option (default off) adds a single
Glossary lookup sheet defining the abbreviated terms used in the
workbook; the prompts then direct the LLM to use abbreviated terms
sparingly (readable 4+ character terms, not every sheet).

The pipeline aggregates a per-stage **trace** (prompts, outputs,
timings) stored on the document record under the ``gen_tracing`` field
(see :func:`document_gen.document_query.save_document`) and returned on
the :class:`ExcelArtifact`. The trace is self-sufficient for
re-rendering: the ``excel`` stage persists the full spec-only
``ExcelDoc`` JSON, the ``values`` stage persists the Faker seed, and
the ``figures`` stage persists the figure specs, so the .xlsx can be
regenerated deterministically from a stored trace alone via
``fill_table_values(doc, seed)`` + ``render_excel_doc(doc, path,
figures)`` — no LLM calls, content untouched.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from document_gen import document_pdf, document_query, figures
from document_gen.generators.excel_gen import fill_table_values, render_excel_doc
from document_gen.llm import get_chat_backend
from document_gen.models import (
    EXCEL_FAKER_FIELDS,
    DocumentType,
    ExcelDoc,
    ExcelPlan,
    FigureExtraction,
    FigureSpec,
    SyntheticCompany,
)
from document_gen.prompts import (
    document_figures_prompt,
    excel_content_prompt,
    excel_plan_prompt,
    excel_styling_prompt,
    quick_document_figures_prompt,
)

logger = logging.getLogger(__name__)

#: Output-token cap for the markdown draft (keeps the draft, and the
#: styling prompt that embeds it, from growing unbounded).
MARKDOWN_MAX_TOKENS = 8192

#: Fraction of the token cap kept when the "Quick Doc" option is on
#: (the cap is cut down by 80%).
QUICK_DOC_TOKEN_FRACTION = 0.2


@dataclass
class ExcelArtifact:
    """Result of one Excel workbook generation run."""

    company_id: int
    report_name: str
    markdown: str
    excel_doc: ExcelDoc
    xlsx_path: Path
    figures: list[FigureSpec] = field(default_factory=list)
    gen_tracing: dict[str, Any] | None = None


#: Fallback plan used when the workbook-plan LLM call fails: a neutral
#: design brief with the default-mode sheet layout.
_DEFAULT_EXCEL_PLAN = ExcelPlan(
    design_direction="Professional, neutral workbook styling.",
    palette=["#1F3A5F", "#333333", "#FFFFFF"],
    sheet_names=["Cover", "Data"],
    table_density="standard",
    notes="Neutral styling; standard table density.",
)


# ---------------------------------------------------------------------------
# Prompt slot helpers
# ---------------------------------------------------------------------------


def _design_brief_text(plan: ExcelPlan) -> str:
    """Render an :class:`ExcelPlan` as the ``<design_brief>`` prompt slot.

    Args:
        plan: The workbook plan.

    Returns:
        The instruction text for the ``<design_brief>`` prompt slot.
    """
    return (
        f"Design direction: {plan.design_direction}\n"
        f"Color palette: {', '.join(plan.palette)}\n"
        f"Table density: {plan.table_density}\n"
        f"Planned sheet names: {', '.join(plan.sheet_names)}\n"
        f"Notes: {plan.notes or 'None.'}"
    )


def _mode_text(simple_sheets: bool, glossary: bool) -> str:
    """Build the ``<mode>`` instruction for the Excel prompts.

    Args:
        simple_sheets: Whether simple-sheets mode is on.
        glossary: Whether the glossary lookup sheet is requested.

    Returns:
        The instruction text for the ``<mode>`` prompt slot.
    """
    if simple_sheets:
        base = (
            "Simple sheets mode: data sheets only — no cover sheet; at "
            "most 4 sheets; 1-2 simple tables per sheet with single-row "
            "column headers; no figures and no loose annotation blocks."
        )
    else:
        base = (
            "Default mode: start with a Cover sheet, then the data "
            "sheets; multi-row headers, header fills, borders, number "
            "formats, loose annotation blocks, and figure placements are "
            "allowed."
        )
    if glossary:
        base += (
            " Glossary sheet: include a `## Glossary` section — a "
            "two-column markdown table (`Term` | `Definition`) defining "
            "the abbreviated terms used in the workbook (e.g. "
            "`TOTAL_SV` — total sale value for the current year). Use "
            "abbreviated terms sparingly: only for long or frequently "
            "repeated headers and line items (a handful at most, not "
            "every sheet), and every term must be at least 4 characters "
            "(no 1-3 letter codes); all other headers stay plain and "
            "self-explanatory."
        )
    else:
        base += (
            " No glossary sheet: keep every column header and line item "
            "plain and self-explanatory (no abbreviated terms)."
        )
    return base


def _styling_figures_instruction(specs: list[FigureSpec]) -> str:
    """Build the ``<figures>`` instruction for the Excel styling prompt.

    Lists the extracted figures with their 1-based placement indices.

    Args:
        specs: The extracted figure specs (indices follow this order).

    Returns:
        The instruction text for the ``<figures>`` prompt slot.
    """
    if not specs:
        return "None. Do not place any figures."
    return "\n".join(
        f"Figure {index}: {spec.title or 'Untitled'} ({spec.kind}) — index {index}"
        for index, spec in enumerate(specs, start=1)
    )


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _plan_workbook(
    backend: Any,
    profile: SyntheticCompany,
    report_type: DocumentType,
    kinds: list[str],
    simple_sheets: bool,
    glossary: bool,
    user_input: str | None,
    seed: int,
    model_name: str | None,
    thinking: bool,
) -> tuple[ExcelPlan, str, float, bool]:
    """Ask the LLM to plan the workbook (sheet names + design brief).

    Falls back to :data:`_DEFAULT_EXCEL_PLAN` (logged) when the call
    fails, so a failed plan step never aborts the generation.

    Args:
        backend: The chat LLM backend.
        profile: The company profile.
        report_type: The report type being generated.
        kinds: The requested figure kinds (may be empty).
        simple_sheets: Whether simple-sheets mode is on.
        glossary: Whether the glossary lookup sheet is requested.
        user_input: Optional free-text user guidance.
        seed: Random seed for deterministic runs.
        model_name: Optional model ID override.
        thinking: Whether model thinking/reasoning is enabled.

    Returns:
        A tuple ``(plan, prompt, elapsed_s, used_default)`` where
        *prompt* is the rendered prompt (even on fallback), *elapsed_s*
        the call wall time, and *used_default* whether the fallback
        plan was used.
    """
    report_type_text = (
        f"name: {report_type.name}\n"
        f"category: {report_type.category}\n"
        f"purpose: {report_type.purpose}"
    )
    prompt = (
        excel_plan_prompt.replace("<company_profile>", profile.format_prompt())
        .replace("<document_type>", report_type_text)
        .replace("<simple_sheets>", "yes" if simple_sheets else "no")
        .replace("<glossary>", "yes" if glossary else "no")
        .replace("<figures>", ", ".join(kinds) if kinds else "none")
        .replace(
            "<user_input>",
            user_input.strip() if user_input and user_input.strip() else "None.",
        )
    )
    t_step = time.perf_counter()
    logger.info("Excel document: LLM call (workbook plan) started")
    try:
        plan = backend.query(
            prompt,
            model=ExcelPlan,
            deterministic=False,
            seed=seed,
            model_name=model_name,
            thinking=thinking,
        )
    except Exception:
        logger.exception(
            "Excel document: workbook plan call failed; using default plan"
        )
        return _DEFAULT_EXCEL_PLAN, prompt, time.perf_counter() - t_step, True
    elapsed = time.perf_counter() - t_step
    logger.info(
        "Excel document: LLM call (workbook plan) done in %.3fs (sheets=%s, "
        "density=%r, palette=%s)",
        elapsed,
        ", ".join(plan.sheet_names),
        plan.table_density,
        ", ".join(plan.palette),
    )
    return plan, prompt, elapsed, False


def _extract_figure_specs(
    backend: Any,
    markdown: str,
    kinds: list[str],
    quick_doc: bool,
    seed: int,
    model_name: str | None,
    thinking: bool,
) -> tuple[list[FigureSpec], dict[str, Any]]:
    """Extract figure specs from the markdown (heuristic first, LLM fallback).

    Identical logic to the PDF pipeline: the deterministic heuristic
    parses fenced figure blocks; when the parsed figures do not cover
    every requested kind, a structured LLM call extracts the missing
    specs.

    Args:
        backend: The chat LLM backend.
        markdown: The markdown draft.
        kinds: The requested figure kinds (may be empty).
        quick_doc: Whether the quick-doc option is on (quick figures
            prompt instead of the full one).
        seed: Random seed for deterministic runs.
        model_name: Optional model ID override.
        thinking: Whether model thinking/reasoning is enabled.

    Returns:
        A tuple ``(specs, trace)`` where *trace* is the ``figures``
        stage trace dict.
    """
    figure_specs: list[FigureSpec] = []
    trace: dict[str, Any] = {
        "requested_kinds": kinds,
        "heuristic_specs": [],
        "llm": None,
    }
    if kinds:
        t_step = time.perf_counter()
        heuristic_specs = figures.extract_figure_specs(markdown)
        if heuristic_specs:
            logger.info(
                "Excel document: heuristic extracted %d figure spec(s) in %.3fs",
                len(heuristic_specs),
                time.perf_counter() - t_step,
            )
        figure_specs.extend(heuristic_specs)
        trace["heuristic_specs"] = [s.model_dump(mode="json") for s in heuristic_specs]
        missing_kinds = [k for k in kinds if k not in {s.kind for s in figure_specs}]
        if missing_kinds:
            logger.info(
                "Excel document: requested kinds not covered by heuristic "
                "(missing: %s); LLM figure extraction started",
                ", ".join(missing_kinds),
            )
            t_llm = time.perf_counter()
            figures_template = (
                quick_document_figures_prompt if quick_doc else document_figures_prompt
            )
            figures_prompt = figures_template.replace("<markdown>", markdown).replace(
                "<figure_types>", ", ".join(missing_kinds)
            )
            extraction = backend.query(
                figures_prompt,
                model=FigureExtraction,
                deterministic=False,
                seed=seed,
                model_name=model_name,
                thinking=thinking,
            )
            llm_specs = [s for s in extraction.figures if s.is_drawable]
            figure_specs.extend(llm_specs)
            llm_elapsed = time.perf_counter() - t_llm
            logger.info(
                "Excel document: LLM extraction returned %d drawable figure "
                "spec(s) in %.3fs",
                len(llm_specs),
                llm_elapsed,
            )
            trace["llm"] = {
                "prompt": figures_prompt,
                "output": [s.model_dump(mode="json") for s in llm_specs],
                "elapsed_s": round(llm_elapsed, 3),
            }
    trace["specs"] = [s.model_dump(mode="json") for s in figure_specs]
    return figure_specs, trace


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def generate_document_excel(
    company_id: int,
    report: str,
    user_input: str | None = None,
    model_name: str | None = None,
    output_dir: Path | None = None,
    figure_kinds: list[str] | None = None,
    quick_doc: bool = False,
    simple_sheets: bool = False,
    glossary: bool = False,
    gen_tracing: bool = False,
) -> ExcelArtifact:
    """Generate an Excel workbook for a stored company.

    Pipeline: LLM workbook plan (sheet names + design brief) -> LLM
    markdown draft (data-table focused) -> figure extraction
    (heuristic first, LLM fallback; skipped in simple-sheets mode) ->
    LLM ExcelDoc JSON (domain values transcribed from the markdown;
    generic personal/contact columns left for Faker) -> deterministic
    Faker value fill of the remaining empty cells -> openpyxl render
    (matplotlib figures anchored per placement).

    Args:
        company_id: TinyDB ``doc_id`` of the company.
        report: Document type name (case-insensitive) or 0-based index
            string into the company's document list.
        user_input: Optional free-text guidance for the workbook content.
        model_name: Optional model ID override for the LLM queries.
        output_dir: Output directory override. When ``None``,
            :func:`document_pdf.resolve_output_dir` is used.
        figure_kinds: The allowed matplotlib figure kinds (e.g.
            ``["bar", "line"]``). When ``None`` or empty, no figures
            are included. Forced to empty when *simple_sheets* is on.
        quick_doc: When ``True``, cut the markdown output-token cap by
            80% (keep :data:`QUICK_DOC_TOKEN_FRACTION`) and disable
            model thinking/reasoning on every LLM call in the run.
        simple_sheets: When ``True``, skip the cover sheet, force the
            figure kinds to empty, and instruct the prompts to keep the
            workbook to at most 4 sheets with 1-2 simple tables each. A
            post-check warns (log only) when the LLM still returns 5+
            sheets or figure placements.
        glossary: When ``True``, add a single **Glossary** lookup sheet
            defining the abbreviated terms used in the workbook and
            instruct the prompts to use abbreviated terms sparingly
            (readable 4+ character terms, not every sheet). Default off.
        gen_tracing: When ``True``, the aggregated per-stage trace
            (prompts, outputs, timings) is persisted on the
            document record under the ``gen_tracing`` field.
            The trace is always built and returned on the artifact;
            this flag only controls database persistence.

    Returns:
        The generated :class:`ExcelArtifact` (markdown, value-filled
        ExcelDoc, xlsx path, figure specs, and the aggregated per-stage
        ``gen_tracing`` trace; the trace is stored on the document
        record only when *gen_tracing* is ``True``).

    Raises:
        ValueError: When the company or document type is missing, or when
            no effective output directory is configured.
    """
    t_total = time.perf_counter()
    logger.info(
        "Excel document: starting for company %s (report=%r)", company_id, report
    )
    t_step = time.perf_counter()

    doc = document_query.get_company(company_id)
    if doc is None:
        raise ValueError(f"Company {company_id} not found")
    profile_data = doc.get("profile")
    if profile_data is None:
        raise ValueError(f"Company {company_id} has no profile")
    profile = SyntheticCompany.model_validate(profile_data)
    report_type = document_pdf.resolve_document_type(doc, report)

    out_dir = output_dir or document_pdf.resolve_output_dir()
    if out_dir is None:
        raise ValueError(
            "No document output directory set: configure it in the web UI "
            f"Settings tab or set the {document_pdf.DOCUMENTS_DIR_ENV} "
            "environment variable."
        )

    seed = doc.get("seed", 0)
    backend = get_chat_backend()
    markdown_max_tokens = int(
        MARKDOWN_MAX_TOKENS * (QUICK_DOC_TOKEN_FRACTION if quick_doc else 1.0)
    )
    # Quick docs trade quality for speed: besides the reduced token cap,
    # model thinking/reasoning is disabled on every LLM call in the run.
    thinking = not quick_doc
    logger.info(
        "Excel document: resolved company %s report %r (setup %.3fs)",
        company_id,
        report_type.name,
        time.perf_counter() - t_step,
    )

    # Simple-sheets mode never embeds figures.
    kinds: list[str] = [] if simple_sheets else list(figure_kinds or [])

    # Per-stage trace (prompts, outputs, timings) aggregated as the
    # pipeline runs; stored on the document record as
    # ``gen_tracing`` and returned on the artifact.
    trace: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "company_id": company_id,
        "report": report_type.name,
        "user_input": user_input,
        "quick_doc": quick_doc,
        "simple_sheets": simple_sheets,
        "glossary": glossary,
        "stages": {},
    }

    # Stage 0: quick LLM plan — sheet names + design brief for the
    # styling stage (falls back to defaults on failure).
    plan, plan_prompt, plan_elapsed, plan_used_default = _plan_workbook(
        backend,
        profile,
        report_type,
        kinds,
        simple_sheets,
        glossary,
        user_input,
        seed,
        model_name,
        thinking,
    )
    trace["stages"]["plan"] = {
        "prompt": plan_prompt,
        "output": plan.model_dump(mode="json"),
        "elapsed_s": round(plan_elapsed, 3),
        "used_default_fallback": plan_used_default,
    }

    # Stage 1: markdown draft focused on data tables.
    report_type_text = (
        f"name: {report_type.name}\n"
        f"category: {report_type.category}\n"
        f"purpose: {report_type.purpose}"
    )
    content_prompt = (
        excel_content_prompt.replace("<company_profile>", profile.format_prompt())
        .replace("<document_type>", report_type_text)
        .replace(
            "<user_input>",
            user_input.strip() if user_input and user_input.strip() else "None.",
        )
        .replace("<mode>", _mode_text(simple_sheets, glossary))
        .replace(
            "<figures>",
            document_pdf._content_figures_instruction(kinds, quick=quick_doc),
        )
    )
    t_step = time.perf_counter()
    logger.info("Excel document: LLM call (markdown draft) started")
    markdown = backend.complete(
        content_prompt,
        deterministic=False,
        seed=seed,
        model_name=model_name,
        max_tokens=markdown_max_tokens,
        thinking=thinking,
    )
    markdown_elapsed = time.perf_counter() - t_step
    logger.info(
        "Excel document: LLM call (markdown draft) done in %.3fs (%d chars)",
        markdown_elapsed,
        len(markdown),
    )
    trace["stages"]["markdown"] = {
        "prompt": content_prompt,
        "output": markdown,
        "elapsed_s": round(markdown_elapsed, 3),
    }

    # Stage 2: figure extraction (heuristic first, LLM fallback);
    # skipped entirely in simple-sheets mode (kinds forced to []).
    figure_specs, figures_trace = _extract_figure_specs(
        backend, markdown, kinds, quick_doc, seed, model_name, thinking
    )
    trace["stages"]["figures"] = figures_trace

    # Stage 3: styling — the markdown becomes a spec-only ExcelDoc JSON
    # (structure + styling + column specs, no data-row values).
    styling_prompt = (
        excel_styling_prompt.replace("<company_profile>", profile.format_prompt())
        .replace("<document_type>", report_type_text)
        .replace("<design_brief>", _design_brief_text(plan))
        .replace("<markdown>", markdown)
        .replace("<figures>", _styling_figures_instruction(figure_specs))
        .replace("<mode>", _mode_text(simple_sheets, glossary))
        .replace("<faker_fields>", ", ".join(sorted(EXCEL_FAKER_FIELDS)))
    )
    t_step = time.perf_counter()
    logger.info("Excel document: LLM call (ExcelDoc styling) started")
    excel_doc = backend.query(
        styling_prompt,
        model=ExcelDoc,
        deterministic=False,
        seed=seed,
        model_name=model_name,
        thinking=thinking,
    )
    styling_elapsed = time.perf_counter() - t_step
    logger.info(
        "Excel document: LLM call (ExcelDoc styling) done in %.3fs (%d sheets)",
        styling_elapsed,
        len(excel_doc.sheets),
    )
    # The trace persists the spec-only doc: table structure, positions,
    # styling, column specs, and figure placements — everything needed
    # to re-render the workbook later without LLM calls.
    trace["stages"]["excel"] = {
        "prompt": styling_prompt,
        "excel_doc": excel_doc.model_dump(mode="json"),
        "elapsed_s": round(styling_elapsed, 3),
    }

    # Simple-sheets post-check: the LLM is instructed to keep the
    # workbook small and figure-free; warn (log only) when it does not.
    if simple_sheets:
        if len(excel_doc.sheets) >= 5:
            logger.warning(
                "Excel document: simple-sheets mode requested but the LLM "
                "returned %d sheets (expected at most 4)",
                len(excel_doc.sheets),
            )
        if any(sheet.figures for sheet in excel_doc.sheets):
            logger.warning(
                "Excel document: simple-sheets mode requested but the LLM "
                "returned figure placements"
            )

    # Stage 4: deterministic Faker value fill (seed chosen here so the
    # trace alone can reproduce the exact values later).
    faker_seed = random.randint(0, 2**31 - 1)
    excel_doc.doc_schema.seed = faker_seed
    t_step = time.perf_counter()
    fill_table_values(excel_doc, faker_seed)
    values_elapsed = time.perf_counter() - t_step
    filled_values = sum(
        len(column.cells)
        for sheet in excel_doc.sheets
        for table in sheet.tables
        for column in table.columns
    )
    logger.info(
        "Excel document: Faker fill done in %.3fs (%d values, seed=%d)",
        values_elapsed,
        filled_values,
        faker_seed,
    )
    trace["stages"]["values"] = {
        "seed": faker_seed,
        "filled_values": filled_values,
        "elapsed_s": round(values_elapsed, 3),
    }

    # Stage 5: openpyxl render. File name comes from the concise
    # document title the LLM wrote as the markdown's first heading;
    # fall back to the report type name when no heading is found.
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = document_pdf._slug(
        document_pdf._document_title(markdown) or report_type.name
    )
    path = document_pdf._unique_path(out_dir, stem, "xlsx")
    t_step = time.perf_counter()
    logger.info("Excel document: openpyxl render started -> %s", path)
    render_excel_doc(excel_doc, path, figure_specs)
    xlsx_elapsed = time.perf_counter() - t_step
    logger.info(
        "Excel document: openpyxl render done in %.3fs (%d bytes)",
        xlsx_elapsed,
        path.stat().st_size,
    )
    trace["stages"]["xlsx"] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "elapsed_s": round(xlsx_elapsed, 3),
    }
    trace["finished_at"] = datetime.now(UTC).isoformat()
    trace["total_elapsed_s"] = round(time.perf_counter() - t_total, 3)
    document_pdf._record_document(
        company_id, report_type.name, path, gen_tracing=trace if gen_tracing else None
    )
    logger.info(
        "Excel document: finished for company %s in %.3fs total -> %s",
        company_id,
        time.perf_counter() - t_total,
        path,
    )
    return ExcelArtifact(
        company_id=company_id,
        report_name=report_type.name,
        markdown=markdown,
        excel_doc=excel_doc,
        xlsx_path=path,
        figures=figure_specs,
        gen_tracing=trace,
    )
