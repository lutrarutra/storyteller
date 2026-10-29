"""A matplotlib snapshot of an explorer's charts at their initial settings.

It is what readers see where scripts cannot run (VS Code's sanitised preview, mail
clients, some archives) and what the runtime replaces with the live charts.
"""

from __future__ import annotations

import math
from typing import Any

from ..theme import palette
from .explorer import Bar, Box, Control, Histogram, Scatter, Threshold, View

CHART_VIEWS = (Histogram, Scatter, Bar, Box)


def render(columns: dict[str, Any], controls: list[Control], views: list[View], subset: Any, passes: Any, mode: str):
    """A matplotlib Figure with one panel per chart view, or None when there is no chart view."""
    import matplotlib.pyplot as plt
    import numpy as np

    from ..theme import mpl as theme_mpl

    charts = [view for view in views if isinstance(view, CHART_VIEWS)]
    if not charts:
        return None
    ncols = 1 if len(charts) == 1 else 2
    nrows = math.ceil(len(charts) / ncols)
    with plt.rc_context(theme_mpl.rc_params("light")):
        figure, axes = plt.subplots(
            nrows, ncols, figsize=(3.6 * ncols if ncols > 1 else 7.0, 2.9 * nrows), squeeze=False
        )
        for ax in axes.flat[len(charts) :]:
            ax.set_visible(False)
        shown = subset & passes if mode == "filter" else subset
        for ax, view in zip(axes.flat, charts, strict=False):
            _draw(ax, view, columns, controls, shown, subset & passes, np)
    return figure


PASS = palette.CATEGORICAL["light"][0]
FAIL = palette.NEUTRAL["light"]


def _thresholds(controls: list[Control], column: str) -> list[float]:
    return [
        bound
        for control in controls
        if isinstance(control, Threshold) and control.column == column
        for bound in (control.min, control.max)
        if bound is not None
    ]


def _label(controls: list[Control], column: str) -> str:
    return next((c.label for c in controls if c.column == column and c.label), column)


def _plain_log_ticks(ax) -> None:
    """Log axes label only the decades (minor labels like 2×10³ collide in small panels)."""
    from matplotlib.ticker import NullFormatter

    for axis, scale in ((ax.xaxis, ax.get_xscale()), (ax.yaxis, ax.get_yscale())):
        if scale == "log":
            axis.set_minor_formatter(NullFormatter())


def _draw(ax, view: View, columns, controls, shown, passing, np) -> None:
    if isinstance(view, Histogram):
        values = np.asarray(columns[view.column], dtype=float)
        finite = np.isfinite(values) & shown
        data = values[finite]
        if view.log_x:
            data = data[data > 0]
            bins = np.logspace(np.log10(data.min()), np.log10(data.max()), view.bins) if data.size else view.bins
            ax.set_xscale("log")
        else:
            bins = view.bins
        good = values[finite & passing]
        if view.log_x:
            good = good[good > 0]
        ax.hist(data, bins=bins, color=FAIL, label="fails")
        ax.hist(good, bins=bins, color=PASS, label="passes")
        for bound in _thresholds(controls, view.column):
            ax.axvline(bound, color=palette.TOKENS["light"]["ink"], linewidth=1.2, linestyle=(0, (4, 2)))
        if view.log_y:
            ax.set_yscale("log")
        _plain_log_ticks(ax)
        ax.set_xlabel(_label(controls, view.column))
        ax.set_ylabel("rows")
        ax.set_title(view.title or _label(controls, view.column))
    elif isinstance(view, Scatter):
        x = np.asarray(columns[view.x], dtype=float)
        y = np.asarray(columns[view.y], dtype=float)
        index = np.flatnonzero(shown)
        if index.size > view.max_points:
            index = np.random.default_rng(0).choice(index, view.max_points, replace=False)
            index.sort()
        size = 4 if index.size > 5000 else 9
        if view.color == "pass":
            ok = passing[index]
            ax.scatter(x[index][~ok], y[index][~ok], s=size, color=FAIL, label="fails", linewidths=0)
            ax.scatter(x[index][ok], y[index][ok], s=size, color=PASS, label="passes", linewidths=0)
            ax.legend(loc="best", markerscale=2)
        elif view.color:
            values = columns[view.color]
            if np.asarray(values).dtype.kind in "iuf":
                points = ax.scatter(
                    x[index], y[index], s=size, c=np.asarray(values, dtype=float)[index], cmap="cf_teal"
                )
                ax.figure.colorbar(points, ax=ax, label=view.color, shrink=0.8)
            else:
                labels = np.asarray(values, dtype=object)[index]
                names = sorted({str(v) for v in labels})
                colors = palette.categorical()
                for i, label in enumerate(names):
                    chosen = labels.astype(str) == label
                    # Beyond the eight categorical colours, the rest share the neutral "other" colour.
                    color = colors[i] if i < len(colors) else FAIL
                    shown_label = label if i < len(colors) else ("other" if i == len(colors) else None)
                    ax.scatter(x[index][chosen], y[index][chosen], s=size, color=color, label=shown_label, linewidths=0)
                ax.legend(loc="best", markerscale=2)
        else:
            ax.scatter(x[index], y[index], s=size, color=PASS, linewidths=0)
        for bound in _thresholds(controls, view.x):
            ax.axvline(bound, color=palette.TOKENS["light"]["ink"], linewidth=1, linestyle=(0, (4, 2)))
        for bound in _thresholds(controls, view.y):
            ax.axhline(bound, color=palette.TOKENS["light"]["ink"], linewidth=1, linestyle=(0, (4, 2)))
        if view.log_x:
            ax.set_xscale("log")
        if view.log_y:
            ax.set_yscale("log")
        ax.grid(True, axis="both")
        _plain_log_ticks(ax)
        ax.set_xlabel(view.x)
        ax.set_ylabel(view.y)
        ax.set_title(view.title or f"{view.y} vs {view.x}")
    elif isinstance(view, Bar):
        labels = np.asarray(columns[view.column], dtype=object).astype(str)
        categories = sorted(set(labels[shown].tolist()))
        good = [int(((labels == c) & shown & passing).sum()) for c in categories]
        bad = [int(((labels == c) & shown & ~passing).sum()) for c in categories]
        draw = ax.barh if view.horizontal else ax.bar
        draw(categories, good, color=PASS, label="passes")
        if view.horizontal:
            ax.barh(categories, bad, left=good, color=FAIL, label="fails")
        else:
            ax.bar(categories, bad, bottom=good, color=FAIL, label="fails")
        ax.legend(loc="best")
        ax.set_title(view.title or f"rows per {view.column}")
    elif isinstance(view, Box):
        values = np.asarray(columns[view.column], dtype=float)
        if view.by:
            labels = np.asarray(columns[view.by], dtype=object).astype(str)
            groups = sorted(set(labels[shown].tolist()))
            data = [values[shown & (labels == g) & np.isfinite(values)] for g in groups]
        else:
            groups, data = [view.column], [values[shown & np.isfinite(values)]]
        parts = ax.boxplot(data, tick_labels=groups, patch_artist=True, showfliers=False, widths=0.6)
        for patch, color in zip(parts["boxes"], palette.categorical(), strict=False):
            patch.set_facecolor(color)
            patch.set_alpha(0.85)
        for median in parts["medians"]:
            median.set_color(palette.TOKENS["light"]["ink"])
        if view.log_y:
            ax.set_yscale("log")
        ax.set_ylabel(view.column)
        ax.set_title(view.title or (f"{view.column} by {view.by}" if view.by else view.column))
