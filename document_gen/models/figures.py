"""Models for report figure extraction and matplotlib rendering.

A :class:`FigureSpec` describes one chart/plot/graph that should be
rendered from the sample data tables of a generated report. Specs are
produced either by the deterministic markdown heuristic
(:func:`document_gen.figures.extract_figure_specs`) or by the LLM
fallback extraction (validated into :class:`FigureExtraction`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Kinds of figure the renderer supports.
FigureKind = Literal["bar", "line", "area", "pie", "scatter", "histogram"]

#: All supported figure kinds, in canonical order.
FIGURE_KINDS: tuple[str, ...] = (
    "bar",
    "line",
    "area",
    "pie",
    "scatter",
    "histogram",
)


class FigureSeries(BaseModel):
    """One numeric series of a figure (e.g. one column of a data table)."""

    name: str = "Value"
    values: list[float] = Field(default_factory=list)


class FigureSpec(BaseModel):
    """Declarative description of a single figure to render.

    Attributes:
        kind: The chart type to draw.
        title: Short caption (e.g. "Units sold by region, 2021-2024").
        x_label: Optional x-axis label.
        y_label: Optional y-axis label.
        labels: X-axis category labels (one per data point).
        series: One or more numeric series aligned with *labels*.
    """

    kind: FigureKind = "bar"
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    labels: list[str] = Field(default_factory=list)
    series: list[FigureSeries] = Field(default_factory=list)

    @property
    def is_drawable(self) -> bool:
        """Whether this spec carries enough data to render.

        Returns:
            ``True`` when every series has at least one value.
        """
        return bool(self.series) and all(s.values for s in self.series)


class FigureExtraction(BaseModel):
    """Structured LLM output: the figures found in a report markdown."""

    figures: list[FigureSpec] = Field(default_factory=list)
