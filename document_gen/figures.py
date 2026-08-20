"""Figure extraction, matplotlib rendering, and HTML embedding for reports.

After the stage-1 markdown draft is generated, figures are handled in
three steps:

1. :func:`extract_figure_specs` — deterministic heuristic that parses
   fenced ```` ```chart ````/```` ```plot ````/```` ```graph ```` blocks
   out of the markdown (row/column tables are ignored; they render
   correctly through the HTML+CSS stage).
2. :func:`render_figure_png` — draws a spec with matplotlib (lazy
   import, Agg backend) into in-memory PNG bytes.
3. :func:`embed_figure_placeholders` — replaces the ``{{FIGURE_n}}``
   placeholder tokens the HTML LLM was told to place with
   ``<figure>``/``<img>`` tags carrying base64 data URIs, so the final
   document stays a single standalone file for WeasyPrint. The base64
   payloads never reach the HTML+CSS LLM step.
"""

from __future__ import annotations

import base64
import html as html_module
import io
import logging
import re

from document_gen.models.figures import FIGURE_KINDS, FigureSeries, FigureSpec

logger = logging.getLogger(__name__)

#: Fenced code blocks declaring a figure (```chart / ```plot / ```graph).
_CHART_FENCE = re.compile(
    r"```(?:chart|plot|graph)\b[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE
)

#: ``key: value`` metadata lines inside a figure block.
_META_LINE = re.compile(r"^(type|kind|title|x|x_label|y|y_label|series)\s*:\s*(.+)$")

#: Placeholder tokens the HTML LLM is instructed to place.
_PLACEHOLDER = re.compile(r"\{\{FIGURE_(\d+)\}\}")

#: Longest x-axis category label kept verbatim; longer ones are elided.
_MAX_LABEL_CHARS = 25

#: Pie charts with more slices than this (or with very small slices) move
#: their labels into a legend instead of drawing them around the wheel.
_PIE_MAX_INLINE_SLICES = 7

#: A pie slice smaller than this share of the total forces legend mode.
_PIE_MIN_INLINE_SHARE = 0.05

#: Pie labels longer than this force legend mode (they crowd the wheel).
_PIE_MAX_INLINE_LABEL_CHARS = 14


# ---------------------------------------------------------------------------
# Heuristic markdown extraction
# ---------------------------------------------------------------------------


def _parse_data_row(line: str) -> list[str] | None:
    """Split one data line into stripped cells (CSV or markdown table).

    Markdown table rows (``| a | b |``) are accepted; blank lines and
    separator rows (``|---|---|``) yield ``None``.
    """
    line = line.strip()
    if not line:
        return None
    if line.startswith("|"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            return None
        return cells
    return [cell.strip() for cell in line.split(",")]


def _parse_figure_block(body: str) -> FigureSpec | None:
    """Parse the body of one fenced figure block into a :class:`FigureSpec`.

    Args:
        body: The text between the figure fence and its closing fence.

    Returns:
        The parsed spec, or ``None`` when the block carries no usable data.
    """
    kind = "bar"
    title = ""
    x_label = ""
    y_label = ""
    series_names: list[str] = []
    rows: list[list[str]] = []
    in_data = False

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        meta = _META_LINE.match(line)
        if meta and not in_data:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            if key in ("type", "kind"):
                kind = value.lower()
            elif key == "title":
                title = value
            elif key in ("x", "x_label"):
                x_label = value
            elif key in ("y", "y_label"):
                y_label = value
            elif key == "series":
                series_names = [
                    name.strip() for name in value.split(",") if name.strip()
                ]
            continue

        if line.lower().startswith("data:"):
            in_data = True
            rest = line.split(":", 1)[1].strip()
            if rest:
                rows.append(_parse_data_row(rest) or [])
            continue
        if in_data and line:
            row = _parse_data_row(line)
            if row is None:
                # A markdown separator row marks the line above it as a
                # header: drop the header.
                if rows:
                    rows.pop()
            else:
                rows.append(row)

    rows = [row for row in rows if len(row) >= 2]
    if not rows:
        return None

    labels: list[str] = []
    columns: list[list[str]] = []
    for row in rows:
        labels.append(row[0])
        columns.append(row[1:])
    width = max(len(col) for col in columns)
    if any(len(col) != width for col in columns):
        logger.warning("Figure block with ragged data rows skipped: %r", title)
        return None

    if series_names and len(series_names) == width:
        names = series_names
    elif width == 1:
        names = [y_label or "Value"]
    else:
        names = [f"Series {i}" for i in range(1, width + 1)]

    series = []
    for name, column_index in zip(names, range(width)):
        try:
            values = [float(col[column_index]) for col in columns]
        except ValueError:
            logger.warning("Figure block with non-numeric data skipped: %r", title)
            return None
        series.append({"name": name, "values": values})

    if kind not in FIGURE_KINDS:
        logger.warning("Unknown figure kind %r; defaulting to bar", kind)
        kind = "bar"
    return FigureSpec(
        kind=kind,
        title=title,
        x_label=x_label,
        y_label=y_label,
        labels=labels,
        series=series,
    )


def extract_figure_specs(markdown: str) -> list[FigureSpec]:
    """Extract figure specs from fenced figure blocks in *markdown*.

    Row/column tables (plain markdown tables) are intentionally ignored:
    they are rendered correctly in the HTML+CSS stage. Only explicit
    ```` ```chart ````/```` ```plot ````/```` ```graph ```` fenced blocks
    are treated as figures.

    Args:
        markdown: The stage-1 report markdown draft.

    Returns:
        The drawable figure specs, in document order (may be empty).
    """
    specs: list[FigureSpec] = []
    for match in _CHART_FENCE.finditer(markdown):
        spec = _parse_figure_block(match.group(1))
        if spec is not None and spec.is_drawable:
            specs.append(spec)
    return specs


# ---------------------------------------------------------------------------
# Matplotlib rendering
# ---------------------------------------------------------------------------


# Label / layout intelligence (pure helpers, matplotlib-free where possible)


def _truncate_label(label: str, max_chars: int = _MAX_LABEL_CHARS) -> str:
    """Shorten *label* to *max_chars* with an ellipsis when needed.

    Args:
        label: The raw category label.
        max_chars: Maximum allowed length (ellipsis included).

    Returns:
        The label, unchanged when it already fits.
    """
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 1].rstrip() + "\u2026"


def _figure_size(spec: FigureSpec) -> tuple[float, float]:
    """Pick a figure size (inches) that gives the labels room to breathe.

    Categorical charts widen with the number of data points and with the
    longest label; pies get a fixed near-square canvas.

    Args:
        spec: The figure spec to size for.

    Returns:
        A ``(width, height)`` tuple in inches.
    """
    if spec.kind == "pie":
        width, height = 6.5, 4.2
        if spec.series and _pie_use_legend(spec.labels, spec.series[0].values):
            # Room for the external legend (longest entry + padding).
            longest = max(
                (len(_truncate_label(label, 30)) for label in spec.labels), default=0
            )
            width = min(11.0, width + 0.07 * longest + 0.6)
        return (width, height)
    n = max((len(s.values) for s in spec.series), default=1)
    longest = max((len(label) for label in spec.labels), default=0)
    width = 3.0 + 0.55 * n
    if longest > 12:
        width += 1.0
    width = max(5.0, min(11.0, width))
    height = 4.2 if longest > 12 else 3.5
    return (width, height)


def _pie_use_legend(labels: list[str], values: list[float]) -> bool:
    """Decide whether a pie chart should use a legend instead of wheel labels.

    Legend mode is chosen when there are many slices, any slice is too
    small to carry an inline label, or the labels are long enough to
    crowd the wheel.

    Args:
        labels: Slice labels.
        values: Slice values.

    Returns:
        ``True`` when the labels should move to a legend.
    """
    if len(values) > _PIE_MAX_INLINE_SLICES:
        return True
    total = sum(abs(v) for v in values)
    if total > 0 and min(abs(v) for v in values) / total < _PIE_MIN_INLINE_SHARE:
        return True
    return (
        max((len(label) for label in labels), default=0) > _PIE_MAX_INLINE_LABEL_CHARS
    )


def _compact_float(value: float, _position: object) -> str:
    """Format *value* with k/M suffixes for y-axis tick labels.

    Args:
        value: The tick value.
        _position: Unused matplotlib tick position.

    Returns:
        A compact string (e.g. ``1.2M``, ``340k``).
    """
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:g}k"
    return f"{value:g}"


def _scatter_xy(spec: FigureSpec) -> tuple[FigureSeries, FigureSeries] | None:
    """Return ``(x_series, y_series)`` for a paired two-series scatter.

    A scatter with exactly two series is treated as an x/y pair (e.g.
    "score vs weight") rather than two independent category clouds. When
    the spec's ``x_label`` matches the second series' name, the series
    are swapped so the axes line up with the declared labels.

    Args:
        spec: A scatter figure spec.

    Returns:
        The ``(x, y)`` series pair, or ``None`` for the category mode.
    """
    if spec.kind != "scatter" or len(spec.series) != 2:
        return None
    x_series, y_series = spec.series
    if spec.x_label and y_series.name == spec.x_label:
        x_series, y_series = y_series, x_series
    return x_series, y_series


def _fit_xtick_labels(fig, ax) -> None:
    """Rotate x-tick labels just enough that they stop overlapping.

    Tries rotations 0/30/45/60/90 in order, redrawing after each attempt
    and measuring the rendered label boxes; the first rotation with no
    horizontal overlap wins, so short labels stay horizontal. Labels that
    would still spill past the figure edge (a centered numeric tick at
    the axes edge, which layout engines do not reserve room for) are
    hidden rather than clipped.

    Args:
        fig: The parent figure (must use the Agg backend).
        ax: The axes whose x tick labels are fitted.
    """
    tick_labels = [lbl for lbl in ax.get_xticklabels() if lbl.get_visible()]
    if not tick_labels:
        return
    for rotation, ha in (
        (0, "center"),
        (30, "right"),
        (45, "right"),
        (60, "right"),
        (90, "center"),
    ):
        ax.tick_params(axis="x", rotation=rotation, labelsize=8)
        for lbl in tick_labels:
            lbl.set_ha(ha)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        boxes = [lbl.get_window_extent(renderer=renderer) for lbl in tick_labels]
        if all(b1.x1 <= b2.x0 + 1.0 for b1, b2 in zip(boxes, boxes[1:])):
            break
    fig_box = fig.get_window_extent()
    for lbl, box in zip(tick_labels, boxes):
        if box.x1 > fig_box.x1 + 1.0 or box.x0 < fig_box.x0 - 1.0:
            lbl.set_visible(False)


def _draw(ax, spec: FigureSpec) -> None:
    """Draw *spec* onto a matplotlib *ax* according to its kind."""
    import matplotlib.ticker as ticker

    labels = spec.labels or [str(i) for i in range(1, len(spec.series[0].values) + 1)]
    labels = [_truncate_label(label) for label in labels]
    kind = spec.kind

    if kind == "bar":
        width = 0.8 / len(spec.series)
        for offset, series in enumerate(spec.series):
            positions = [
                i + (offset - (len(spec.series) - 1) / 2) * width
                for i in range(len(labels))
            ]
            ax.bar(positions, series.values, width=width, label=series.name)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
    elif kind == "line":
        for series in spec.series:
            ax.plot(labels, series.values, marker="o", markersize=3, label=series.name)
    elif kind == "pie":
        values = spec.series[0].values
        use_legend = _pie_use_legend(labels, values)
        pie_result = ax.pie(
            values,
            labels=None if use_legend else labels[: len(values)],
            autopct=None if use_legend else "%1.1f%%",
            pctdistance=0.75,
            labeldistance=1.08,
        )
        wedges = pie_result[0]
        if use_legend:
            total = sum(values) or 1
            ax.legend(
                wedges,
                [
                    f"{_truncate_label(label, 30)} ({value / total:.1%})"
                    for label, value in zip(labels, values)
                ],
                loc="center left",
                bbox_to_anchor=(1.0, 0.5),
                fontsize=8,
            )
    elif kind == "scatter":
        pair = _scatter_xy(spec)
        if pair is not None:
            x_series, y_series = pair
            ax.scatter(x_series.values, y_series.values)
            ax.set_xlabel(spec.x_label or x_series.name)
            ax.set_ylabel(spec.y_label or y_series.name)
            for x_value, y_value, label in zip(
                x_series.values, y_series.values, labels
            ):
                ax.annotate(
                    _truncate_label(label, 18),
                    (x_value, y_value),
                    textcoords="offset points",
                    xytext=(4, 4),
                    fontsize=7,
                )
        else:
            for series in spec.series:
                ax.scatter(labels, series.values, label=series.name)
    elif kind == "area":
        for series in spec.series:
            (line,) = ax.plot(labels, series.values, linewidth=1, label=series.name)
            ax.fill_between(labels, series.values, alpha=0.3, color=line.get_color())
    elif kind == "histogram":
        for series in spec.series:
            ax.hist(
                series.values,
                bins=min(10, max(4, len(series.values) // 2)),
                label=series.name,
            )
    else:  # pragma: no cover - kind is validated by the model
        raise ValueError(f"Unsupported figure kind: {kind}")

    if kind != "scatter" or _scatter_xy(spec) is None:
        if spec.x_label:
            ax.set_xlabel(spec.x_label)
        if spec.y_label:
            ax.set_ylabel(spec.y_label)

    all_values = [v for s in spec.series for v in s.values]
    if all_values and max(abs(v) for v in all_values) >= 10_000:
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(_compact_float))

    show_legend = len(spec.series) > 1 and kind != "pie"
    if kind == "scatter" and _scatter_xy(spec) is not None:
        show_legend = False
    if show_legend:
        ax.legend(fontsize=7, loc="best")


def render_figure_png(spec: FigureSpec) -> bytes:
    """Render *spec* to PNG bytes with matplotlib.

    matplotlib is imported lazily (heavy dependency) and the Agg
    backend is forced so no display is required. The figure is sized to
    the data, x-tick labels are rotated adaptively so they never
    overlap, and constrained layout keeps everything in bounds.

    Args:
        spec: A drawable figure spec.

    Returns:
        The PNG image bytes.

    Raises:
        ValueError: When *spec* is not drawable.
    """
    if not spec.is_drawable:
        raise ValueError(f"Figure spec is not drawable: {spec.title!r}")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=_figure_size(spec), dpi=150, constrained_layout=True)
    _draw(ax, spec)
    if spec.title:
        fig.suptitle(spec.title, fontsize=11)
    _fit_xtick_labels(fig, ax)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# HTML embedding (base64 data URIs, injected after the LLM HTML step)
# ---------------------------------------------------------------------------


def figure_placeholder(index: int) -> str:
    """Return the placeholder token for the *index*-th figure (1-based)."""
    return "{{FIGURE_%d}}" % index


def _figure_html(index: int, spec: FigureSpec) -> str:
    """Build the ``<figure>`` tag with an embedded base64 PNG for *spec*."""
    png = render_figure_png(spec)
    payload = base64.b64encode(png).decode("ascii")
    caption = html_module.escape(spec.title)
    caption = f"Figure {index}: {caption}" if caption else f"Figure {index}"
    return (
        f'<figure class="report-figure" style="margin: 1em 0; page-break-inside: avoid;">'
        f'<img src="data:image/png;base64,{payload}" alt="{caption}" '
        f'style="max-width: 100%; height: auto;" />'
        f'<figcaption style="font-size: 9pt; text-align: center; margin-top: 0.3em;">'
        f"{caption}</figcaption></figure>"
    )


def embed_figure_placeholders(html: str, specs: list[FigureSpec]) -> str:
    """Replace ``{{FIGURE_n}}`` placeholders in *html* with rendered figures.

    Placeholders are matched by 1-based position in *specs*. Placeholders
    the LLM never emitted are appended before ``</body>`` so no figure is
    lost; stray placeholders without a matching spec are removed.

    Args:
        html: The sanitized HTML document (placeholders, no images yet).
        specs: The figure specs, in the same order the placeholders were
            announced to the LLM.

    Returns:
        The HTML document with figures embedded as base64 data URIs.
    """
    if not specs:
        return _PLACEHOLDER.sub("", html)

    used: set[int] = set()
    for index, spec in enumerate(specs, start=1):
        token = figure_placeholder(index)
        if token in html:
            html = html.replace(token, _figure_html(index, spec))
            used.add(index)

    html = _PLACEHOLDER.sub("", html)

    missing = [i for i in range(1, len(specs) + 1) if i not in used]
    if missing:
        extra = "".join(_figure_html(i, specs[i - 1]) for i in missing)
        if "</body>" in html:
            html = html.replace("</body>", f"{extra}</body>", 1)
        else:
            html = html + extra
    return html
