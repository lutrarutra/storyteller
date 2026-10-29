"""Encoding images for embedding: resize, pick the smallest suitable format, strip metadata.

Every embedded file is an `Asset`, addressed by the SHA-256 of its bytes, so the
same image is stored once however often it is used and re-encoding is skipped
when a report is rebuilt.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass

from PIL import Image, ImageOps

DEFAULT_MAX_PX = 2000
PHOTO_QUALITY = 85
# Images with at most this many colours are graphics (plots, diagrams): lossless.
FLAT_COLOURS = 256


@dataclass(frozen=True)
class Asset:
    data: bytes
    mime: str
    width: int | None = None  # intrinsic pixels (SVG: CSS pixels from its size)
    height: int | None = None
    density: float = 1.0  # pixels per CSS pixel: 2 for figures rendered at 2x

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def extension(self) -> str:
        return MIME_EXTENSIONS.get(self.mime, "bin")

    def data_uri(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"

    @property
    def display_width(self) -> int | None:
        return round(self.width / self.density) if self.width else None

    @property
    def display_height(self) -> int | None:
        return round(self.height / self.density) if self.height else None

    @property
    def is_raster(self) -> bool:
        return self.mime in ("image/png", "image/jpeg", "image/webp")


MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "application/json": "json",
    "application/gzip": "gz",
    "application/octet-stream": "bin",
}
EXTENSION_MIMES = {ext: mime for mime, ext in MIME_EXTENSIONS.items()}


def _encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def _has_alpha(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255
    return False


def prepare(image: Image.Image, max_px: int = DEFAULT_MAX_PX) -> Image.Image:
    """Upright, 8-bit RGB(A) or greyscale, no larger than `max_px` on its long side."""
    image = ImageOps.exif_transpose(image)
    if image.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        image = _stretch_to_8bit(image)
    elif image.mode == "P":
        image = image.convert("RGBA")
    elif image.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
        image = image.convert("RGB")
    elif image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert("RGBA")
    if max(image.size) > max_px:
        scale = max_px / max(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
    return image


def _stretch_to_8bit(image: Image.Image) -> Image.Image:
    """Maps a 16-bit or float greyscale image to 0..255 over its own range."""
    low, high = image.getextrema()
    if high <= low:
        return Image.new("L", image.size, 0)
    image = image.convert("F")
    return image.point(lambda v: (v - low) * 255.0 / (high - low)).convert("L")


def encode_raster(
    image: Image.Image,
    *,
    max_px: int = DEFAULT_MAX_PX,
    lossless: bool | None = None,
    quality: int = PHOTO_QUALITY,
    density: float = 1.0,
) -> Asset:
    """The smallest good encoding of an image.

    Graphics (few colours) stay lossless: optimised PNG, palette PNG when it is
    exact, or lossless WebP. Photographs and micrographs become WebP or JPEG at
    `quality`. `lossless=True` forces a lossless format, `False` a lossy one.
    Metadata is dropped in every case.
    """
    image = prepare(image, max_px)
    alpha = _has_alpha(image)
    if not alpha and image.mode in ("RGBA", "LA"):
        image = image.convert("RGB" if image.mode == "RGBA" else "L")
    flat = image.getcolors(FLAT_COLOURS) is not None
    candidates: list[tuple[bytes, str]] = []
    if lossless or (lossless is None and flat):
        candidates.append((_encode(image, "PNG", optimize=True), "image/png"))
        if flat and image.mode in ("RGB", "RGBA"):
            # Exact: at most 256 colours fit a palette without loss.
            paletted = image.quantize(colors=FLAT_COLOURS, method=Image.Quantize.FASTOCTREE if alpha else None)
            candidates.append((_encode(paletted, "PNG", optimize=True), "image/png"))
        candidates.append((_encode(image, "WEBP", lossless=True, method=6), "image/webp"))
    else:
        candidates.append((_encode(image, "WEBP", quality=quality, method=6), "image/webp"))
        if not alpha:
            rgb = image if image.mode in ("RGB", "L") else image.convert("RGB")
            candidates.append((_encode(rgb, "JPEG", quality=quality, optimize=True, progressive=True), "image/jpeg"))
    data, mime = min(candidates, key=lambda candidate: len(candidate[0]))
    return Asset(data, mime, image.width, image.height, density)


def shrink(asset: Asset, factor: float = 0.75, quality: int = 75) -> Asset:
    """A smaller re-encoding of a raster asset, for fitting a report into its size budget."""
    if not asset.is_raster:
        return asset
    with Image.open(io.BytesIO(asset.data)) as image:
        image.load()
        target = max(1, round(max(image.size) * factor))
        lossless = asset.mime == "image/png" or (asset.mime == "image/webp" and quality >= 100)
        smaller = encode_raster(image, max_px=target, lossless=lossless or None, quality=quality, density=asset.density)
    return smaller if len(smaller.data) < len(asset.data) else asset


_SVG_JUNK = [
    re.compile(rb"<\?xml[^>]*\?>\s*"),
    re.compile(rb"<!DOCTYPE[^>]*>\s*", re.IGNORECASE),
    re.compile(rb"<!--.*?-->\s*", re.DOTALL),
    re.compile(rb"<metadata>.*?</metadata>\s*", re.DOTALL),
]
_SVG_SIZE = re.compile(rb'<svg[^>]*?\swidth="([\d.]+)(pt|px)?"[^>]*?\sheight="([\d.]+)(pt|px)?"', re.DOTALL)


def encode_svg(data: bytes) -> Asset:
    """An SVG without its XML prolog, comments and metadata, with whitespace between tags collapsed."""
    for pattern in _SVG_JUNK:
        data = pattern.sub(b"", data)
    data = re.sub(rb">\s+<", b"><", data).strip()
    width = height = None
    match = _SVG_SIZE.search(data)
    if match:
        to_px = 4 / 3 if match.group(2) == b"pt" else 1.0
        width = round(float(match.group(1)) * to_px)
        height = round(float(match.group(3)) * (4 / 3 if match.group(4) == b"pt" else 1.0))
    return Asset(data, "image/svg+xml", width, height)
