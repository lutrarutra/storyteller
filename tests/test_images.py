import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from storyteller import adapters
from storyteller.images import encode_raster, encode_svg, shrink


def _decode(asset):
    return Image.open(io.BytesIO(asset.data))


def test_large_photo_is_resized_and_lossy():
    rng = np.random.default_rng(0)
    photo = Image.fromarray(rng.integers(0, 255, (3000, 4000, 3), dtype=np.uint8))
    asset = encode_raster(photo, max_px=1200)
    assert max(asset.width, asset.height) == 1200
    assert asset.mime in ("image/webp", "image/jpeg")
    assert max(_decode(asset).size) == 1200


def test_flat_graphic_stays_lossless():
    image = Image.new("RGB", (400, 300), "#fffaf0")
    image.paste((79, 70, 229), (50, 50, 200, 200))
    asset = encode_raster(image)
    assert asset.mime in ("image/png", "image/webp")
    decoded = _decode(asset).convert("RGB")
    assert decoded.getpixel((100, 100)) == (79, 70, 229)
    assert decoded.getpixel((10, 10)) == (255, 250, 240)


def test_metadata_is_dropped():
    image = Image.new("RGB", (64, 64), "white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=None, dpi=(300, 300))
    asset = adapters.from_bytes(buffer.getvalue())
    assert "dpi" not in _decode(asset).info


def test_16_bit_and_float_arrays_are_normalised():
    ramp = np.linspace(0, 65535, 256 * 256, dtype=np.uint16).reshape(256, 256)
    decoded = np.asarray(_decode(adapters.from_array(ramp)).convert("L"))
    assert decoded.min() == 0 and decoded.max() == 255
    floats = np.full((32, 32), np.nan)
    floats[:16] = 1.5
    floats[16:] = -2.0
    assert _decode(adapters.from_array(floats)).size == (32, 32)


def test_channel_first_arrays_are_moved_last():
    array = np.zeros((3, 20, 30), dtype=np.uint8)
    array[0] = 255
    image = _decode(adapters.from_array(array)).convert("RGB")
    assert image.size == (30, 20)
    assert image.getpixel((0, 0)) == (255, 0, 0)


def test_small_matplotlib_figure_becomes_svg_large_one_raster():
    import matplotlib.pyplot as plt

    figure, ax = plt.subplots()
    ax.plot([1, 2, 3], [3, 1, 2])
    small = adapters.to_asset(figure)
    assert small.mime == "image/svg+xml"
    assert b"<?xml" not in small.data and b"<metadata" not in small.data
    assert not plt.fignum_exists(figure.number)

    figure, ax = plt.subplots()
    rng = np.random.default_rng(0)
    ax.scatter(rng.normal(size=60_000), rng.normal(size=60_000), s=1)
    big = adapters.to_asset(figure)
    assert big.is_raster and big.density == 2.0
    assert big.width <= 2000


def test_axes_and_seaborn_like_grids_are_accepted():
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2)
    assert adapters.matplotlib_figure(axes) is figure
    assert adapters.matplotlib_figure(axes[0]) is figure

    class Grid:
        def __init__(self, figure):
            self.figure = figure

    assert adapters.matplotlib_figure(Grid(figure)) is figure
    plt.close(figure)


def test_svg_size_is_read():
    asset = encode_svg(b'<?xml version="1.0"?><!-- c --><svg width="72pt" height="36pt"><metadata>x</metadata></svg>')
    assert (asset.width, asset.height) == (96, 48)
    assert asset.data == b'<svg width="72pt" height="36pt"></svg>'


def test_shrink_makes_smaller_or_keeps():
    rng = np.random.default_rng(1)
    photo = encode_raster(Image.fromarray(rng.integers(0, 255, (1000, 1000, 3), dtype=np.uint8)))
    smaller = shrink(photo)
    assert len(smaller.data) < len(photo.data)
    assert smaller.width == 750


def test_unsupported_objects_say_what_works():
    with pytest.raises(adapters.UnsupportedFigure, match="matplotlib figure"):
        adapters.to_asset(object())
    with pytest.raises(adapters.UnsupportedFigure, match="PNG or SVG"):
        path = Path("figure.pdf")
        path.write_bytes(b"%PDF")
        adapters.to_asset(path)


def test_plotly_spec_and_trace_types():
    import plotly.graph_objects as go

    figure = go.Figure(go.Scatter(x=np.arange(3), y=np.arange(3)))
    figure.add_trace(go.Sankey())
    spec, traces = adapters.plotly_spec(figure)
    assert traces == {"scatter", "sankey"}
    assert "template" not in spec["layout"]
