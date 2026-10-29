"""code flower's look for plotly: a layout template, as a plain dict and as a registered template."""

from __future__ import annotations

from typing import Any

from . import palette

FONT = "DM Sans, system-ui, -apple-system, Segoe UI, sans-serif"


def _scale(stops: list[str]) -> list[list[Any]]:
    return [[i / (len(stops) - 1), color] for i, color in enumerate(stops)]


def layout(mode: palette.Mode = "light") -> dict[str, Any]:
    """The template's layout as a dict; the report's runtime applies the same one to its own charts."""
    tokens = palette.TOKENS[mode]
    axis = {
        "gridcolor": tokens["soft-line"],
        "linecolor": tokens["muted"],
        "zerolinecolor": tokens["soft-line"],
        "tickcolor": tokens["muted"],
        "ticks": "outside",
        "ticklen": 4,
        "automargin": True,
        "title": {"standoff": 8},
    }
    return {
        "colorway": palette.categorical(mode=mode),
        "font": {"family": FONT, "size": 12, "color": tokens["ink"]},
        "title": {"x": 0, "xanchor": "left", "font": {"size": 15}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "xaxis": {**axis, "showgrid": False},
        "yaxis": axis,
        "legend": {"bgcolor": "rgba(0,0,0,0)", "borderwidth": 0},
        "hoverlabel": {"bgcolor": tokens["card"], "bordercolor": tokens["line"], "font": {"color": tokens["ink"]}},
        "colorscale": {
            "sequential": _scale(palette.colormap_stops("teal")),
            "diverging": _scale(palette.diverging_stops(9)),
        },
        "margin": {"l": 56, "r": 16, "t": 40, "b": 48},
        "bargap": 0.12,
    }


def template(mode: palette.Mode = "light") -> dict[str, Any]:
    return {"layout": layout(mode), "data": {"bar": [{"marker": {"line": {"width": 0}}}]}}


def use(mode: palette.Mode = "light") -> None:
    """Registers the "codeflower" template and makes it plotly's default."""
    import plotly.io as pio

    pio.templates["codeflower"] = template(mode)
    pio.templates.default = "codeflower"
