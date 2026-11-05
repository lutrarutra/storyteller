"""Native PowerPoint charts: the user can edit their data in PowerPoint (Edit Data), restyle
them and change the numbers. Their colours are the theme's accents, and their text and lines
the theme's text colour, so a user's template and dark slides restyle them.

`prepare` turns what the analysis has (a DataFrame, a Series, a dict of columns, a list of
rows) into the chart's categories and series, as stored in the deck's document; `add` draws
the stored chart on a slide.
"""

from __future__ import annotations

import numbers
from collections.abc import Sequence
from typing import Any

from .. import tables
from . import colors

KINDS = ("bar", "barh", "line", "area", "pie", "scatter")
MAX_SERIES = 6  # the theme has six accents
MAX_CATEGORIES = 40
MAX_POINTS = 2000
MARKERS_UP_TO = 15  # a line with this many points or fewer shows its points


def prepare(
    data: Any,
    kind: str,
    *,
    x: str | None = None,
    y: str | Sequence[str] | None = None,
    by: str | None = None,
) -> dict[str, Any]:
    """The chart's ``categories`` and ``series`` (``name``, ``values``; for scatter ``x``, ``y``)."""
    if kind not in KINDS:
        raise ValueError(f"unknown chart kind {kind!r}: use one of {', '.join(KINDS)}")
    columns, rows, labels = tables._columns_and_rows(data, None)
    if not rows:
        raise ValueError("the chart has no data")

    def column(name: str) -> list[Any]:
        if name not in columns:
            raise KeyError(f"no column {name!r}: the columns are {', '.join(map(repr, columns))}")
        i = columns.index(name)
        return [row[i] for row in rows]

    numeric = [c for c in columns if _is_numeric(column(c))]
    wanted = [y] if isinstance(y, str) else list(y) if y is not None else None
    if by is not None and wanted is not None and len(wanted) != 1:
        raise ValueError("with by=, give one y column: by= makes a series of each of its values")

    if kind == "scatter":
        x = x or next((c for c in numeric if c != by), None)
        if x is None:
            raise ValueError("a scatter chart needs a numeric x column")
        ys = wanted or [c for c in numeric if c not in (x, by)][: 1 if by else MAX_SERIES]
        if not ys:
            raise ValueError("a scatter chart needs a numeric y column besides x")
        xs = _numbers(column(x), x)
        if by is not None:
            values = _numbers(column(ys[0]), ys[0])
            groups: dict[str, tuple[list[Any], list[Any]]] = {}
            for group, a, b in zip(column(by), xs, values, strict=True):
                gx, gy = groups.setdefault(tables.format_value(group), ([], []))
                gx.append(a)
                gy.append(b)
            series = [{"name": name, "x": gx, "y": gy} for name, (gx, gy) in groups.items()]
        else:
            series = [{"name": name, "x": xs, "y": _numbers(column(name), name)} for name in ys]
        chart: dict[str, Any] = {"categories": [], "series": series}
        points = sum(len(s["x"]) for s in series)
        if points > MAX_POINTS:
            raise ValueError(
                f"a native scatter chart takes at most {MAX_POINTS} points, this one has {points}: "
                "draw it with matplotlib and deck.figure() (rasterized=True keeps its axes editable)"
            )
    else:
        if x is not None:
            categories = column(x)
        elif labels is not None:
            categories = labels
        else:
            # The first text column, else the first column when more follow (x before ys).
            x = next((c for c in columns if c not in numeric and c != by), None)
            if x is None and len([c for c in numeric if c != by]) > 1:
                x = columns[0]
            categories = column(x) if x else list(range(1, len(rows) + 1))
        names = [tables.format_value(c) for c in categories]
        if by is not None:
            name = wanted[0] if wanted else next((c for c in numeric if c not in (x, by)), None)
            if name is None:
                raise ValueError("by= needs a numeric column of values (y=)")
            order = list(dict.fromkeys(names))
            cells: dict[str, dict[str, Any]] = {}
            for category, group, value in zip(names, column(by), _numbers(column(name), name), strict=True):
                row = cells.setdefault(tables.format_value(group), {})
                if category in row:
                    raise ValueError(f"{category!r} appears twice in group {group!r}: sum or average the rows first")
                row[category] = value
            names = order
            series = [{"name": group, "values": [row.get(c) for c in order]} for group, row in cells.items()]
        else:
            ys = wanted or [c for c in numeric if c != x]
            if not ys:
                raise ValueError("the chart needs a numeric column of values (y=)")
            series = [{"name": name, "values": _numbers(column(name), name)} for name in ys]
        chart = {"categories": names, "series": series}
        if len(names) > MAX_CATEGORIES:
            raise ValueError(
                f"a slide chart shows at most {MAX_CATEGORIES} categories, this one has {len(names)}: "
                "show the ones that make the point (the top 10), or draw it with matplotlib"
            )
        if kind == "pie" and len(series) != 1:
            raise ValueError("a pie chart shows one series: pass y= the column of values")
    if len(chart["series"]) > MAX_SERIES:
        raise ValueError(
            f"a chart shows at most {MAX_SERIES} series (the theme's six colours), this one has "
            f"{len(chart['series'])}: fold the smallest into 'Other' or make several charts"
        )
    return chart


def _is_numeric(values: list[Any]) -> bool:
    present = [v for v in values if not tables._is_missing(v)]
    return bool(present) and all(_is_number(v) for v in present)


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _numbers(values: list[Any], name: str) -> list[float | None]:
    out: list[float | None] = []
    for value in values:
        if tables._is_missing(value):
            out.append(None)
        elif _is_number(value):
            out.append(float(value))
        else:
            raise TypeError(f"column {name!r} holds {value!r}, not a number")
    return out


# -- drawing -----------------------------------------------------------------------------------------


def add(slide, block: dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    """Draws the chart `block` (see `prepare`) in the given box (EMU)."""
    from pptx.chart.data import CategoryChartData, XyChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
    from pptx.util import Pt

    kind = block["kind"]
    stacked = block.get("stacked", False)
    series = block["series"]
    if kind == "scatter":
        data = XyChartData()
        kept = [
            [(a, b) for a, b in zip(item["x"], item["y"], strict=True) if a is not None and b is not None]
            for item in series
        ]
        longest = max(len(pairs) for pairs in kept)
        for item, pairs in zip(series, kept, strict=True):
            points = data.add_series(item["name"], number_format=block.get("number_format"))
            # Series of one length: LibreOffice leaves out a series shorter than the next.
            for a, b in pairs + [(None, None)] * (longest - len(pairs)):
                points.add_data_point(a, b)
        chart_type = XL_CHART_TYPE.XY_SCATTER
    else:
        data = CategoryChartData(number_format=block.get("number_format") or "General")
        # PowerPoint draws bars from the bottom up: reversed, the first category is on top, as it reads.
        turn = (lambda values: values[::-1]) if kind == "barh" else list
        data.categories = turn(block["categories"])
        for item in series:
            data.add_series(item["name"], turn(item["values"]))
        few = len(block["categories"]) <= MARKERS_UP_TO
        chart_type = {
            "bar": XL_CHART_TYPE.COLUMN_STACKED if stacked else XL_CHART_TYPE.COLUMN_CLUSTERED,
            "barh": XL_CHART_TYPE.BAR_STACKED if stacked else XL_CHART_TYPE.BAR_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS if few else XL_CHART_TYPE.LINE,
            "area": XL_CHART_TYPE.AREA_STACKED if stacked else XL_CHART_TYPE.AREA,
            "pie": XL_CHART_TYPE.PIE,
        }[kind]
    chart = slide.shapes.add_chart(chart_type, x, y, w, h, data).chart
    chart.font.size = Pt(14)
    colors.font(chart.font, "tx1")
    chart.has_title = False
    chart.has_legend = kind != "pie" and len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    plot = chart.plots[0]
    if kind in ("bar", "barh"):
        plot.gap_width = 60
        if stacked:
            plot.overlap = 100
    if kind == "pie":
        for index, point in enumerate(plot.series[0].points):
            colors.fill(point.format.fill, _accent(index))
            colors.line(point.format.line, "bg1", width=Pt(1.5))
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.show_category_name, labels.show_percentage, labels.show_value = True, True, False
        labels.number_format, labels.number_format_is_linked = "0%", False
        labels.position = XL_LABEL_POSITION.OUTSIDE_END
        return
    for index, item in enumerate(plot.series):
        accent = _accent(index)
        if kind in ("line", "scatter"):
            if kind == "line":
                colors.line(item.format.line, accent, width=Pt(2.5))
                item.smooth = False
            else:
                item.format.line.fill.background()
            _marker(item, accent, kind == "scatter" or chart_type == XL_CHART_TYPE.LINE_MARKERS)
        else:
            colors.fill(item.format.fill, accent)
            if kind in ("bar", "barh"):
                # Left out, it means yes: PowerPoint leaves negative bars unfilled, LibreOffice draws them upwards.
                item.invert_if_negative = False
    if block.get("labels"):
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.font.size = Pt(12)
        if stacked:  # inside the bars: the background's colour reads on every accent, light or dark
            colors.font(labels.font, "bg1")
        if block.get("number_format"):
            labels.number_format = block["number_format"]
            labels.number_format_is_linked = False
    _axes(chart, block, kind)


def _accent(index: int) -> str:
    return f"accent{index % MAX_SERIES + 1}"


def _marker(series, accent: str, shown: bool) -> None:
    from pptx.enum.chart import XL_MARKER_STYLE

    marker = series.marker
    if not shown:
        marker.style = XL_MARKER_STYLE.NONE
        return
    marker.style = XL_MARKER_STYLE.CIRCLE
    marker.size = 7
    colors.fill(marker.format.fill, accent)
    marker.format.line.fill.background()


def _axes(chart, block: dict[str, Any], kind: str) -> None:
    """Quiet axes: the values' axis has faint gridlines and no line, the categories' a line."""
    from pptx.enum.chart import XL_TICK_LABEL_POSITION, XL_TICK_MARK
    from pptx.util import Pt

    categories, values = chart.category_axis, chart.value_axis
    values.has_major_gridlines = True
    colors.line(values.major_gridlines.format.line, "tx1", alpha=0.15, width=Pt(0.75))
    values.format.line.fill.background()
    values.major_tick_mark = XL_TICK_MARK.NONE
    colors.line(categories.format.line, "tx1", alpha=0.45, width=Pt(1))
    categories.major_tick_mark = XL_TICK_MARK.NONE
    # Labels at the plot's edge, not where the axes cross inside it (at 0, with negative values).
    values.tick_label_position = categories.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    if kind == "scatter":
        categories.has_major_gridlines = True
        colors.line(categories.major_gridlines.format.line, "tx1", alpha=0.15, width=Pt(0.75))
    if block.get("number_format"):
        values.tick_labels.number_format = block["number_format"]
        values.tick_labels.number_format_is_linked = False
    for axis, label in ((categories, block.get("x_label")), (values, block.get("y_label"))):
        if label:
            axis.has_title = True
            frame = axis.axis_title.text_frame
            frame.text = label
            run = frame.paragraphs[0].runs[0]
            run.font.size = Pt(13)
            run.font.bold = False
            colors.font(run.font, "tx1", alpha=0.75)
