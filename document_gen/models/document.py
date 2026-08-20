"""Models for PDF document generation planning."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class DocumentPlan(BaseModel):
    """LLM-determined plan for one document generation run.

    Decides whether the document needs a table of contents and provides a
    design brief (palette, typography, layout style) so each document gets
    a distinctive visual identity instead of one shared template look.
    """

    include_toc: bool = Field(
        description=(
            "Whether the markdown draft should start with a table of "
            "contents (long multi-section documents yes, short/quick "
            "documents no)."
        )
    )
    toc_reason: str = Field(
        description="One short sentence explaining the TOC decision."
    )
    design_direction: str = Field(
        description=(
            "1-2 sentence description of the visual identity for this "
            "document (mood, header treatment, overall feel)."
        )
    )
    palette: list[str] = Field(
        description="3-5 hex colors (e.g. '#1F3A5F') for the document."
    )
    typography: str = Field(
        description=(
            "Font pairing, e.g. 'serif headings, sans-serif body' "
            "(Web-safe / system font families only)."
        )
    )
    layout_style: str = Field(
        description=(
            "Short label for the layout, e.g. 'corporate', "
            "'modern minimal', 'editorial'."
        )
    )

    @field_validator("palette")
    @classmethod
    def _validate_palette(cls, value: list[str]) -> list[str]:
        """Ensure the palette is non-empty and holds hex color strings."""
        if not value:
            raise ValueError("palette must contain 3-5 hex colors")
        hex_digits = set("0123456789abcdefABCDEF")
        for color in value:
            if not color.startswith("#") or not (
                len(color) in (4, 5, 7, 9) and set(color[1:]) <= hex_digits
            ):
                raise ValueError(f"not a hex color: {color!r}")
        return value
