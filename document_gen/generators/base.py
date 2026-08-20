"""Base class and shared helpers for file generator modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


def make_output_path(output_dir: Path, stem: str, extension: str) -> Path:
    """Build an output file path inside ``output_dir``.

    Args:
        output_dir: Directory to write into (created if missing).
        stem: File name without extension (e.g. company name slug).
        extension: File extension without the leading dot (e.g. ``"xlsx"``).

    Returns:
        Path to the output file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{stem}.{extension}"


def data_stem(data: Any) -> str:
    """Derive a file-name stem from ``data``.

    Uses a ``name`` attribute when present (e.g. company name),
    falling back to ``"document"``.

    Args:
        data: Data model to derive the stem from.

    Returns:
        A string safe to use as a file-name stem.
    """
    name = data.get("name") if isinstance(data, dict) else getattr(data, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().replace(" ", "_")
    return "document"


class FileGenerator(ABC):
    """Abstract base for rendering data models to a single file format.

    Subclasses set :attr:`extension` and implement :meth:`generate`.
    """

    extension: ClassVar[str]

    @abstractmethod
    def generate(self, data: Any, output_dir: Path) -> Path:
        """Render ``data`` to a file in ``output_dir``.

        Args:
            data: Data model to render (e.g. ``Company`` or ``ExcelDoc``).
            output_dir: Directory to write the file into.

        Returns:
            Path to the written file.
        """
