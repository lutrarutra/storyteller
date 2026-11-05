"""Theme colours for the shapes and charts storyteller draws: named by their slot (``accent1``,
``tx1``, ``bg1``), optionally see-through, so a user's template and dark slides (which remap the
slots) restyle them. A faint ``tx1`` is grey on light slides and on dark ones alike."""

from __future__ import annotations

from typing import Any

A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _theme(name: str):
    from pptx.enum.dml import MSO_THEME_COLOR

    slots = {"tx1": "TEXT_1", "tx2": "TEXT_2", "bg1": "BACKGROUND_1", "bg2": "BACKGROUND_2"}
    if name.startswith("accent"):
        return getattr(MSO_THEME_COLOR, f"ACCENT_{name[6:]}")
    return getattr(MSO_THEME_COLOR, slots[name])


def _set(color: Any, name: str, alpha: float | None) -> None:
    from lxml import etree

    color.theme_color = _theme(name)
    element = color._color._xClr
    for old in element.findall(f"{{{A}}}alpha"):
        element.remove(old)
    if alpha is not None:
        etree.SubElement(element, f"{{{A}}}alpha", val=str(round(alpha * 100000)))


def fill(fill_format: Any, name: str, alpha: float | None = None) -> None:
    fill_format.solid()
    _set(fill_format.fore_color, name, alpha)


def line(line_format: Any, name: str, alpha: float | None = None, width: int | None = None) -> None:
    line_format.fill.solid()
    _set(line_format.color, name, alpha)
    if width is not None:
        line_format.width = width


def font(font_format: Any, name: str, alpha: float | None = None) -> None:
    _set(font_format.color, name, alpha)
