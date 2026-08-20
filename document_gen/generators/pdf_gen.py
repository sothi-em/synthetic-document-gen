"""PDF file generator using the WeasyPrint HTML -> PDF pipeline.

Rendering is split in two steps so the HTML stage is testable without
WeasyPrint (which pulls in system libraries):

1. :func:`to_html` — data model -> standalone HTML document (with CSS).
2. :meth:`PdfGenerator.generate` — HTML string -> PDF via WeasyPrint.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .base import FileGenerator, data_stem, make_output_path

_BASE_CSS = """
body { font-family: sans-serif; font-size: 10pt; margin: 2cm; }
h1 { font-size: 18pt; }
h2 { font-size: 13pt; margin-top: 1em; }
table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
th, td { border: 1px solid #999; padding: 4px 8px; text-align: left; }
th { background: #eee; }
"""


def _to_payload(data: Any) -> dict[str, Any]:
    """Coerce ``data`` into a plain dict for HTML rendering.

    Args:
        data: A pydantic model, dict, or arbitrary object.

    Returns:
        Plain dict of scalar values.
    """
    if hasattr(data, "model_dump"):
        payload = data.model_dump(mode="json")
    elif isinstance(data, dict):
        payload = data
    else:
        payload = {"value": str(data)}
    if not isinstance(payload, dict):
        payload = {"value": str(payload)}
    return {
        str(k): (v if isinstance(v, (str, int, float, bool)) else str(v))
        for k, v in payload.items()
    }


def to_html(data: Any) -> str:
    """Render ``data`` to a standalone HTML document string.

    Args:
        data: Data model to render (pydantic model, dict, or object).

    Returns:
        Complete HTML document with embedded CSS.
    """
    payload = _to_payload(data)
    title = str(payload.get("name", "Document"))
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in payload.items()
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<style>{_BASE_CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><table>{rows}</table>"
        "</body></html>"
    )


def html_to_pdf(html: str, path: Path) -> Path:
    """Render a standalone HTML document string to a PDF file.

    WeasyPrint is imported lazily because it loads system libraries
    (pango/cairo) at import time. On Windows, set the
    ``WEASYPRINT_DLL_DIRECTORIES`` env var (see ``.env.example``) to a
    directory with the 64-bit pango/cairo/gdk-pixbuf DLLs.

    Args:
        html: A complete HTML document string (with embedded CSS).
        path: Where to write the PDF file.

    Returns:
        *path* (the written PDF file).
    """
    from weasyprint import HTML

    path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(path))
    return path


class PdfGenerator(FileGenerator):
    """Render data models to ``.pdf`` files via WeasyPrint."""

    extension = "pdf"

    def generate(self, data: Any, output_dir: Path) -> Path:
        """Render ``data`` to a PDF document.

        Pipeline: :func:`to_html` -> :func:`html_to_pdf` (WeasyPrint).

        Args:
            data: Data model to render.
            output_dir: Directory to write the file into.

        Returns:
            Path to the written PDF file.
        """
        path = make_output_path(output_dir, data_stem(data), self.extension)
        return html_to_pdf(to_html(data), path)
