"""File generator modules: docx, pdf, xlsx, csv.

Usage:
    from document_gen.generators import generate_file, GENERATORS

    path = generate_file(data, output_dir, "xlsx")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import FileGenerator, data_stem, make_output_path
from .csv_gen import CsvGenerator
from .docx_gen import DocxGenerator
from .excel_gen import ExcelGenerator, fill_table_values, render_excel_doc
from .pdf_gen import PdfGenerator, html_to_pdf

GENERATORS: dict[str, type[FileGenerator]] = {
    cls.extension: cls
    for cls in (DocxGenerator, PdfGenerator, ExcelGenerator, CsvGenerator)
}


def generate_file(data: Any, output_dir: Path, fmt: str) -> Path:
    """Render ``data`` with the generator registered for ``fmt``.

    Args:
        data: Data model to render (e.g. ``Company`` or ``ExcelDoc``).
        output_dir: Directory to write the file into.
        fmt: File extension without dot (e.g. ``"docx"``, ``"pdf"``).

    Returns:
        Path to the written file.

    Raises:
        ValueError: If ``fmt`` has no registered generator.
    """
    try:
        generator_cls = GENERATORS[fmt]
    except KeyError:
        supported = ", ".join(sorted(GENERATORS))
        raise ValueError(f"Unknown format {fmt!r}. Supported: {supported}") from None
    return generator_cls().generate(data, output_dir)


__all__ = [
    "CsvGenerator",
    "DocxGenerator",
    "ExcelGenerator",
    "FileGenerator",
    "GENERATORS",
    "data_stem",
    "fill_table_values",
    "PdfGenerator",
    "render_excel_doc",
    "generate_file",
    "html_to_pdf",
    "make_output_path",
]
