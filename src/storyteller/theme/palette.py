"""code flower's colours for reports and charts.

The brand tokens are those of the extension's webview and the launcher window
(paper and ink, with indigo, teal, pink and yellow accents). The chart palettes
are derived from them and checked with an OKLab colour-vision validator
(tests/test_palette.py): the categorical order passes lightness, chroma,
colour-blind and normal-vision separation on both report surfaces, and the
discrete sequential steps are one hue with visible lightness gaps.

Rules the palettes encode:

- categorical colours are assigned in this fixed order, never cycled: more than
  eight categories fold into "Other" or become small multiples;
- sequential = one hue, light to dark; diverging = two hues through a neutral
  midpoint;
- status colours are reserved for good/warning/critical and always come with a
  label or icon.
"""

from __future__ import annotations

import math
from typing import Literal

Mode = Literal["light", "dark"]

TOKENS: dict[Mode, dict[str, str]] = {
    "light": {
        "paper": "#f6efd9",
        "card": "#fffaf0",
        "ink": "#1d1d1f",
        "muted": "#6b5d45",
        "line": "#1d1d1f",
        "soft-line": "#e3d8bd",
        "shadow": "#1d1d1f",
        "code": "#efe5c9",
        "indigo": "#4f46e5",
        "indigo-soft": "#e4e1ff",
        "teal": "#0f9d8a",
        "teal-soft": "#d3f1eb",
        "pink": "#e8668f",
        "pink-soft": "#fde1ea",
        "yellow": "#ffcf3f",
        "yellow-soft": "#fff1c2",
        "orange": "#ed9e4b",
        "red": "#e5483b",
        "green": "#3a9d4b",
        "on-accent": "#ffffff",
    },
    "dark": {
        "paper": "#1c1b21",
        "card": "#25242c",
        "ink": "#ece6d6",
        "muted": "#a19a8b",
        "line": "#45424f",
        "soft-line": "#34323c",
        "shadow": "transparent",
        "code": "#2c2a33",
        "indigo": "#7f78ff",
        "indigo-soft": "#2b2946",
        "teal": "#3cc9b4",
        "teal-soft": "#1b3533",
        "pink": "#f08cab",
        "pink-soft": "#3d2530",
        "yellow": "#ffd75e",
        "yellow-soft": "#352f1e",
        "orange": "#f2a65a",
        "red": "#f07167",
        "green": "#6cc77b",
        "on-accent": "#ffffff",
    },
}

# Indigo, teal, orange, raspberry, cyan, gold, violet, red. The brand's bright
# yellow and light orange are too pale for marks on cream and stay highlight
# colours; orange and gold here are below 3:1 contrast, so charts using them
# carry a legend or direct labels (the report's charts always do).
CATEGORICAL: dict[Mode, tuple[str, ...]] = {
    "light": ("#4f46e5", "#0f9d8a", "#d9822b", "#c2477a", "#0f8fb0", "#b8930a", "#8b5cf6", "#d6453a"),
    "dark": ("#837ffd", "#23a894", "#cb7d2f", "#d76698", "#2c9fcc", "#b18d0b", "#987beb", "#e4645d"),
}

STATUS: dict[Mode, dict[str, str]] = {
    "light": {"good": "#3a9d4b", "warning": "#c97a1c", "critical": "#d6453a", "info": "#4f46e5"},
    "dark": {"good": "#6cc77b", "warning": "#f2a65a", "critical": "#f07167", "info": "#7f78ff"},
}

# The colour of "the rest": filtered-out points, "Other", backgrounds of highlights.
NEUTRAL: dict[Mode, str] = {"light": "#c9bfa6", "dark": "#5a5664"}

# Hue anchors of the sequential ramps (OKLCH hue is taken from these).
_HUES = {"teal": "#0f9d8a", "indigo": "#4f46e5", "pink": "#e8668f"}
SEQUENTIAL_NAMES = tuple(_HUES)


# -- OKLab ----------------------------------------------------------------------


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    """`#rrggbb` → sRGB components in 0..1."""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected #rrggbb, got {color!r}")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def to_oklab(color: str) -> tuple[float, float, float]:
    r, g, b = (_to_linear(c) for c in hex_to_rgb(color))
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)  # noqa: E741
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def _oklab_to_linear(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3  # noqa: E741
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def from_oklch(lightness: float, chroma: float, hue: float) -> str:
    """An sRGB colour for OKLCH values; chroma is reduced until it is in gamut."""
    while True:
        rgb = _oklab_to_linear((lightness, chroma * math.cos(hue), chroma * math.sin(hue)))
        if all(-1e-9 <= c <= 1 + 1e-9 for c in rgb) or chroma <= 0:
            return rgb_to_hex(tuple(_to_srgb(min(1.0, max(0.0, c))) for c in rgb))  # type: ignore[arg-type]
        chroma -= 0.002


def _hue(color: str) -> float:
    _, a, b = to_oklab(color)
    return math.atan2(b, a)


def _ramp(name: str, n: int, light: float, dark: float, peak: float) -> list[str]:
    hue = _hue(_HUES[name])
    steps = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.5
        # Chroma arcs: muted at the light end, strongest in the middle, still coloured at the dark end.
        chroma = peak * (0.3 + 0.7 * math.sin(math.pi * (0.15 + 0.7 * t)))
        steps.append(from_oklch(light + (dark - light) * t, chroma, hue))
    return steps


_PEAK = {"teal": 0.13, "indigo": 0.24, "pink": 0.18}


# -- public palettes --------------------------------------------------------------


def categorical(n: int | None = None, mode: Mode = "light") -> list[str]:
    """The first `n` categorical colours (all eight by default), in their fixed order."""
    colors = list(CATEGORICAL[mode])
    if n is None:
        return colors
    if n > len(colors):
        raise ValueError(
            f"{n} categories, but the palette has {len(colors)} colours: "
            "fold the smallest into 'Other' or use small multiples"
        )
    return colors[:n]


def sequential(n: int, name: str = "teal") -> list[str]:
    """`n` discrete steps of one hue, light to dark (for ordered classes, at most 7).

    The lightest step still stands off the report surface, so every class is visible.
    """
    if name not in _HUES:
        raise ValueError(f"unknown sequential palette {name!r}; choose from {', '.join(_HUES)}")
    if not 1 <= n <= 7:
        raise ValueError("ordered classes need 1 to 7 steps; use a continuous colormap for more")
    return _ramp(name, n, 0.75, 0.34 if name != "indigo" else 0.28, _PEAK[name])


def colormap_stops(name: str = "teal", n: int = 9) -> list[str]:
    """Stops of a continuous one-hue colormap, near-white to dark (for heatmaps and expression)."""
    if name not in _HUES:
        raise ValueError(f"unknown colormap {name!r}; choose from {', '.join(_HUES)}")
    return _ramp(name, n, 0.96, 0.32 if name != "indigo" else 0.28, _PEAK[name])


def diverging_stops(n: int = 9) -> list[str]:
    """Teal ↔ neutral ↔ pink, through a warm grey midpoint (for signed values like log fold change)."""
    if n < 3 or n % 2 == 0:
        raise ValueError("a diverging palette needs an odd number of stops, at least 3")
    half = n // 2
    teal_hue, pink_hue = _hue(_HUES["teal"]), _hue(_HUES["pink"])
    left = [from_oklch(0.36 + (0.88 - 0.36) * i / half, 0.13 * (1 - i / half) + 0.02, teal_hue) for i in range(half)]
    right = [
        from_oklch(0.88 - (0.88 - 0.38) * i / half, 0.17 * (i / half) + 0.02, pink_hue) for i in range(1, half + 1)
    ]
    return [*left, "#ebe6dc", *right]


def status(kind: str, mode: Mode = "light") -> str:
    return STATUS[mode][kind]


def css_variables(mode: Mode = "light") -> str:
    """The tokens and chart colours as CSS custom properties (`--cf-*`)."""
    lines = [f"--cf-{name}: {value};" for name, value in TOKENS[mode].items()]
    lines += [f"--cf-c{i + 1}: {value};" for i, value in enumerate(CATEGORICAL[mode])]
    lines += [f"--cf-{name}-status: {value};" for name, value in STATUS[mode].items()]
    lines.append(f"--cf-neutral: {NEUTRAL[mode]};")
    return " ".join(lines)
