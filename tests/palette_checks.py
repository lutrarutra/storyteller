"""Colour-vision checks for chart palettes, in OKLab.

Categorical palettes: every colour inside a lightness band and above a chroma floor
(so no colour reads as grey or washes out), neighbours separated by ΔE ≥ 8 under
protanopia and deuteranopia (Machado, Oliveira & Fernandes 2009, severity 1) and by
ΔE ≥ 15 for normal vision. ΔE is the Euclidean OKLab distance × 100.

Ordered (sequential) classes: one hue, lightness monotone with visible steps, and
the lightest step at least 2:1 against the surface (WCAG contrast).
"""

from __future__ import annotations

import math

from storyteller.theme.palette import _to_linear, hex_to_rgb, to_oklab

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_FLOOR = 8.0
NORMAL_FLOOR = 15.0
ORDINAL_MIN_STEP = 0.06
ORDINAL_LIGHT_CONTRAST = 2.0

MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
}


def _lab_from_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)  # noqa: E741
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def _simulated(color: str, kind: str | None) -> tuple[float, float, float]:
    linear = tuple(_to_linear(c) for c in hex_to_rgb(color))
    if kind:
        matrix = MACHADO[kind]
        linear = tuple(min(1.0, max(0.0, sum(m * c for m, c in zip(row, linear, strict=True)))) for row in matrix)
    return _lab_from_linear(linear)  # type: ignore[arg-type]


def delta_e(a: str, b: str, kind: str | None = None) -> float:
    return math.dist(_simulated(a, kind), _simulated(b, kind)) * 100


def lch(color: str) -> tuple[float, float, float]:
    L, a, b = to_oklab(color)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def contrast(a: str, b: str) -> float:
    def luminance(color: str) -> float:
        r, g, b = (_to_linear(c) for c in hex_to_rgb(color))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def categorical_problems(colors: list[str], mode: str) -> list[str]:
    low, high = BAND[mode]
    problems = [f"{c} lightness {lch(c)[0]:.3f} outside {low}–{high}" for c in colors if not low <= lch(c)[0] <= high]
    problems += [f"{c} chroma {lch(c)[1]:.3f} below {CHROMA_FLOOR}" for c in colors if lch(c)[1] < CHROMA_FLOOR]
    for a, b in zip(colors, colors[1:], strict=False):
        for kind in ("protan", "deutan"):
            if (d := delta_e(a, b, kind)) < CVD_FLOOR:
                problems.append(f"{a}/{b} ΔE {d:.1f} under {kind} simulation")
        if (d := delta_e(a, b)) < NORMAL_FLOOR:
            problems.append(f"{a}/{b} ΔE {d:.1f} for normal vision")
    return problems


def ordinal_problems(colors: list[str], surface: str) -> list[str]:
    lightness = [lch(c)[0] for c in colors]
    problems = []
    if lightness != sorted(lightness, reverse=True):
        problems.append(f"lightness not monotone: {[round(v, 3) for v in lightness]}")
    problems += [
        f"{a}/{b} lightness step {abs(la - lb):.3f}"
        for (a, la), (b, lb) in zip(
            zip(colors, lightness, strict=True), zip(colors[1:], lightness[1:], strict=True), strict=False
        )
        if abs(la - lb) < ORDINAL_MIN_STEP
    ]
    if (ratio := contrast(colors[0], surface)) < ORDINAL_LIGHT_CONTRAST:
        problems.append(f"lightest {colors[0]} only {ratio:.2f}:1 against {surface}")
    hues = [lch(c)[2] for c in colors]
    spread = max(hues) - min(hues)
    if min(spread, 360 - spread) > 40:
        problems.append(f"hue spread {spread:.0f}° (not one hue)")
    return problems
