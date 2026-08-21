"""LLM-driven PNG image document generation pipeline.

Single-page image document: same LLM pipeline shape as the PDF pipeline
(plan -> markdown -> figures -> HTML+CSS), rendered to one PNG and
optionally passed through the distress (scanned/aged look) post-processing
in :func:`document_gen.generators.png_gen.distress_image`.

This module currently holds the HTML sanitization step; the full
``generate_document_image`` pipeline will be added here.
"""

from __future__ import annotations

import re

from document_gen.document_pdf import (
    _AT_PAGE,
    _STYLE_BLOCK,
    _extract_document,
    _find_rule_end,
    _strip_top_level_declarations,
)

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
