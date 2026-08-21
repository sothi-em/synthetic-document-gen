"""LLM-driven PNG image document generation pipeline.

Single-page image document: same LLM pipeline shape as the PDF pipeline
(plan -> markdown -> figures -> HTML+CSS), rendered to one PNG and
optionally passed through the distress (scanned/aged look) post-processing
in :func:`document_gen.generators.png_gen.distress_image`.

Stages (see :func:`generate_document_image`):

1. A small structured LLM call produces a **document plan** (design
   brief; the TOC decision is ignored — image documents never carry a
   TOC). On failure the pipeline falls back to defaults and continues.
2. The chat LLM drafts the content as **markdown** (single-page
   contract: title + 3-5 short sections, at most one table, at most
   one figure; full token cap, thinking on).
3. Figures are extracted from the markdown (heuristic first, LLM
   fallback — identical logic to the PDF/Excel pipelines) and rendered
   with matplotlib.
4. The chat LLM converts the markdown into a standalone **HTML+CSS**
   document (everything on one page, no page furniture, compact type
   scale); :func:`sanitize_image_html` forces the canonical page rule
   (A4 portrait or content-sized) and the base64 figure images are
   injected afterwards.
5. :func:`document_gen.generators.png_gen.html_to_png` renders the HTML
   to a single PNG (page 1 of the render).
6. When enabled, :func:`document_gen.generators.png_gen.distress_image`
   post-processes the PNG in-place into a scanned/aged look (noise and
   warp are seeded from the trace; stain positions are intentionally
   random on every run). With tracing on, the untouched render is
   preserved first as ``<stem>_original.png`` next to the document and
   referenced from the trace
   (``stages.distress.original_path``).

The pipeline aggregates a per-stage **trace** (prompts, outputs,
timings) stored on the document record under the ``gen_tracing`` field
(see :func:`document_gen.document_query.save_document`) and returned on
the :class:`ImageArtifact`.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from document_gen import document_query, figures
from document_gen.document_excel import _extract_figure_specs
from document_gen.document_pdf import (
    DOCUMENTS_DIR_ENV,
    _AT_PAGE,
    _STYLE_BLOCK,
    _content_figures_instruction,
    _design_brief_text,
    _document_title,
    _extract_document,
    _find_rule_end,
    _html_figures_instruction,
    _plan_document,
    _record_document,
    _slug,
    _strip_top_level_declarations,
    _unique_path,
    resolve_document_type,
    resolve_output_dir,
)
from document_gen.generators.png_gen import distress_image, html_to_png
from document_gen.llm import get_chat_backend
from document_gen.models import (
    DistressOptions,
    DocumentType,
    FigureSpec,
    SyntheticCompany,
)
from document_gen.prompts import (
    image_content_prompt,
    image_html_prompt,
    image_html_system_prompt,
)

logger = logging.getLogger(__name__)

#: Output-token cap for the stage-1 markdown draft (keeps the draft, and
#: the stage-2 HTML prompt that embeds it, from growing unbounded).
MARKDOWN_MAX_TOKENS = 8192

#: Output-token cap for the stage-2 HTML+CSS document.
HTML_MAX_TOKENS = 12000

#: Canonical page rule for A4-locked image documents.
_PAGE_RULE_A4 = "@page { size: A4 portrait; margin: 2cm; }"

#: Canonical page rule for content-sized image documents (WeasyPrint
#: supports ``size: auto`` -> the page sizes itself to the content).
_PAGE_RULE_AUTO = "@page { size: auto; margin: 2cm; }"


def _fix_page_rules(css: str, page_rule: str) -> str:
    """Force *page_rule* on the CSS ``@page`` rule(s).

    The first ``@page`` rule is prefixed with the canonical *page_rule*;
    any ``size:``/``margin:`` declarations inside all ``@page`` rules are
    stripped so they cannot override it. Nested margin-box rules (page
    numbers etc.) are preserved.
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
            out.append(page_rule)
            first = False
        if body:
            out.append(f" @page {{{body}}}")
        pos = end_idx
    if first:
        out.append(page_rule)
    return "".join(out)


def _apply_page_rule(doc: str, page_rule: str) -> str:
    """Force *page_rule* onto *doc*'s CSS.

    Ensures a ``<style>`` block exists and prefixes the first ``@page``
    rule with *page_rule* (any conflicting ``size:``/``margin:`` in
    existing ``@page`` rules is removed).
    """

    def _process(match: re.Match[str]) -> str:
        return f"{match.group(1)}{_fix_page_rules(match.group(2), page_rule)}{match.group(3)}"

    if _STYLE_BLOCK.search(doc):
        return _STYLE_BLOCK.sub(_process, doc)
    tag = f"<style>{page_rule}</style>"
    if "</head>" in doc:
        return doc.replace("</head>", f"{tag}</head>", 1)
    if re.search(r"<body[^>]*>", doc, re.IGNORECASE):
        return re.sub(r"(<body[^>]*>)", rf"{tag}\1", doc, count=1, flags=re.IGNORECASE)
    return tag + doc


def sanitize_image_html(html: str, a4: bool = True) -> str:
    """Apply the hardcoded WeasyPrint rules to an LLM-generated HTML document.

    - Extracts the HTML document from code fences / surrounding prose.
    - Ensures a ``<style>`` block exists and forces the canonical page
      rule (any conflicting ``size``/``margin`` in existing ``@page``
      rules is removed):

      - ``a4=True`` -> ``@page { size: A4 portrait; margin: 2cm; }``
      - ``a4=False`` -> ``@page { size: auto; margin: 2cm; }``
        (content-sized intent; WeasyPrint ignores ``size: auto`` and
        :func:`document_gen.generators.png_gen.html_to_png` replaces it
        with an explicit measured size via :func:`force_page_size`).

    Args:
        html: The raw HTML document string from the LLM.
        a4: Lock the page to A4 portrait (True) or size it to the
            content (False).

    Returns:
        The sanitized HTML document string.
    """
    doc = _extract_document(html)
    page_rule = _PAGE_RULE_A4 if a4 else _PAGE_RULE_AUTO
    return _apply_page_rule(doc, page_rule)


def save_original_png(path: Path) -> Path:
    """Preserve an untouched copy of a rendered PNG alongside itself.

    Copies *path* to ``<stem>_original.png`` in the same directory (e.g.
    ``foo.png`` -> ``foo_original.png``), using
    :func:`document_pdf._unique_path` for collision safety (a numeric
    suffix is added when the target already exists). The copy is trace
    support data, not a document: it is never registered in TinyDB and
    is referenced only via ``gen_tracing``.

    Args:
        path: The rendered PNG to preserve.

    Returns:
        The path of the preserved copy.
    """
    target = _unique_path(path.parent, f"{path.stem}_original", "png")
    shutil.copyfile(path, target)
    return target


def force_page_size(html: str, width: str, height: str) -> str:
    """Force an explicit ``@page`` size on a sanitized HTML document.

    WeasyPrint (v61+) does not support ``size: auto``; content-sized
    pages are achieved by measuring the content on a tall page and
    re-rendering with the explicit size produced here.

    Args:
        html: A sanitized HTML document string.
        width: CSS width (e.g. ``"210mm"``).
        height: CSS height (e.g. ``"180mm"``).

    Returns:
        The HTML document string with the forced page rule.
    """
    return _apply_page_rule(html, f"@page {{ size: {width} {height}; margin: 2cm; }}")


@dataclass
class ImageArtifact:
    """Result of one PNG image document generation run."""

    company_id: int
    report_name: str
    markdown: str
    html: str
    png_path: Path
    figures: list[FigureSpec] = field(default_factory=list)
    gen_tracing: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def generate_document_image(
    company_id: int,
    report: str,
    user_input: str | None = None,
    model_name: str | None = None,
    output_dir: Path | None = None,
    figure_kinds: list[str] | None = None,
    a4_aspect: bool = True,
    distress: DistressOptions | None = None,
    gen_tracing: bool = False,
) -> ImageArtifact:
    """Generate a single-page PNG image document for a stored company.

    Pipeline: LLM document plan (design brief; TOC decision ignored) ->
    LLM markdown draft (single-page contract) -> figure extraction
    (heuristic first, LLM fallback) -> LLM HTML+CSS (one page, no page
    furniture) -> :func:`sanitize_image_html` -> base64 matplotlib
    figure injection -> :func:`html_to_png` (WeasyPrint, page 1) ->
    optional :func:`distress_image` post-processing.

    Args:
        company_id: TinyDB ``doc_id`` of the company.
        report: Document type name (case-insensitive) or 0-based index
            string into the company's document list.
        user_input: Optional free-text guidance for the document content.
        model_name: Optional model ID override for the LLM queries.
        output_dir: Output directory override. When ``None``,
            :func:`document_pdf.resolve_output_dir` is used.
        figure_kinds: The allowed matplotlib figure kinds (e.g.
            ``["bar", "line"]``). When ``None`` or empty, no figures
            are included.
        a4_aspect: When ``True`` (default), lock the page to A4
            portrait; when ``False``, the page sizes itself to the
            content (width stays A4 width).
        distress: Optional per-effect controls for the distress
            (scanned/aged look) pass. When ``None`` or
            ``distress.enabled`` is ``False``, the PNG is left as a
            perfect render. The pass seed is ``distress.seed`` when set,
            otherwise the company seed.
        gen_tracing: When ``True``, the aggregated per-stage trace
            (prompts, outputs, timings) is persisted on the
            document record under the ``gen_tracing`` field. When the
            distress pass runs under tracing, the untouched render is
            also preserved as ``<stem>_original.png`` next to the
            document and referenced from the trace
            (``stages.distress.original_path``). The trace is always
            built and returned on the artifact; this flag only
            controls database persistence.

    Returns:
        The generated :class:`ImageArtifact` (markdown, HTML, PNG path,
        figure specs, and the aggregated per-stage ``gen_tracing``
        trace; the trace is stored on the document record only
        when *gen_tracing* is ``True``).

    Raises:
        ValueError: When the company or document type is missing, or when
            no effective output directory is configured.
    """
    t_total = time.perf_counter()
    logger.info(
        "Image document: starting for company %s (report=%r)", company_id, report
    )
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
    distress_options = distress if distress is not None else DistressOptions()
    # Image documents always use the full token caps and keep model
    # thinking on (there is no quick-doc variant).
    thinking = True
    logger.info(
        "Image document: resolved company %s report %r (setup %.3fs)",
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
        "a4_aspect": a4_aspect,
        "stages": {},
    }

    # Stage 0: quick LLM plan — design brief for the HTML+CSS stage
    # (falls back to defaults on failure). The TOC decision is ignored:
    # image documents never carry a table of contents.
    plan, plan_prompt, plan_elapsed, plan_used_default = _plan_document(
        backend,
        profile,
        report_type,
        kinds,
        quick_doc=False,
        user_input=user_input,
        seed=seed,
        model_name=model_name,
    )
    trace["stages"]["plan"] = {
        "prompt": plan_prompt,
        "output": plan.model_dump(mode="json"),
        "elapsed_s": round(plan_elapsed, 3),
        "used_default_fallback": plan_used_default,
    }

    # Stage 1: single-page markdown draft (no TOC slot: image documents
    # never carry a table of contents).
    report_type_text = (
        f"name: {report_type.name}\n"
        f"category: {report_type.category}\n"
        f"purpose: {report_type.purpose}"
    )
    content_prompt = (
        image_content_prompt.replace("<company_profile>", profile.format_prompt())
        .replace("<document_type>", report_type_text)
        .replace(
            "<user_input>",
            user_input.strip() if user_input and user_input.strip() else "None.",
        )
        .replace("<figures>", _content_figures_instruction(kinds, quick=True))
    )
    t_step = time.perf_counter()
    logger.info("Image document: LLM call (markdown draft) started")
    markdown = backend.complete(
        content_prompt,
        deterministic=False,
        seed=seed,
        model_name=model_name,
        max_tokens=MARKDOWN_MAX_TOKENS,
        thinking=thinking,
    )
    markdown_elapsed = time.perf_counter() - t_step
    logger.info(
        "Image document: LLM call (markdown draft) done in %.3fs (%d chars)",
        markdown_elapsed,
        len(markdown),
    )
    trace["stages"]["markdown"] = {
        "prompt": content_prompt,
        "output": markdown,
        "elapsed_s": round(markdown_elapsed, 3),
    }

    # Stage 2: figure extraction (heuristic first, LLM fallback).
    figure_specs, figures_trace = _extract_figure_specs(
        backend,
        markdown,
        kinds,
        quick_doc=False,
        seed=seed,
        model_name=model_name,
        thinking=thinking,
    )
    trace["stages"]["figures"] = figures_trace

    # Stage 3: HTML+CSS — everything on one page, styled per the design
    # brief; the canonical page rule (A4 or content-sized) is forced on
    # the model output afterwards.
    page_size_text = "A4 portrait" if a4_aspect else "content-sized (auto)"
    html_prompt = (
        image_html_prompt.replace("<company_profile>", profile.format_prompt())
        .replace("<design_brief>", _design_brief_text(plan))
        .replace("<markdown>", markdown)
        .replace("<figures>", _html_figures_instruction(figure_specs))
    )
    html_system_prompt = image_html_system_prompt.replace("<page_size>", page_size_text)
    t_step = time.perf_counter()
    logger.info("Image document: LLM call (HTML+CSS) started")
    raw_html = backend.complete(
        html_prompt,
        system=html_system_prompt,
        deterministic=False,
        seed=seed,
        model_name=model_name,
        max_tokens=HTML_MAX_TOKENS,
        thinking=thinking,
    )
    html_llm_elapsed = time.perf_counter() - t_step
    logger.info(
        "Image document: LLM call (HTML+CSS) done in %.3fs (%d chars)",
        html_llm_elapsed,
        len(raw_html),
    )

    t_step = time.perf_counter()
    html = sanitize_image_html(raw_html, a4_aspect)
    html_sanitize_elapsed = time.perf_counter() - t_step
    logger.info(
        "Image document: HTML sanitization done in %.3fs (%d chars)",
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
            "Image document: embedded %d figure(s) in %.3fs (%d chars)",
            len(figure_specs),
            time.perf_counter() - t_step,
            len(html),
        )

    # Stage 4: WeasyPrint render to a single PNG (page 1). File name
    # comes from the concise document title the LLM wrote as the
    # markdown's first heading; fall back to the report type name when
    # no heading is found. Collisions get a numeric suffix.
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(_document_title(markdown) or report_type.name)
    path = _unique_path(out_dir, stem, "png")
    t_step = time.perf_counter()
    logger.info("Image document: WeasyPrint render started -> %s", path)
    html_to_png(html, path, a4=a4_aspect)
    png_render_elapsed = time.perf_counter() - t_step
    logger.info(
        "Image document: WeasyPrint render done in %.3fs (%d bytes)",
        png_render_elapsed,
        path.stat().st_size,
    )
    trace["stages"]["png"] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "elapsed_s": round(png_render_elapsed, 3),
    }

    # Stage 5: optional distress pass (scanned/aged look), in-place.
    # Noise and warp are seeded from the trace; stain positions are
    # intentionally unseeded (vary per run). When tracing is on and the
    # pass runs, the untouched render is preserved first as
    # ``<stem>_original.png`` next to the document and referenced from
    # the trace (``stages.distress.original_path``).
    distress_seed = distress_options.seed if distress_options.seed is not None else seed
    trace["stages"]["distress"] = {
        "enabled": distress_options.enabled,
        "options": distress_options.model_dump(mode="json"),
        "seed": distress_seed if distress_options.enabled else None,
    }
    if distress_options.enabled:
        if gen_tracing:
            original = save_original_png(path)
            logger.info("Image document: preserved original render -> %s", original)
            trace["stages"]["distress"]["original_path"] = str(original)
        t_step = time.perf_counter()
        logger.info("Image document: distress pass started (seed=%d)", distress_seed)
        distress_image(path, distress_options, distress_seed)
        distress_elapsed = time.perf_counter() - t_step
        logger.info(
            "Image document: distress pass done in %.3fs (%d bytes)",
            distress_elapsed,
            path.stat().st_size,
        )
    else:
        distress_elapsed = 0.0
    trace["stages"]["distress"]["elapsed_s"] = round(distress_elapsed, 3)

    trace["finished_at"] = datetime.now(UTC).isoformat()
    trace["total_elapsed_s"] = round(time.perf_counter() - t_total, 3)
    _record_document(
        company_id, report_type.name, path, gen_tracing=trace if gen_tracing else None
    )
    logger.info(
        "Image document: finished for company %s in %.3fs total -> %s",
        company_id,
        time.perf_counter() - t_total,
        path,
    )
    return ImageArtifact(
        company_id=company_id,
        report_name=report_type.name,
        markdown=markdown,
        html=html,
        png_path=path,
        figures=figure_specs,
        gen_tracing=trace,
    )
