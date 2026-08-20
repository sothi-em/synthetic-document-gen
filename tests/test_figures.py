"""Tests for figure extraction, matplotlib rendering, and HTML embedding."""

from __future__ import annotations

import pytest

from document_gen import figures
from document_gen.models import FIGURE_KINDS, FigureSpec


def _spec(**overrides) -> FigureSpec:
    """Build a drawable spec, applying *overrides*."""
    base = dict(
        kind="bar",
        title="Revenue by segment",
        labels=["A", "B"],
        series=[{"name": "Value", "values": [1.0, 2.0]}],
    )
    base.update(overrides)
    return FigureSpec(**base)


# ---------------------------------------------------------------------------
# Heuristic markdown extraction
# ---------------------------------------------------------------------------


class TestExtractFigureSpecs:
    def test_no_figure_blocks(self) -> None:
        # Row/column tables render via HTML+CSS; only fenced blocks count.
        assert (
            figures.extract_figure_specs("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
            == []
        )
        assert (
            figures.extract_figure_specs(
                "## Results\n\n| 2021 | 2022 |\n|---|---|\n| 100 | 120 |\n"
            )
            == []
        )

    def test_single_series_bar_block(self) -> None:
        markdown = (
            "## Results\n\n| Segment | Revenue |\n|---|---|\n| A | 1 |\n\n"
            "```chart\n"
            "type: bar\n"
            "title: Revenue by segment\n"
            "data:\n"
            "A, 1\n"
            "B, 2\n"
            "```\n"
        )
        specs = figures.extract_figure_specs(markdown)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.kind == "bar"
        assert spec.title == "Revenue by segment"
        assert spec.labels == ["A", "B"]
        assert [s.name for s in spec.series] == ["Value"]
        assert spec.series[0].values == [1.0, 2.0]
        assert spec.is_drawable

    def test_multi_series_with_series_names(self) -> None:
        markdown = (
            "```graph\n"
            "type: line\n"
            "title: Revenue trend\n"
            "x: Year\n"
            "y: USD m\n"
            "series: Technology, Healthcare\n"
            "data:\n"
            "2021, 100, 50\n"
            "2022, 120, 60\n"
            "```\n"
        )
        specs = figures.extract_figure_specs(markdown)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.kind == "line"
        assert spec.x_label == "Year"
        assert spec.y_label == "USD m"
        assert [s.name for s in spec.series] == ["Technology", "Healthcare"]
        assert spec.series[1].values == [50.0, 60.0]

    def test_multiple_blocks_in_document_order(self) -> None:
        markdown = (
            "```chart\ntype: pie\ntitle: Mix\ndata:\nA, 3\nB, 7\n```\n"
            "prose\n"
            "```plot\ntype: scatter\ntitle: Spread\ndata:\n1, 2\n3, 4\n```\n"
        )
        specs = figures.extract_figure_specs(markdown)
        assert [s.kind for s in specs] == ["pie", "scatter"]
        assert [s.title for s in specs] == ["Mix", "Spread"]

    def test_markdown_table_rows_as_data(self) -> None:
        markdown = (
            "```chart\ntype: bar\ntitle: T\ndata:\n"
            "| Segment | Revenue |\n|---|---|\n| A | 1 |\n| B | 2 |\n```\n"
        )
        specs = figures.extract_figure_specs(markdown)
        assert len(specs) == 1
        assert specs[0].labels == ["A", "B"]
        assert specs[0].series[0].values == [1.0, 2.0]

    def test_unknown_kind_defaults_to_bar(self) -> None:
        markdown = "```chart\ntype: radar\ntitle: T\ndata:\nA, 1\n```\n"
        specs = figures.extract_figure_specs(markdown)
        assert len(specs) == 1
        assert specs[0].kind == "bar"

    @pytest.mark.parametrize(
        "data",
        ["A, n/a", "A, 1\nB, 2, 3", ""],  # non-numeric, ragged, empty
    )
    def test_invalid_data_skipped(self, data: str) -> None:
        markdown = f"```chart\ntype: bar\ntitle: T\ndata:\n{data}\n```\n"
        assert figures.extract_figure_specs(markdown) == []


# ---------------------------------------------------------------------------
# Matplotlib rendering
# ---------------------------------------------------------------------------


class TestRenderFigurePng:
    @pytest.mark.parametrize("kind", list(FIGURE_KINDS))
    def test_png_magic_for_each_kind(self, kind: str) -> None:
        spec = _spec(kind=kind)
        if kind == "pie":
            spec = _spec(
                kind="pie", labels=["A", "B"], series=[{"name": "V", "values": [3, 7]}]
            )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 1000

    def test_multi_series_bar(self) -> None:
        spec = _spec(
            series=[
                {"name": "2021", "values": [1.0, 2.0]},
                {"name": "2022", "values": [3.0, 4.0]},
            ]
        )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_not_drawable_raises(self) -> None:
        with pytest.raises(ValueError, match="not drawable"):
            figures.render_figure_png(_spec(series=[]))

    def test_many_long_labels_line(self) -> None:
        # The Bean Smith regression: 12 long labels must render without
        # raising (rotation is fitted adaptively).
        labels = [
            "Sleep Quality",
            "Pain Level",
            "Stress/Anxiety",
            "Mobility",
            "Nutrition",
            "Hydration",
            "Exercise",
            "Mental Clarity",
            "Social Connection",
            "Env Sensitivity",
            "Med Adherence",
            "Recovery Readiness",
        ]
        spec = _spec(
            kind="line",
            labels=labels,
            series=[{"name": "Score", "values": [float(i) for i in range(1, 13)]}],
        )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_pie_with_tiny_slice(self) -> None:
        spec = _spec(
            kind="pie",
            labels=["Big", "Small", "Tiny"],
            series=[{"name": "V", "values": [90.0, 8.0, 1.0]}],
        )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_paired_scatter(self) -> None:
        spec = _spec(
            kind="scatter",
            x_label="Weight",
            y_label="Score",
            labels=["Physical", "Mental/Emotional", "Nutritional"],
            series=[
                {"name": "Score", "values": [3.0, 2.5, 3.5]},
                {"name": "Weight", "values": [0.3, 0.25, 0.15]},
            ],
        )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_large_values_compact_y_axis(self) -> None:
        spec = _spec(
            labels=["A", "B"],
            series=[{"name": "Revenue", "values": [1_200_000.0, 340_000.0]}],
        )
        png = figures.render_figure_png(spec)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Label / layout intelligence helpers
# ---------------------------------------------------------------------------


class TestLabelIntelligence:
    def test_truncate_label_short_unchanged(self) -> None:
        assert figures._truncate_label("Nutrition") == "Nutrition"

    def test_truncate_label_long_elided(self) -> None:
        label = figures._truncate_label("Mental and Emotional Wellbeing Score")
        assert len(label) <= 25
        assert label.endswith("\u2026")

    def test_figure_size_grows_with_points(self) -> None:
        small = figures._figure_size(_spec())
        wide = figures._figure_size(
            _spec(
                labels=[f"Label number {i}" for i in range(12)],
                series=[{"name": "V", "values": [1.0] * 12}],
            )
        )
        assert wide[0] > small[0]
        assert wide[0] <= 11.0

    def test_figure_size_pie_fixed(self) -> None:
        assert figures._figure_size(_spec(kind="pie")) == (6.5, 4.2)

    def test_pie_legend_many_slices(self) -> None:
        assert figures._pie_use_legend([f"S{i}" for i in range(8)], [1.0] * 8)

    def test_pie_legend_tiny_slice(self) -> None:
        assert figures._pie_use_legend(["A", "B"], [99.0, 0.5])

    def test_pie_legend_long_labels(self) -> None:
        assert figures._pie_use_legend(["Environmental/Sleep", "Social"], [5.0, 5.0])

    def test_pie_inline_when_comfortable(self) -> None:
        assert not figures._pie_use_legend(["A", "B"], [3.0, 7.0])

    def test_scatter_xy_pair(self) -> None:
        spec = _spec(
            kind="scatter",
            x_label="Weight",
            series=[
                {"name": "Score", "values": [1.0, 2.0]},
                {"name": "Weight", "values": [0.3, 0.2]},
            ],
        )
        x, y = figures._scatter_xy(spec)
        # x_label matches the second series, so it becomes the x axis.
        assert x.name == "Weight"
        assert y.name == "Score"

    def test_scatter_xy_default_order(self) -> None:
        spec = _spec(
            kind="scatter",
            series=[
                {"name": "X", "values": [1.0, 2.0]},
                {"name": "Y", "values": [3.0, 4.0]},
            ],
        )
        x, y = figures._scatter_xy(spec)
        assert x.name == "X"
        assert y.name == "Y"

    def test_scatter_xy_single_series_none(self) -> None:
        assert figures._scatter_xy(_spec(kind="scatter")) is None

    def test_scatter_xy_non_scatter_none(self) -> None:
        spec = _spec(
            series=[
                {"name": "A", "values": [1.0]},
                {"name": "B", "values": [2.0]},
            ]
        )
        assert figures._scatter_xy(spec) is None

    def test_compact_float(self) -> None:
        assert figures._compact_float(1_200_000, None) == "1.2M"
        assert figures._compact_float(340_000, None) == "340k"
        assert figures._compact_float(999, None) == "999"


# ---------------------------------------------------------------------------
# HTML embedding
# ---------------------------------------------------------------------------


class TestEmbedFigurePlaceholders:
    def test_replaces_placeholder(self) -> None:
        assert figures.figure_placeholder(3) == "{{FIGURE_3}}"
        html = "<html><body><p>{{FIGURE_1}}</p></body></html>"
        result = figures.embed_figure_placeholders(html, [_spec()])
        assert "{{FIGURE" not in result
        assert "data:image/png;base64," in result
        assert "Figure 1: Revenue by segment" in result
        assert "page-break-inside: avoid" in result

    def test_unused_figure_appended_before_body_end(self) -> None:
        html = "<html><body><p>no placeholders</p></body></html>"
        result = figures.embed_figure_placeholders(html, [_spec()])
        assert "data:image/png;base64," in result
        assert result.index("data:image/png") < result.index("</body>")

    def test_stray_placeholder_removed(self) -> None:
        html = "<html><body>{{FIGURE_2}}</body></html>"
        result = figures.embed_figure_placeholders(html, [_spec()])
        assert "{{FIGURE" not in result
        # Figure 1 (unused) is appended; stray FIGURE_2 removed.
        assert "Figure 1" in result

    def test_empty_specs_strip_placeholders(self) -> None:
        html = "<html><body>{{FIGURE_1}}</body></html>"
        assert (
            figures.embed_figure_placeholders(html, []) == "<html><body></body></html>"
        )

    def test_multiple_figures_in_order(self) -> None:
        html = "<html><body>{{FIGURE_1}} {{FIGURE_2}}</body></html>"
        specs = [_spec(title="First"), _spec(title="Second")]
        result = figures.embed_figure_placeholders(html, specs)
        assert result.index("Figure 1: First") < result.index("Figure 2: Second")
