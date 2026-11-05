"""The deck's viewer: a storyteller page with each slide as LibreOffice drew it.

It is built like a report page (the same head, theme, comments and runtime), so code
flower's VS Code tab and `storyteller serve` follow it the same way: each slide is a
part (`data-cf-part="slide:<key>"`) whose hash changes only when that slide does, so
an update swaps and flashes just the slides that changed. A section is a part too,
whose hash covers its heading only; its slides sit in its own slot.

Until LibreOffice has drawn a slide, the page shows its outline, and a slide whose
picture is older than its content keeps the old picture with an "updating" mark.
`<meta name="cf-deck">` names the .pptx beside the page.
"""

from __future__ import annotations

import base64
from html import escape
from typing import Any

from .. import __version__, markdown
from .. import render as page
from ..comments import to_json as comments_json
from . import build

UPDATING = "Updating…"
DRAWING = "Drawing…"


def _pill(status: str) -> str:
    if status == "ready":
        return ""
    return f'<span class="cf-pill cf-pill-{status}"><span class="cf-dot"></span>{page.STATUS_LABELS[status]}</span>'


def _wireframe(entry: dict[str, Any], node: dict[str, Any]) -> str:
    """The slide's text, until LibreOffice has drawn it."""
    lines = []
    for child in node.get("children", []) if entry["kind"] == "slide" else []:
        if child["type"] == "text":
            lines.extend(f"<li>{escape(build.plain(p))}</li>" for p in child["markdown"].split("\n") if p.strip())
        elif child["type"] in ("figure", "table", "metrics"):
            lines.append(f'<li class="cf-slide-wire-block">{child["type"]}</li>')
    subtitle = node.get("subtitle") or node.get("description") or ""
    return (
        '<div class="cf-slide-wire">'
        f'<p class="cf-slide-wire-title">{escape(entry["title"] or "Untitled")}</p>'
        f"{f'<p class=cf-slide-wire-sub>{escape(build.plain(subtitle))}</p>' if subtitle else ''}"
        f"{f'<ul>{chr(10).join(lines[:8])}</ul>' if lines else ''}"
        "</div>"
    )


def _slide(
    entry: dict[str, Any],
    node: dict[str, Any],
    number: int,
    drawn: dict[str, Any] | None,
    image: str | None,
    ratio: str,
    draft: bool,
) -> str:
    fresh = drawn is not None and drawn.get("hash") == entry["hash"]
    status = node.get("status", "ready") if entry["kind"] == "slide" else "ready"
    title = entry["title"] or ("Section" if entry["kind"] == "head" else "Untitled")
    if image:
        alt = f"Slide {number}: {title}"
        frame = f'<img class="cf-slide-image" src="{image}" alt="{escape(alt)}">'
    else:
        frame = _wireframe(entry, node)
    badge = ""
    if draft and not fresh:
        badge = f'<span class="cf-slide-badge">{UPDATING if image else DRAWING}</span>'
    error = ""
    if node.get("status") == "error" and (draft or entry["kind"] == "slide"):
        message = escape(node.get("error") or "The script that writes this slide failed.")
        error = (
            '<div class="cf-callout cf-callout-critical" role="alert"><span class="cf-callout-icon">✕</span>'
            f'<p class="cf-callout-title">This slide failed to update</p><div class="cf-text"><pre>{message}</pre></div></div>'
        )
    if draft and (entry.get("warnings") or entry.get("problem")):
        items = [*entry.get("warnings", []), *([entry["problem"]] if entry.get("problem") else [])]
        error += "".join(f'<p class="cf-slide-warning">{escape(item)}</p>' for item in items)
    notes = ""
    if node.get("notes"):
        notes = (
            '<details class="cf-slide-notes"><summary>Speaker notes</summary>'
            f'<div class="cf-text">{markdown.render(node["notes"])}</div></details>'
        )
    kind = "head" if entry["kind"] == "head" else "slide"
    key = escape(node["key"])
    bar = (
        f'<div class="cf-slide-bar"><span class="cf-slide-number">{number:02d}</span>'
        f'<span class="cf-slide-title">{escape(title)}</span>{_pill(status) if draft else ""}'
        f'<code class="cf-slide-key" title="The key a script writes this slide with">{"§" if kind == "head" else "#"}{key}</code></div>'
    )
    body = f'{bar}<div class="cf-slide-frame" style="aspect-ratio:{ratio}">{frame}{badge}</div>{error}{notes}'
    digest = page.node_hash([entry["hash"], number, image and image[-64:], fresh, draft])
    flash = " data-cf-flash" if fresh else ""
    anchor = f"h-{key}" if kind == "head" else f"s-{key}"
    return (
        f'<article class="cf-slide cf-slide-{kind}{"" if fresh else " cf-slide-stale"}" id="{anchor}"'
        f"{page._with_part(entry['part'], digest)}{flash}>{body}</article>"
    )


def _section_head(node: dict[str, Any], draft: bool) -> str:
    """A label over the section's slides: its header slide already shows the title large."""
    status = node.get("status", "ready")
    source = f" · from <code>{escape(node['source'])}</code>" if node.get("source") else ""
    return (
        '<header class="cf-deck-section-head">'
        f'<h2 class="cf-deck-section-title">{escape(node.get("title", ""))}</h2>'
        f'{_pill(status) if draft else ""}<span class="cf-deck-section-source">{source[3:] if source else ""}</span>'
        "</header>"
    )


DOWNLOAD_ICON = (
    '<svg class="cf-button-icon" viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="M8 2.5v8m-3.5-3.5L8 10.5 11.5 7M3 13.5h10"/></svg>'
)


def _download(deck_file: str) -> str:
    """The toolbar's download button: a link to the .pptx beside the page, which a host page
    (code flower's) downloads instead (see runtime.js), and hides when it cannot."""
    return (
        f'<a class="cf-button" href="{escape(deck_file)}" download data-cf-action="download" '
        f'data-cf-file="{escape(deck_file)}" title="Download the slides ({escape(deck_file)})">'
        f"{DOWNLOAD_ICON}Slides</a>"
    )


def _page_rule(built: dict[str, Any]) -> str:
    """Printed pages the size of the slides, without margins (after the report's A4 rule)."""
    width, height = built.get("width"), built.get("height")
    if not width or not height:
        return ""
    size = f"{width / 914400:.3f}in {height / 914400:.3f}in"
    return f"@page{{size:{size};margin:0;@bottom-left{{content:none}}@bottom-right{{content:none}}}}"


def render_page(
    document: dict[str, Any],
    built: dict[str, Any],
    drawn: dict[str, Any],
    images: dict[str, str],
    *,
    deck_file: str,
    comments: list[dict[str, Any]] | None = None,
    draft: bool | None = None,
) -> str:
    """The viewer for `document`, built as `built` (the slides of the last build) and drawn as
    `drawn` (the last render), with `images` the data URIs of the drawn slides by part."""
    meta = {**document.get("meta", {})}
    draft = (not meta.get("final", False)) if draft is None else draft
    meta.setdefault("eyebrow", "code flower · storyteller deck")
    ratio = f"{built.get('width', 16)}/{built.get('height', 9)}"
    drawn_by_part = {slide["part"]: slide for slide in drawn.get("slides", [])}
    by_key: dict[str, dict[str, Any]] = {}
    for node, _ in page.walk(document.get("nodes", [])):
        if node.get("key"):
            by_key.setdefault(node["key"], node)

    def slide_html(entry: dict[str, Any], number: int) -> str:
        node = by_key.get(entry["key"], {"key": entry["key"]})
        return _slide(entry, node, number, drawn_by_part.get(entry["part"]), images.get(entry["part"]), ratio, draft)

    parts: list[str] = []
    toc: list[dict[str, Any]] = []
    number = 0
    slides = built.get("slides", [])
    index = 0
    while index < len(slides):
        entry = slides[index]
        if entry["section"] is None:
            number += 1
            parts.append(slide_html(entry, number))
            toc.append({"key": entry["key"], "title": entry["title"] or f"Slide {number}", "status": "ready"})
            index += 1
            continue
        section_key = entry["section"]
        section = by_key.get(section_key, {"key": section_key, "title": section_key})
        inner = []
        while index < len(slides) and slides[index]["section"] == section_key:
            number += 1
            inner.append(slide_html(slides[index], number))
            index += 1
        head = _section_head(section, draft)
        digest = page.node_hash([head, draft])
        parts.append(
            f'<section class="cf-section cf-deck-section" id="s-{escape(section_key)}"'
            f"{page._with_part(f'section:{section_key}', digest)}>{head}"
            f'<div class="cf-slides" data-cf-slot="section:{escape(section_key)}">{"".join(inner)}</div></section>'
        )
        toc.append(
            {"key": section_key, "title": section.get("title", section_key), "status": section.get("status", "ready")}
        )

    bar_items = [
        f"{number} slide{'s' if number != 1 else ''}",
        f"<code>{escape(deck_file)}</code>",
    ]
    if drawn.get("error"):
        bar_items.append(f'<span class="cf-deck-error">Slides not drawn: {escape(drawn["error"])}</span>')
    elif drawn.get("rendered") and draft:
        bar_items.append(f"drawn {escape(page._clock(drawn['rendered']))}")
    deck_bar = f'<p class="cf-deck-bar">{" · ".join(bar_items)}</p>'
    deck_bar = f'<div class="cf-deck-info"{page._with_part("deck", page.node_hash(deck_bar))}>{deck_bar}</div>'

    sections = [n for n in document.get("nodes", []) if n["type"] == "section"]
    favicon = base64.b64encode(page.asset_bytes("logo.svg")).decode("ascii")
    title = escape(meta.get("title", "Deck"))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f'<meta name="generator" content="storyteller {escape(__version__)}">'
        f'<meta name="cf-deck" content="{escape(deck_file)}">'
        f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon}">'
        f"<title>{title}</title>"
        f"<style>{page._font_faces()}</style>"
        f"<style>{page._theme_css(meta)}{page.asset_text('report.css')}{page.asset_text('deck.css')}"
        f"{_page_rule(built)}</style></head>"
        '<body><div class="cf-report cf-deck">'
        f'<div class="cf-layout">{page._toc(toc, draft)}<main class="cf-sheet">'
        f"{page._header(meta, draft, sections, _download(deck_file))}"
        f"{deck_bar}"
        f'<div data-cf-slot="main">{"".join(parts)}</div>'
        f'<footer class="cf-footer" data-cf-part="footer" data-cf-hash="deck">'
        f"{page.asset_text('logo-mono.svg')}<span>Made with code flower · storyteller {escape(__version__)}</span></footer>"
        "</main><div></div></div>"
        f'<script type="application/json" id="cf-comments">{comments_json(comments or [])}</script>'
        f"<script>{page.asset_text('comments.js')}</script>"
        f'<script type="application/json" id="cf-theme">{page.json_script(page.theme_json())}</script>'
        f"<script>{page.asset_text('runtime.js')}</script>"
        "</div></body></html>"
    )
