"""Checks that a report is self-contained: nothing it shows or runs comes from elsewhere.

Links the reader may click (``<a href="https://...">``) are fine; resources the page
loads by itself (``src``, stylesheet links, CSS ``url()``, ``srcset``, imports) must
be embedded as data URIs.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PREVIEW_LIMIT = 10 * 1024 * 1024

_URL_IN_CSS = re.compile(r"""url\(\s*(['"]?)(?!data:|#)([^'")]+)\1\s*\)""", re.IGNORECASE)
_IMPORT_IN_CSS = re.compile(r"""@import\s+(?:url\()?\s*['"]?(?!data:)([^'");\s]+)""", re.IGNORECASE)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self._in_style = False
        self._in_script = False

    def _external(self, value: str | None) -> bool:
        return bool(value) and not value.startswith(("data:", "#", "blob:", "about:")) and value.strip() != ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "style":
            self._in_style = True
        if tag == "script":
            self._in_script = True
            if values.get("src"):
                self.problems.append(f"<script src={values['src']!r}> loads code from outside the file")
        for name in ("src", "poster", "data"):
            if tag != "script" and name in values and self._external(values[name]):
                self.problems.append(f"<{tag} {name}={values[name]!r}> is not embedded")
        if tag == "link" and "stylesheet" in (values.get("rel") or "") and self._external(values.get("href")):
            self.problems.append(f"<link rel=stylesheet href={values['href']!r}> is not embedded")
        if tag in ("img", "source") and values.get("srcset"):
            for candidate in values["srcset"].split(","):
                url = candidate.strip().split(" ")[0]
                if self._external(url):
                    self.problems.append(f"<{tag} srcset> entry {url!r} is not embedded")
        if values.get("style"):
            self._check_css(values["style"], f"style attribute of <{tag}>")
        if values.get("data-missing"):
            self.problems.append(f"image {values['data-missing']!r} was not found when the report was built")
        if values.get("data-cf-missing-ref"):
            self.problems.append(
                f"link [](#{values['data-cf-missing-ref']}) leads nowhere: no block has that key, "
                "or the block has no number or title to show (give the link text)"
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._check_css(data, "<style>")

    def _check_css(self, css: str, where: str) -> None:
        for match in _URL_IN_CSS.finditer(css):
            self.problems.append(f"{where}: url({match.group(2)!r}) is not embedded")
        for match in _IMPORT_IN_CSS.finditer(css):
            self.problems.append(f"{where}: @import {match.group(1)!r} is not embedded")


def check_html(html: str) -> list[str]:
    """Problems that would make the report depend on other files or the network."""
    collector = _Collector()
    collector.feed(html)
    collector.close()
    return collector.problems


def check_file(path: str | Path, *, limit: int = PREVIEW_LIMIT) -> list[str]:
    path = Path(path)
    problems = check_html(path.read_text(encoding="utf-8"))
    size = path.stat().st_size
    if size > limit:
        problems.append(f"{path} is {size / 1e6:.1f} MB: VS Code's preview opens files up to {limit / 1e6:.0f} MB")
    return problems
