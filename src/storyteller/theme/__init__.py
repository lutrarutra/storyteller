"""Colours and chart defaults: `sty.theme.use()` styles matplotlib and plotly like the report."""

from __future__ import annotations

import importlib.util

from . import palette
from .palette import Mode


def use(mode: Mode = "light") -> None:
    """Applies code flower's theme to matplotlib and plotly, whichever are installed."""
    if importlib.util.find_spec("matplotlib"):
        from . import mpl

        mpl.use(mode)
    if importlib.util.find_spec("plotly"):
        from . import plotly

        plotly.use(mode)


__all__ = ["palette", "use"]
