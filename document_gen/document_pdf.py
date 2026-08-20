"""LLM-driven PDF document generation pipeline.

Combines a stored company profile, one of its document types, and an
optional free-text user input to produce a PDF:

1. A small structured LLM call produces a **document plan**: whether the
   document needs a table of contents (based on the quick-doc flag,
   document type, and requested figures) and a design brief (palette,
   typography, layout style) that gives each document a distinctive
   visual identity. On failure the pipeline falls back to defaults
   (TOC included, neutral design) and continues.
2. The chat LLM drafts the document content as **markdown** (table of
   contents when the plan says so, sections/subsections, sample data
   tables, and fenced figure blocks).
3. Figures are extracted from the markdown: a deterministic heuristic
   parses the fenced figure blocks first; when none are found, or when
   the parsed figures do not cover every requested kind (and figures
   were requested), a small structured LLM call extracts the missing
   figure specs from the sample data tables. Row/column tables are ignored —
   they render correctly through the HTML+CSS stage. Each spec is
   rendered to a PNG with matplotlib
   (:mod:`document_gen.figures`).
4. The chat LLM converts the markdown into a standalone **HTML+CSS**
   document, styled per the plan's design brief (palette, typography,
   layout style), guided by a hardcoded system prompt. It places
   lightweight ``{{FIGURE_n}}`` placeholder tokens where the figures
   belong; the base64 images are injected *after* this step so they
   never bloat the LLM prompt.
5. :func:`document_gen.generators.pdf_gen.html_to_pdf` renders the HTML
   to PDF with WeasyPrint.

When the **quick-doc** option is on, every stage uses the smaller,
content-focused quick prompt set (``quick_document_*`` in
:mod:`document_gen.prompts`): no TOC, at most one data table, at most
one figure, and a minimal single-accent HTML style.

The pipeline also aggregates a per-stage **trace** (prompts, outputs,
timings) which is stored on the document record under the
``gen_tracing`` field (see :func:`document_gen.document_query.save_document`),
and returned on the :class:`DocumentArtifact`.

The WeasyPrint layout rules (A4 portrait via the CSS ``@page`` rule, no
fixed pixel widths, proportional scaling) are enforced in two places:
they are instructed to the LLM in
:data:`document_gen.prompts.document_html_system_prompt`, and they are
applied unconditionally to the model output by
:func:`sanitize_document_html` below.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from document_gen import document_query, figures
from document_gen.generators.pdf_gen import html_to_pdf
from document_gen.llm import get_chat_backend
from document_gen.models.company import DocumentType, SyntheticCompany
from document_gen.models.figures import FigureExtraction, FigureSpec
from document_gen.models.document import DocumentPlan
from document_gen.prompts import (
    quick_document_content_prompt,
    quick_document_figures_prompt,
    quick_document_html_prompt,
    quick_document_html_system_prompt,
    quick_document_plan_prompt,
    document_content_prompt,
    document_figures_prompt,
    document_html_prompt,
    document_html_system_prompt,
    document_plan_prompt,
)

logger = logging.getLogger(__name__)

#: Output-token cap for the stage-1 markdown draft (keeps the draft, and
#: the stage-2 HTML prompt that embeds it, from growing unbounded).
MARKDOWN_MAX_TOKENS = 8192

#: Output-token cap for the stage-2 HTML+CSS document.
HTML_MAX_TOKENS = 12000

#: Fraction of the token caps kept when the "Quick Doc" option is on
#: (the caps are cut down by 80%).
QUICK_DOC_TOKEN_FRACTION = 0.2

#: Key under which the document output directory is stored in the
#: ``user_settings`` TinyDB collection (``{"output_dir": "..."}``).
DOCUMENTS_SETTINGS_KEY = "documents"

#: Environment variable holding the default document output directory.
DOCUMENTS_DIR_ENV = "DOCUMENTS_DIR"

#: Legacy environment variable (finance-era name), still honored as a
#: fallback when :data:`DOCUMENTS_DIR_ENV` is unset.
LEGACY_DOCUMENTS_DIR_ENV = "REPORTS_DIR"

#: Canonical page rule injected/overridden in every generated document.
_PAGE_RULE = "@page { size: A4 portrait; margin: 2cm; }"

_AT_PAGE = re.compile(r"@page\b")
_STYLE_BLOCK = re.compile(r"(<style[^>]*>)(.*?)(</style>)", re.DOTALL | re.IGNORECASE)
_TOP_LEVEL_DECL = re.compile(r"(?<![\w-])(?:size|margin)\s*:\s*[^;{}]*;?")
_FIXED_WIDTH_RULE = re.compile(
    r"((?:^|[\s,}>])(?:body|main|table|\.page|#page)\s*\{[^{}]*\})",
    re.IGNORECASE,
)


@dataclass
class DocumentArtifact:
    """Result of one PDF document generation run."""

    company_id: int
    report_name: str
    markdown: str
    html: str
    pdf_path: Path
    figures: list[FigureSpec] = field(default_factory=list)
    gen_tracing: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


def resolve_output_dir() -> Path | None:
    """Return the effective document output directory, or ``None``.

    Resolution order: the saved ``"documents"`` user setting
    (``output_dir``), then the :data:`DOCUMENTS_DIR_ENV` environment
    variable (with the legacy :data:`LEGACY_DOCUMENTS_DIR_ENV` as a
    last-resort fallback).

    Returns:
        The output directory as a (not yet created) path, or ``None``
        when neither source provides one.
    """
    saved = document_query.get_setting(DOCUMENTS_SETTINGS_KEY)
    if saved:
        value = saved.get("output_dir")
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
    for env_var in (DOCUMENTS_DIR_ENV, LEGACY_DOCUMENTS_DIR_ENV):
        env = os.getenv(env_var)
        if env and env.strip():
            return Path(env).expanduser()
    return None


# ---------------------------------------------------------------------------
# Report type resolution
# ---------------------------------------------------------------------------


def resolve_document_type(company: dict, document: str) -> DocumentType:
    """Resolve *document* (a name or index string) to one of the company's document types.

    Matching is by document type name (case-insensitive) first; a purely
    numeric *document* is treated as a 0-based index into the company's
    document list.

    Args:
        company: A company document as returned by
            :func:`document_gen.document_query.get_company` (must carry
            a ``reports`` list).
        document: Document type name or index string.

    Returns:
        The matching :class:`~document_gen.models.company.DocumentType`.

    Raises:
        ValueError: When the company has no documents or *document* matches none.
    """
    reports = [DocumentType.model_validate(r) for r in company.get("reports") or []]
    if not reports:
        raise ValueError("Company has no document types")
    key = document.strip().lower()
    for item in reports:
        if item.name.lower() == key:
            return item
    if key.isdigit():
        index = int(key)
        if 0 <= index < len(reports):
            return reports[index]
    raise ValueError(f"Document type '{document}' not found for company")


# ---------------------------------------------------------------------------
# HTML sanitization (hardcoded WeasyPrint rules)
# ---------------------------------------------------------------------------


def _extract_document(html: str) -> str:
    """Strip code fences / surrounding prose from an LLM HTML response.

    Returns the text from the first ``<!DOCTYPE``/``<html`` marker to the
    last ``</html>``, or *html* unchanged when no markers are found.
    """
    lowered = html.lower()
    start = -1
    for marker in ("<!doctype", "<html"):
        index = lowered.find(marker)
        if index != -1 and (start == -1 or index < start):
            start = index
    if start == -1:
        return html
    end = lowered.rfind("</html>")
    if end == -1:
        return html[start:]
    return html[start : end + len("</html>")]


def _find_rule_end(text: str, open_idx: int) -> int:
    """Return the index just past the closing brace of a CSS rule.

    Args:
        text: CSS text.
        open_idx: Index of the rule's opening ``{``.
    """
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _strip_top_level_declarations(body: str) -> str:
    """Remove top-level ``size:``/``margin:`` declarations from a rule body.

    Declarations nested inside margin boxes (e.g. ``@bottom-center { ... }``)
    are left untouched.
    """
    result: list[str] = []
    last = 0
    for match in _TOP_LEVEL_DECL.finditer(body):
        depth = body[: match.start()].count("{") - body[: match.start()].count("}")
        if depth == 0:
            result.append(body[last : match.start()])
            last = match.end()
    result.append(body[last:])
    return "".join(result)


def _fix_page_rules(css: str) -> str:
    """Force A4 portrait on the CSS ``@page`` rule(s).

    The first ``@page`` rule is prefixed with the canonical
    :data:`_PAGE_RULE`; any ``size:``/``margin:`` declarations inside all
    ``@page`` rules are stripped so they cannot override it. Nested
    margin-box rules (page numbers etc.) are preserved.
    """
    out: list[str] = []
    pos = 0
    first = True
    while True:
        match = _AT_PAGE.search(css, pos)
        if match is None:
            out.append(css[pos:])
            break
        out.append(css[pos : match.start()])
        open_idx = css.find("{", match.end())
        if open_idx == -1:
            out.append(css[match.start() :])
            break
        end_idx = _find_rule_end(css, open_idx)
        body = _strip_top_level_declarations(css[open_idx + 1 : end_idx - 1]).strip()
        if first:
            out.append(_PAGE_RULE)
            first = False
        if body:
            out.append(f" @page {{{body}}}")
        pos = end_idx
    if first:
        out.append(_PAGE_RULE)
    return "".join(out)


def _strip_fixed_px_widths(css: str) -> str:
    """Replace fixed pixel widths on page-level elements with ``100%``.

    Best-effort pass over simple (non-nested) rule blocks whose selector
    is ``body``, ``main``, ``table``, ``.page`` or ``#page``.
    """

    def _fix(match: re.Match[str]) -> str:
        return re.sub(
            r"width\s*:\s*\d+(?:\.\d+)?px",
            "width: 100%",
            match.group(0),
            flags=re.IGNORECASE,
        )

    return _FIXED_WIDTH_RULE.sub(_fix, css)


def sanitize_document_html(html: str) -> str:
    """Apply the hardcoded WeasyPrint rules to an LLM-generated HTML document.

    - Extracts the HTML document from code fences / surrounding prose.
    - Ensures a ``<style>`` block exists and forces
      ``@page { size: A4 portrait; margin: 2cm; }`` (any conflicting
      ``size``/``margin`` in existing ``@page`` rules is removed).
    - Replaces fixed pixel widths on page-level elements with ``100%``.

    Args:
        html: The raw HTML document string from the LLM.

    Returns:
        The sanitized HTML document string.
    """
    doc = _extract_document(html)

    def _process(match: re.Match[str]) -> str:
        css = _fix_page_rules(match.group(2))
        css = _strip_fixed_px_widths(css)
        return f"{match.group(1)}{css}{match.group(3)}"

    if _STYLE_BLOCK.search(doc):
        doc = _STYLE_BLOCK.sub(_process, doc)
    else:
        tag = f"<style>{_PAGE_RULE}</style>"
        if "</head>" in doc:
            doc = doc.replace("</head>", f"{tag}</head>", 1)
        elif re.search(r"<body[^>]*>", doc, re.IGNORECASE):
            doc = re.sub(
                r"(<body[^>]*>)", rf"{tag}\1", doc, count=1, flags=re.IGNORECASE
            )
        else:
            doc = tag + doc
    return doc


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Reduce *text* to a file-name-safe slug (lowercase, underscores)."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "document"


def _document_title(markdown: str) -> str | None:
    """Extract the document title from a markdown draft.

    The title is the text of the first top-level ``#`` heading (the LLM
    is instructed to make it a concise document name). Headings inside
    fenced code blocks are ignored.

    Args:
        markdown: The stage-1 markdown draft.

    Returns:
        The title text, or ``None`` when no top-level heading is found.
    """
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^#\s+(.+)$", stripped)
        if match:
            return match.group(1).strip()
    return None


def _unique_path(directory: Path, stem: str, extension: str) -> Path:
    """Return a non-colliding path for *stem*.*extension* in *directory*."""
    path = directory / f"{stem}.{extension}"
    counter = 1
    while path.exists():
        path = directory / f"{stem}_{counter}.{extension}"
        counter += 1
    return path


def _record_document(
    company_id: int,
    report_name: str,
    path: Path,
    gen_tracing: dict[str, Any] | None = None,
) -> None:
    """Record a generated file in the ``report_documents`` collection.

    Best-effort: a storage failure is logged but never fails the
    generation, since the file itself is the primary artifact. Future
    generators (CSV, DOCX, Excel, …) should follow the same pattern.

    Args:
        company_id: TinyDB ``doc_id`` of the owning company.
        report_name: Name of the report type the file was generated for.
        path: Path of the generated file.
        gen_tracing: Optional per-stage generation trace (prompts,
            outputs, timings) stored on the record as ``gen_tracing``.
    """
    try:
        report_type_id = document_query.get_document_type_id(company_id, report_name)
        if report_type_id is None:
            logger.warning(
                "Document record: no document-type id for %r (company %s); "
                "not recorded",
                report_name,
                company_id,
            )
            return
        document_query.save_document(
            company_id, report_type_id, path, gen_tracing=gen_tracing
        )
    except Exception:
        logger.exception("Document record: failed to record %s", path)


#: Fallback plan used when the report-plan LLM call fails: keep the
#: historical behaviour (TOC included) with a neutral design brief.
_DEFAULT_DOCUMENT_PLAN = DocumentPlan(
    include_toc=True,
    toc_reason="Report plan call failed; defaulting to a table of contents.",
    design_direction="Professional, neutral company document styling.",
    palette=["#1F3A5F", "#333333", "#FFFFFF"],
    typography="serif headings, sans-serif body",
    layout_style="corporate",
)


def _plan_document(
    backend: Any,
    profile: SyntheticCompany,
    report_type: DocumentType,
    kinds: list[str],
    quick_doc: bool,
    user_input: str | None,
    seed: int,
    model_name: str | None,
) -> tuple[DocumentPlan, str, float, bool]:
    """Ask the LLM to plan the report (TOC decision + design brief).

    Falls back to :data:`_DEFAULT_DOCUMENT_PLAN` (logged) when the call
    fails, so a failed plan step never aborts the generation.

    Args:
        backend: The chat LLM backend.
        profile: The company profile.
        report_type: The report type being generated.
        kinds: The requested figure kinds (may be empty).
        quick_doc: Whether the quick-doc option is on.
        user_input: Optional free-text user guidance.
        seed: Random seed for deterministic runs.
        model_name: Optional model ID override.

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
    if quick_doc:
        # Quick docs use the smaller, content-focused plan prompt (no TOC,
        # minimal design) instead of the full design-identity prompt.
        prompt = (
            quick_document_plan_prompt.replace(
                "<company_profile>", profile.format_prompt()
            )
            .replace("<document_type>", report_type_text)
            .replace("<figures>", ", ".join(kinds) if kinds else "none")
            .replace(
                "<user_input>",
                user_input.strip() if user_input and user_input.strip() else "None.",
            )
        )
    else:
        prompt = (
            document_plan_prompt.replace("<company_profile>", profile.format_prompt())
            .replace("<document_type>", report_type_text)
            .replace("<quick_doc>", "yes" if quick_doc else "no")
            .replace("<figures>", ", ".join(kinds) if kinds else "none")
            .replace(
                "<user_input>",
                user_input.strip() if user_input and user_input.strip() else "None.",
            )
        )
    t_step = time.perf_counter()
    logger.info("PDF document: LLM call (document plan) started")
    try:
        plan = backend.query(
            prompt,
            model=DocumentPlan,
            deterministic=False,
            seed=seed,
            model_name=model_name,
            thinking=not quick_doc,
        )
    except Exception:
        logger.exception("PDF document: document plan call failed; using default plan")
        return _DEFAULT_DOCUMENT_PLAN, prompt, time.perf_counter() - t_step, True
    elapsed = time.perf_counter() - t_step
    logger.info(
        "PDF document: LLM call (document plan) done in %.3fs (toc=%s, "
        "style=%r, palette=%s) — %s",
        elapsed,
        plan.include_toc,
        plan.layout_style,
        ", ".join(plan.palette),
        plan.toc_reason,
    )
    return plan, prompt, elapsed, False


def _toc_instruction(include_toc: bool) -> str:
    """Build the ``<toc>`` instruction for the stage-1 markdown prompt.

    Args:
        include_toc: Whether the draft should start with a TOC.

    Returns:
        The instruction text for the ``<toc>`` prompt slot.
    """
    if include_toc:
        return (
            "Start with a **table of contents** that lists every section "
            "and subsection you will write."
        )
    return (
        "Do **not** include a table of contents; start directly with the "
        "first section."
    )


def _design_brief_text(plan: DocumentPlan) -> str:
    """Render a :class:`DocumentPlan` design brief for the HTML prompt.

    Args:
        plan: The report plan.

    Returns:
        The instruction text for the ``<design_brief>`` prompt slot.
    """
    return (
        f"Design direction: {plan.design_direction}\n"
        f"Color palette: {', '.join(plan.palette)}\n"
        f"Typography: {plan.typography}\n"
        f"Layout style: {plan.layout_style}"
    )


def _content_figures_instruction(kinds: list[str], quick: bool = False) -> str:
    """Build the ``<figures>`` instruction for the stage-1 markdown prompt.

    Args:
        kinds: The allowed matplotlib figure kinds (may be empty).
        quick: When ``True`` (quick doc), ask for at most 1 figure
            instead of 1-2.

    Returns:
        The instruction text for the ``<figures>`` prompt slot.
    """
    if not kinds:
        return "None. Do not include any figures."
    count = "at most 1" if quick else "1-2"
    return (
        f"Include {count} figures illustrating the sample data tables, using "
        f"only these kinds: {', '.join(kinds)}. Declare each figure as a "
        "fenced ```chart block placed directly after the table it "
        "illustrates, in exactly this format:\n\n"
        "```chart\n"
        "type: bar\n"
        "title: Units sold by region, 2021-2024\n"
        "data:\n"
        "Technology, 412\n"
        "Healthcare, 268\n"
        "```\n\n"
        "Rules: `type` is one of the allowed kinds; `data` rows are CSV "
        "copied verbatim from the sample data table (first column row "
        "labels, then one numeric column per series); do not invent "
        "numbers. Plain markdown tables without a figure block are fine "
        "on their own. Figures support the narrative — they never replace "
        "it: introduce each figure with a short paragraph before its "
        "table, and follow the figure block with 1-2 paragraphs "
        "interpreting the chart (what the data shows, key takeaways). "
        "Never place two figure blocks back-to-back; at least one "
        "paragraph of prose must separate any two figures."
    )


def _html_figures_instruction(specs: list[FigureSpec]) -> str:
    """Build the ``<figures>`` instruction for the stage-2 HTML prompt.

    Lists the extracted figures with their placeholder tokens; the LLM
    only ever sees these lightweight tokens, never image data.

    Args:
        specs: The extracted figure specs (placeholder numbers follow
            this order, 1-based).

    Returns:
        The instruction text for the ``<figures>`` prompt slot.
    """
    if not specs:
        return "None. Do not include any figures."
    lines = [
        f"Figure {index}: {spec.title or 'Untitled'} ({spec.kind}) — "
        f"placeholder {figures.figure_placeholder(index)}"
        for index, spec in enumerate(specs, start=1)
    ]
    return "\n".join(lines)


def generate_document_pdf(
    company_id: int,
    report: str,
    user_input: str | None = None,
    model_name: str | None = None,
    output_dir: Path | None = None,
    figure_kinds: list[str] | None = None,
    quick_doc: bool = False,
    gen_tracing: bool = False,
) -> DocumentArtifact:
    """Generate a PDF document for a stored company.

    Pipeline: LLM document plan (TOC decision + design brief) -> LLM
    markdown draft -> figure extraction (heuristic first, LLM fallback)
    -> LLM HTML+CSS (styled per the design brief, carrying
    ``{{FIGURE_n}}`` placeholders) -> :func:`sanitize_document_html` ->
    base64 matplotlib figure injection -> WeasyPrint PDF.

    Args:
        company_id: TinyDB ``doc_id`` of the company.
        report: Document type name (case-insensitive) or 0-based index
            string into the company's document list.
        user_input: Optional free-text guidance for the document content.
        model_name: Optional model ID override for the LLM queries.
        output_dir: Output directory override. When ``None``,
            :func:`resolve_output_dir` is used.
        figure_kinds: The allowed matplotlib figure kinds (e.g.
            ``["bar", "line"]``). When ``None`` or empty, no figures
            are included.
            Allowed figures are extracted from the markdown (heuristic
            first, LLM fallback) and rendered with matplotlib; the
            images are embedded into the HTML after the HTML LLM step.
        quick_doc: When ``True``, use the smaller, content-focused quick
            prompt set (no TOC, at most one data table, at most two
            figures, minimal single-accent HTML styling), cut the
            output-token caps for both the markdown draft and the HTML+CSS
            stage by 80% (keep :data:`QUICK_DOC_TOKEN_FRACTION`), at most
            one figure instead of 1-2, and
            disable model thinking/reasoning on every LLM call, producing
            a shorter, faster report.
        gen_tracing: When ``True``, the aggregated per-stage trace
            (prompts, outputs, timings) is persisted on the
            document record under the ``gen_tracing`` field.
            The trace is always built and returned on the artifact;
            this flag only controls database persistence.

    Returns:
        The generated :class:`DocumentArtifact` (markdown, HTML, PDF path,
        figure specs, and the aggregated per-stage ``gen_tracing``
        trace; the trace is stored on the document record only
        when *gen_tracing* is ``True``).

    Raises:
        ValueError: When the company or document type is missing, or when
            no effective output directory is configured.
    """
    t_total = time.perf_counter()
    logger.info("PDF document: starting for company %s (report=%r)", company_id, report)
    t_step = time.perf_counter()

    doc = document_query.get_company(company_id)
    if doc is None:
        raise ValueError(f"Company {company_id} not found")
    profile_data = doc.get("profile")
    if profile_data is None:
        raise ValueError(f"Company {company_id} has no profile")
    profile = SyntheticCompany.model_validate(profile_data)
    report_type = resolve_document_type(doc, report)

    out_dir = output_dir or resolve_output_dir()
    if out_dir is None:
        raise ValueError(
            "No document output directory set: configure it in the web UI "
            f"Settings tab or set the {DOCUMENTS_DIR_ENV} environment variable."
        )

    seed = doc.get("seed", 0)
    backend = get_chat_backend()
    token_scale = QUICK_DOC_TOKEN_FRACTION if quick_doc else 1.0
    markdown_max_tokens = int(MARKDOWN_MAX_TOKENS * token_scale)
    html_max_tokens = int(HTML_MAX_TOKENS * token_scale)
    # Quick docs trade quality for speed: besides the reduced token caps,
    # model thinking/reasoning is disabled on every LLM call in the run.
    thinking = not quick_doc
    logger.info(
        "PDF document: resolved company %s report %r (setup %.3fs)",
        company_id,
        report_type.name,
        time.perf_counter() - t_step,
    )

    kinds = list(figure_kinds or [])

    # Per-stage trace (prompts, outputs, timings) aggregated as the
    # pipeline runs; stored on the document record as
    # ``gen_tracing`` and returned on the artifact.
    trace: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "company_id": company_id,
        "report": report_type.name,
        "user_input": user_input,
        "quick_doc": quick_doc,
        "stages": {},
    }

    # Stage 0: quick LLM plan — TOC decision + design brief for the
    # HTML+CSS stage (falls back to defaults on failure).
    plan, plan_prompt, plan_elapsed, plan_used_default = _plan_document(
        backend,
        profile,
        report_type,
        kinds,
        quick_doc,
        user_input,
        seed,
        model_name,
    )
    trace["stages"]["plan"] = {
        "prompt": plan_prompt,
        "output": plan.model_dump(mode="json"),
        "elapsed_s": round(plan_elapsed, 3),
        "used_default_fallback": plan_used_default,
    }

    report_type_text = (
        f"name: {report_type.name}\n"
        f"category: {report_type.category}\n"
        f"purpose: {report_type.purpose}"
    )
    # Quick docs use the smaller, content-focused draft prompt (no TOC
    # slot: quick reports never carry a table of contents).
    content_template = (
        quick_document_content_prompt if quick_doc else document_content_prompt
    )
    content_prompt = (
        content_template.replace("<company_profile>", profile.format_prompt())
        .replace("<document_type>", report_type_text)
        .replace(
            "<user_input>",
            user_input.strip() if user_input and user_input.strip() else "None.",
        )
        .replace("<figures>", _content_figures_instruction(kinds, quick=quick_doc))
    )
    if not quick_doc:
        content_prompt = content_prompt.replace(
            "<toc>", _toc_instruction(plan.include_toc)
        )
    t_step = time.perf_counter()
    logger.info("PDF document: LLM call (markdown draft) started")
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
        "PDF document: LLM call (markdown draft) done in %.3fs (%d chars)",
        markdown_elapsed,
        len(markdown),
    )
    trace["stages"]["markdown"] = {
        "prompt": content_prompt,
        "output": markdown,
        "elapsed_s": round(markdown_elapsed, 3),
    }

    # Figure extraction: deterministic heuristic on the fenced figure
    # blocks first; an LLM fallback runs when the markdown carries no
    # figure blocks at all, or when the parsed figures do not cover
    # every requested kind (e.g. only bar blocks found but bar and pie
    # were requested).
    figure_specs: list[FigureSpec] = []
    figures_trace: dict[str, Any] = {
        "requested_kinds": kinds,
        "heuristic_specs": [],
        "llm": None,
    }
    if kinds:
        t_step = time.perf_counter()
        heuristic_specs = figures.extract_figure_specs(markdown)
        if heuristic_specs:
            logger.info(
                "PDF document: heuristic extracted %d figure spec(s) in %.3fs",
                len(heuristic_specs),
                time.perf_counter() - t_step,
            )
        figure_specs.extend(heuristic_specs)
        figures_trace["heuristic_specs"] = [
            s.model_dump(mode="json") for s in heuristic_specs
        ]
        missing_kinds = [k for k in kinds if k not in {s.kind for s in figure_specs}]
        if missing_kinds:
            if figure_specs:
                logger.info(
                    "PDF document: requested kinds not covered by heuristic "
                    "(missing: %s); LLM figure extraction started",
                    ", ".join(missing_kinds),
                )
            else:
                logger.info(
                    "PDF document: no figure blocks in markdown; LLM figure "
                    "extraction started"
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
                "PDF document: LLM extraction returned %d drawable figure "
                "spec(s) in %.3fs",
                len(llm_specs),
                llm_elapsed,
            )
            figures_trace["llm"] = {
                "prompt": figures_prompt,
                "output": [s.model_dump(mode="json") for s in llm_specs],
                "elapsed_s": round(llm_elapsed, 3),
            }
    figures_trace["specs"] = [s.model_dump(mode="json") for s in figure_specs]
    trace["stages"]["figures"] = figures_trace

    # Quick docs use the minimal single-accent HTML prompts instead of the
    # full design-brief-driven document-designer prompts.
    html_template = quick_document_html_prompt if quick_doc else document_html_prompt
    html_system_prompt = (
        quick_document_html_system_prompt if quick_doc else document_html_system_prompt
    )
    html_prompt = (
        html_template.replace("<company_profile>", profile.format_prompt())
        .replace("<design_brief>", _design_brief_text(plan))
        .replace("<markdown>", markdown)
        .replace("<figures>", _html_figures_instruction(figure_specs))
    )
    t_step = time.perf_counter()
    logger.info("PDF document: LLM call (HTML+CSS) started")
    raw_html = backend.complete(
        html_prompt,
        system=html_system_prompt,
        deterministic=False,
        seed=seed,
        model_name=model_name,
        max_tokens=html_max_tokens,
        thinking=thinking,
    )
    html_llm_elapsed = time.perf_counter() - t_step
    logger.info(
        "PDF document: LLM call (HTML+CSS) done in %.3fs (%d chars)",
        html_llm_elapsed,
        len(raw_html),
    )

    t_step = time.perf_counter()
    html = sanitize_document_html(raw_html)
    html_sanitize_elapsed = time.perf_counter() - t_step
    logger.info(
        "PDF document: HTML sanitization done in %.3fs (%d chars)",
        html_sanitize_elapsed,
        len(html),
    )
    trace["stages"]["html"] = {
        "system_prompt": html_system_prompt,
        "prompt": html_prompt,
        "raw_output": raw_html,
        "sanitized_html": html,
        "llm_elapsed_s": round(html_llm_elapsed, 3),
        "sanitize_elapsed_s": round(html_sanitize_elapsed, 3),
    }

    # Inject the matplotlib-rendered figures (base64 data URIs) only now,
    # after the LLM step, so the image payloads never enter the prompt.
    if figure_specs:
        t_step = time.perf_counter()
        html = figures.embed_figure_placeholders(html, figure_specs)
        logger.info(
            "PDF document: embedded %d figure(s) in %.3fs (%d chars)",
            len(figure_specs),
            time.perf_counter() - t_step,
            len(html),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # File name comes from the concise document title the LLM wrote as
    # the markdown's first heading; fall back to the report type name
    # when no heading is found. Collisions get a numeric suffix.
    stem = _slug(_document_title(markdown) or report_type.name)
    path = _unique_path(out_dir, stem, "pdf")
    t_step = time.perf_counter()
    logger.info("PDF document: WeasyPrint render started -> %s", path)
    html_to_pdf(html, path)
    pdf_render_elapsed = time.perf_counter() - t_step
    logger.info(
        "PDF document: WeasyPrint render done in %.3fs (%d bytes)",
        pdf_render_elapsed,
        path.stat().st_size,
    )
    trace["stages"]["pdf"] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "elapsed_s": round(pdf_render_elapsed, 3),
    }
    trace["finished_at"] = datetime.now(UTC).isoformat()
    trace["total_elapsed_s"] = round(time.perf_counter() - t_total, 3)
    _record_document(
        company_id, report_type.name, path, gen_tracing=trace if gen_tracing else None
    )
    logger.info(
        "PDF document: finished for company %s in %.3fs total -> %s",
        company_id,
        time.perf_counter() - t_total,
        path,
    )
    return DocumentArtifact(
        company_id=company_id,
        report_name=report_type.name,
        markdown=markdown,
        html=html,
        pdf_path=path,
        figures=figure_specs,
        gen_tracing=trace,
    )
