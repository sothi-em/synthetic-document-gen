"""DOCX (Word) file generator using python-docx."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import FileGenerator


class DocxGenerator(FileGenerator):
    """Render data models to ``.docx`` files via ``docx.Document``."""

    extension = "docx"

    def generate(self, data: Any, output_dir: Path) -> Path:
        """Render ``data`` to a Word document.

        Planned mapping (next stage):
        - Company name -> title paragraph.
        - Report sections -> headings + paragraphs.
        - Tables -> ``doc.add_table`` with styled headers.
        """
        raise NotImplementedError("DocxGenerator.generate: content mapping TBD")
