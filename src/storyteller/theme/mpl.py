"""code flower's look for matplotlib (and seaborn, which draws with it)."""

from __future__ import annotations

from typing import Any

from . import palette

FONT_FAMILY = ["DM Sans", "Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]

COLORMAPS = {
    "cf_teal": lambda: palette.colormap_stops("teal"),
    "cf_indigo": lambda: palette.colormap_stops("indigo"),
    "cf_pink": lambda: palette.colormap_stops("pink"),
    "cf_div": lambda: palette.diverging_stops(9),
}


def rc_params(mode: palette.Mode = "light") -> dict[str, Any]:
    """The rcParams of the theme, for `plt.rcParams.update` or `plt.rc_context`."""
    from cycler import cycler

    tokens = palette.TOKENS[mode]
    ink, muted, grid = tokens["ink"], tokens["muted"], tokens["soft-line"]
    background = "#ffffff" if mode == "light" else tokens["card"]
    return {
        "axes.prop_cycle": cycler(color=palette.categorical(mode=mode)),
        "image.cmap": "cf_teal",
        "font.family": "sans-serif",
        "font.sans-serif": FONT_FAMILY,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 10,
        "axes.labelsize": 10,
        "axes.labelcolor": ink,
        "axes.edgecolor": muted,
        "axes.linewidth": 0.8,
        "axes.facecolor": background,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "grid.color": grid,
        "grid.linewidth": 0.8,
        "xtick.color": muted,
        "ytick.color": muted,
        "xtick.labelcolor": ink,
        "ytick.labelcolor": ink,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "lines.linewidth": 2,
        "lines.markersize": 5,
        "patch.edgecolor": background,
        "patch.linewidth": 0.8,
        "scatter.edgecolors": "none",
        "figure.facecolor": background,
        "figure.edgecolor": background,
        "figure.dpi": 100,
        "figure.figsize": (7.0, 4.2),
        "figure.constrained_layout.use": True,
        "savefig.facecolor": background,
        "savefig.dpi": 150,
        "svg.fonttype": "path",
        "text.color": ink,
    }


def register_colormaps() -> None:
    """Registers cf_teal, cf_indigo, cf_pink and cf_div (and their _r reverses) with matplotlib."""
    import matplotlib as mpl
    from matplotlib.colors import LinearSegmentedColormap

    for name, stops in COLORMAPS.items():
        if name in mpl.colormaps:
            continue
        cmap = LinearSegmentedColormap.from_list(name, stops())
        mpl.colormaps.register(cmap, name=name)
        mpl.colormaps.register(cmap.reversed(), name=f"{name}_r")


def use(mode: palette.Mode = "light") -> None:
    """Makes the theme matplotlib's default for this process."""
    import matplotlib.pyplot as plt

    register_colormaps()
    plt.rcParams.update(rc_params(mode))
