"""A template's fonts: which it names, which are installed, and how wide a line set in them is.

LibreOffice draws a deck in the fonts its template names when it can find them, from
the system (fontconfig) or from the deck's own `fonts/` folder, and substitutes others
silently: then the drawing no longer shows what PowerPoint will. `missing` names
those, so the agent can install them.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import zipfile
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from PIL import ImageFont

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
# Office names fonts every machine has, which LibreOffice replaces by metric-compatible ones.
SUBSTITUTED = {
    "arial": "Liberation Sans",
    "helvetica": "Liberation Sans",
    "times new roman": "Liberation Serif",
    "courier new": "Liberation Mono",
    "calibri": "Carlito",
    "cambria": "Caladea",
}


def theme_fonts(template: bytes) -> tuple[str, str]:
    """The template's heading (major) and body (minor) fonts."""
    with zipfile.ZipFile(io.BytesIO(template)) as package:
        names = sorted(n for n in package.namelist() if re.fullmatch(r"ppt/theme/theme\d+\.xml", n))
        xml = package.read(names[0]).decode("utf-8") if names else ""
    major = re.search(r'<a:majorFont>\s*<a:latin typeface="([^"]*)"', xml)
    minor = re.search(r'<a:minorFont>\s*<a:latin typeface="([^"]*)"', xml)
    return (major.group(1) if major else "Calibri"), (minor.group(1) if minor else "Calibri")


def named(template: bytes) -> list[str]:
    """Every font family the template names: its theme fonts and those set on its masters and layouts."""
    found = list(dict.fromkeys(theme_fonts(template)))
    with zipfile.ZipFile(io.BytesIO(template)) as package:
        for name in package.namelist():
            if re.fullmatch(r"ppt/(slideMasters/slideMaster|slideLayouts/slideLayout)\d+\.xml", name):
                for family in re.findall(r'<a:latin typeface="([^"+][^"]*)"', package.read(name).decode("utf-8")):
                    if family not in found:
                        found.append(family)
    return found


@cache
def _system() -> dict[str, list[Path]]:
    """Installed font files by lower-case family name (every name fontconfig knows them by)."""
    out: dict[str, list[Path]] = {}
    lister = shutil.which("fc-list")
    if lister:
        result = subprocess.run(
            [lister, "--format", "%{family}|%{file}\n"], capture_output=True, text=True, check=False
        )
        for line in result.stdout.splitlines():
            families, _, file = line.rpartition("|")
            for family in families.split(","):
                out.setdefault(family.strip().lower(), []).append(Path(file))
    return out


def _folder(directory: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in FONT_SUFFIXES:
            continue
        try:
            family, style = ImageFont.truetype(str(path), 12).getname()
        except OSError:
            continue
        for name in {family, f"{family} {style}"}:
            out.setdefault(name.lower(), []).append(path)
    return out


STYLE_WORDS = re.compile(r"\s+(regular|book|normal|medium|semibold|bold|light|thin|black|italic)$", re.I)


def files(family: str, folders: Iterable[Path] = ()) -> list[Path]:
    """The installed files of `family`, the deck's own fonts first. Templates often name a style
    as part of the family ("Source Sans Pro Regular"); then the family without it will do."""
    folders = list(folders)
    key = family.strip().lower()
    while True:
        found: list[Path] = []
        for folder in folders:
            found += _folder(Path(folder)).get(key, [])
        found += _system().get(key, [])
        shorter = STYLE_WORDS.sub("", key)
        if found or shorter == key:
            return found
        key = shorter


def missing(template: bytes, folders: Iterable[Path] = ()) -> list[str]:
    """The fonts the template names that neither the system nor the deck's fonts folder has
    (Office's standard fonts count as present: LibreOffice has metric-compatible ones)."""
    folders = list(folders)
    absent = []
    for family in named(template):
        substitute = SUBSTITUTED.get(family.lower())
        if files(family, folders) or (substitute and (files(substitute, folders) or not shutil.which("fc-list"))):
            continue
        absent.append(family)
    return absent


def line_width(text: str, family: str, size_pt: float, bold: bool, folders: Iterable[Path] = ()) -> float | None:
    """How wide `text` is set on one line, in points, or None when the font is not installed."""
    candidates = files(family, folders)
    if not candidates:
        return None
    path = candidates[0]
    if bold:
        path = next((p for p in candidates if "bold" in p.stem.lower()), path)
    try:
        return ImageFont.truetype(str(path), 1000).getlength(text) * size_pt / 1000
    except OSError:
        return None
