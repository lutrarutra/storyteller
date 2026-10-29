"""Turns what an analysis produces (matplotlib figures, PIL images, arrays, files, plotly
figures) into embeddable assets, without importing libraries the caller does not use."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from .images import DEFAULT_MAX_PX, Asset, encode_raster, encode_svg

# A matplotlib SVG larger than this (many points, images inside) is embedded as a raster instead.
SVG_MAX_BYTES = 300_000
# Width of the report's content column in CSS pixels; figures are rendered at twice this.
CONTENT_PX = 880

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


class UnsupportedFigure(TypeError):
    pass


def is_plotly(obj: Any) -> bool:
    return hasattr(obj, "to_plotly_json") and hasattr(obj, "data") and hasattr(obj, "layout")


def matplotlib_figure(obj: Any) -> Any | None:
    """The matplotlib Figure behind a Figure, Axes, array of Axes or seaborn grid, else None."""
    if type(obj).__module__.startswith("matplotlib") and hasattr(obj, "savefig"):
        return obj
    for attribute in ("figure", "fig"):  # Axes, seaborn FacetGrid/PairGrid/JointGrid
        figure = getattr(obj, attribute, None)
        if figure is not None and hasattr(figure, "savefig"):
            return figure
    if hasattr(obj, "flat"):  # numpy array of Axes from plt.subplots
        first = next(iter(obj.flat), None)
        if first is not None and hasattr(getattr(first, "figure", None), "savefig"):
            return first.figure
    return None


def from_matplotlib(
    figure: Any,
    *,
    svg_max_bytes: int = SVG_MAX_BYTES,
    max_px: int = DEFAULT_MAX_PX,
    lossless: bool | None = None,
    close: bool = True,
) -> Asset:
    """SVG when the figure is simple enough (sharp in print), else a raster at twice the display width."""
    try:
        if svg_max_bytes > 0:
            buffer = io.BytesIO()
            figure.savefig(buffer, format="svg", bbox_inches="tight")
            if buffer.tell() <= svg_max_bytes:
                return encode_svg(buffer.getvalue())
        width_in = max(figure.get_size_inches()[0], 0.1)
        display_px = min(CONTENT_PX, width_in * 96)
        dpi = max(72.0, min(300.0, 2 * display_px / width_in))
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
        buffer.seek(0)
        with Image.open(buffer) as image:
            image.load()
            return encode_raster(image, max_px=max_px, lossless=lossless, density=2.0)
    finally:
        if close:
            import matplotlib.pyplot as plt

            plt.close(figure)


def from_array(array: Any, *, max_px: int = DEFAULT_MAX_PX, lossless: bool | None = None) -> Asset:
    """An image from a 2-D (greyscale) or 3-D (channels first or last) array of any numeric type."""
    import numpy as np

    data = np.asarray(array)
    if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[-1] not in (1, 3, 4):
        data = np.moveaxis(data, 0, -1)
    if data.ndim == 3 and data.shape[-1] == 1:
        data = data[..., 0]
    if data.ndim not in (2, 3) or (data.ndim == 3 and data.shape[-1] not in (3, 4)):
        raise UnsupportedFigure(f"cannot show an array of shape {data.shape} as an image")
    if data.dtype != np.uint8:
        values = data.astype(np.float64)
        finite = np.isfinite(values)
        low, high = (values[finite].min(), values[finite].max()) if finite.any() else (0.0, 1.0)
        if data.dtype == np.bool_:
            low, high = 0.0, 1.0
        scaled = np.zeros_like(values) if high <= low else (np.where(finite, values, low) - low) / (high - low)
        data = np.round(scaled * 255).astype(np.uint8)
    return encode_raster(Image.fromarray(data), max_px=max_px, lossless=lossless)


def from_file(path: str | Path, *, max_px: int = DEFAULT_MAX_PX, lossless: bool | None = None) -> Asset:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no such figure file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return encode_svg(path.read_bytes())
    if suffix in (".tif", ".tiff"):
        try:
            return _from_tiff(path, max_px=max_px, lossless=lossless)
        except ImportError:
            pass
    if suffix == ".pdf":
        raise UnsupportedFigure(f"{path}: save the figure as PNG or SVG to embed it (PDF cannot be shown inline)")
    with Image.open(path) as image:
        image.load()
        return encode_raster(image, max_px=max_px, lossless=lossless)


def _from_tiff(path: Path, *, max_px: int, lossless: bool | None) -> Asset:
    """Multi-channel and 16-bit microscopy TIFFs through tifffile, when it is installed."""
    import tifffile

    data = tifffile.imread(path)
    while data.ndim > 3:  # z-stacks, time series: show the first plane
        data = data[0]
    return from_array(data, max_px=max_px, lossless=lossless)


def from_bytes(data: bytes, *, max_px: int = DEFAULT_MAX_PX, lossless: bool | None = None) -> Asset:
    head = data[:256].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:1024]):
        return encode_svg(data)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return encode_raster(image, max_px=max_px, lossless=lossless)


def to_asset(obj: Any, *, max_px: int = DEFAULT_MAX_PX, lossless: bool | None = None, close: bool = True) -> Asset:
    """An embeddable image for any supported figure object."""
    if isinstance(obj, Asset):
        return obj
    if isinstance(obj, (str, Path)):
        return from_file(obj, max_px=max_px, lossless=lossless)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return from_bytes(bytes(obj), max_px=max_px, lossless=lossless)
    if isinstance(obj, Image.Image):
        return encode_raster(obj, max_px=max_px, lossless=lossless)
    figure = matplotlib_figure(obj)
    if figure is not None:
        return from_matplotlib(figure, max_px=max_px, lossless=lossless, close=close)
    if hasattr(obj, "__array_interface__") or hasattr(obj, "__array__"):
        return from_array(obj, max_px=max_px, lossless=lossless)
    raise UnsupportedFigure(
        f"cannot embed a {type(obj).__module__}.{type(obj).__name__}: pass a matplotlib figure or axes, "
        "a PIL image, an array, an image path or bytes (plotly figures go to report.plotly)"
    )


# Trace types in the vendored cartesian bundle; anything else needs the full (strict) bundle.
CARTESIAN_TRACES = frozenset(
    {
        "bar",
        "box",
        "contour",
        "heatmap",
        "histogram",
        "histogram2d",
        "histogram2dcontour",
        "image",
        "pie",
        "scatter",
        "scatterternary",
        "violin",
    }
)


def plotly_spec(figure: Any) -> tuple[dict[str, Any], set[str]]:
    """The figure as a JSON-ready {data, layout} dict, and the trace types it uses."""
    if isinstance(figure, dict):
        spec = json.loads(json.dumps(figure, default=_json_default))
    else:
        import plotly.io as pio

        spec = json.loads(pio.to_json(figure, validate=False, pretty=False))
    spec.pop("config", None)
    layout = spec.setdefault("layout", {})
    # The report applies its own template and sizing.
    layout.pop("template", None)
    layout.pop("width", None)
    traces = {trace.get("type", "scatter") for trace in spec.get("data", [])}
    return {"data": spec.get("data", []), "layout": layout}, traces


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"cannot serialise {type(value).__name__} to JSON")
