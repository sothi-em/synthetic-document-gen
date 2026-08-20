"""CSV file generator using the stdlib csv module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import FileGenerator


class CsvGenerator(FileGenerator):
    """Render data models to ``.csv`` files via the stdlib ``csv`` module."""

    extension = "csv"

    def generate(self, data: Any, output_dir: Path) -> Path:
        """Render ``data`` to a CSV file.

        Planned mapping (next stage):
        - One file per table (or first table); header row from
          ``Column.headers``/column names, one row per record.
        - ``csv.writer`` with ``newline=""`` per the docs.
        """
        raise NotImplementedError("CsvGenerator.generate: content mapping TBD")
