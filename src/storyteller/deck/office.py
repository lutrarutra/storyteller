"""LibreOffice, headless: a deck's .pptx to PDF, and the PDF's pages to images.

LibreOffice is the renderer because it reads the .pptx itself, templates and all, so
the slides the user watches are the file they get. One LibreOffice profile is shared
by every deck of this user (a first start makes it, which takes a few seconds), and a
profile serves one LibreOffice at a time, so conversions take turns under a lock.

`STORYTELLER_SOFFICE` names the program when it is not on PATH.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from xml.sax.saxutils import escape

MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
TIMEOUT_S = 180
RETRY_S = 1.5


class OfficeError(RuntimeError):
    pass


def soffice() -> str | None:
    """LibreOffice's command, or None when it is not installed."""
    named = os.environ.get("STORYTELLER_SOFFICE")
    if named:
        return named
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return MAC_SOFFICE if sys.platform == "darwin" and Path(MAC_SOFFICE).exists() else None


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "storyteller"


@contextlib.contextmanager
def _profile() -> Iterator[Path]:
    """The shared LibreOffice profile, held for one conversion."""
    directory = _cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    profile = directory / "libreoffice-profile"
    with open(directory / "libreoffice.lock", "a+") as handle:
        try:
            import fcntl
        except ImportError:  # Windows: conversions are the caller's to serialise
            yield profile
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield profile
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _font_config(fonts: Path | None) -> dict[str, str]:
    """The environment that has LibreOffice see the fonts in `fonts` besides the system's."""
    if fonts is None or not fonts.is_dir():
        return {}
    directory = _cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / f"fonts-{hashlib.sha1(str(fonts.resolve()).encode()).hexdigest()[:12]}.conf"
    text = (
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{escape(str(fonts.resolve()))}</dir>"
        f"<cachedir>{escape(str(directory / 'fontconfig'))}</cachedir>"
        "</fontconfig>"
    )
    if not config.is_file() or config.read_text() != text:
        config.write_text(text)
    return {"FONTCONFIG_FILE": str(config)}


def to_pdf(source: Path, out_dir: Path, fonts: Path | None = None) -> Path:
    """Converts a .pptx (or any document LibreOffice reads) to a PDF in `out_dir`, with the fonts
    in the folder `fonts` available besides the system's."""
    program = soffice()
    if program is None:
        raise OfficeError("LibreOffice is not installed (soffice is not on PATH; set STORYTELLER_SOFFICE)")
    out_dir.mkdir(parents=True, exist_ok=True)
    with _profile() as profile:
        command = [
            program,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        environment = {**os.environ, **_font_config(fonts)}
        target = out_dir / (source.stem + ".pdf")
        # A LibreOffice started just as the last one exits may hand its document to that one, which
        # then fails to load it ("source file could not be loaded"): a second try finds it gone.
        for attempt in range(2):
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False, env=environment
                )
            except subprocess.TimeoutExpired:
                raise OfficeError(f"LibreOffice took more than {TIMEOUT_S} s to convert {source.name}") from None
            if target.is_file() or attempt:
                break
            time.sleep(RETRY_S)
    if not target.is_file():
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise OfficeError(f"LibreOffice could not convert {source.name}: {detail[0]}")
    return target


def convert(source: Path, suffix: str) -> bytes:
    """`source` converted by LibreOffice to the format of `suffix` (e.g. ".pptx" for an .otp)."""
    program = soffice()
    if program is None:
        raise OfficeError("LibreOffice is not installed (soffice is not on PATH; set STORYTELLER_SOFFICE)")
    with tempfile.TemporaryDirectory(prefix="storyteller-") as work, _profile() as profile:
        command = [
            program,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            suffix.lstrip("."),
            "--outdir",
            work,
            str(source),
        ]
        subprocess.run(command, capture_output=True, timeout=TIMEOUT_S, check=False)
        target = Path(work) / (source.stem + suffix)
        if not target.is_file():
            raise OfficeError(f"LibreOffice could not convert {source.name} to {suffix}")
        return target.read_bytes()


def pages(pdf: Path, width_px: int) -> list:
    """The PDF's pages as PIL images `width_px` wide."""
    import logging

    import pypdfium2 as pdfium

    # pypdfium2 5 warns about its own weak references as it cleans up (and again at exit), which
    # says nothing about the slides.
    logging.getLogger("pypdfium2").setLevel(logging.ERROR)
    with contextlib.suppress(ImportError, AttributeError):
        import pypdfium2_cfg

        pypdfium2_cfg.DEBUG_AUTOCLOSE.value = max(pypdfium2_cfg.DEBUG_AUTOCLOSE.value, logging.ERROR)

    document = pdfium.PdfDocument(str(pdf))
    try:
        images = []
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=width_px / page.get_width())
            images.append(bitmap.to_pil().copy())
            bitmap.close()
            page.close()
        return images
    finally:
        document.close()
