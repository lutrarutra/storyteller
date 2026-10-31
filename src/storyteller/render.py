"""Assembles the report's single HTML file from its document tree.

The document is plain JSON (see report.py): metadata plus a list of nodes, where
images and datasets are referenced by the SHA-256 of an asset. Rendering is a
pure function of the document and its assets, so the same input gives the same
bytes. Each top-level part of the page carries `data-cf-part` (a stable name) and
`data-cf-hash` (its content), which the runtime uses to patch a page in place
when the report changes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterator
from datetime import datetime
from functools import cache
from html import escape, unescape
from importlib import resources
from typing import Any, NamedTuple

from . import __version__
from .adapters import CARTESIAN_TRACES
from .comments import to_json as comments_json
from .images import Asset
from .markdown import ASSET_SCHEME, REF_ATTR
from .theme import palette
from .theme import plotly as plotly_theme

AssetLookup = Callable[[str], Asset]

CALLOUT_ICONS = {"note": "ℹ", "tip": "✦", "success": "✓", "warning": "!", "critical": "✕"}
CALLOUT_TITLES = {"note": "Note", "tip": "Tip", "success": "Done", "warning": "Warning", "critical": "Problem"}
STATUS_LABELS = {"pending": "planned", "running": "updating", "error": "failed"}
TOC_MARKS = {"pending": "○", "running": "◐", "error": "!"}

# Bumped whenever the HTML a node renders to changes, so part hashes change with it.
RENDER_VERSION = "2"

# Blocks that carry a number in their caption, and the word for it.
NUMBERED = {"figure": "Figure", "plotly": "Figure", "table": "Table"}
_REF = re.compile(rf'<a([^>]*?) {REF_ATTR}="([^"]*)"([^>]*)>(.*?)</a>', re.DOTALL)
_REF_KEY = re.compile(rf'{REF_ATTR}="([^"]*)"')
_HREF = re.compile(r' href="[^"]*"')


@cache
def asset_text(name: str) -> str:
    return resources.files("storyteller").joinpath("assets", name).read_text(encoding="utf-8")


@cache
def asset_bytes(name: str) -> bytes:
    return resources.files("storyteller").joinpath("assets", name).read_bytes()


def json_script(value: Any) -> str:
    """JSON that is safe inside a <script> element."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


def css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def node_hash(node: Any, *salt: Any) -> str:
    payload = json.dumps([RENDER_VERSION, node, *salt], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Target(NamedTuple):
    """Where a cross-reference to a key leads, and the text an empty `[](#key)` link gets."""

    node: dict[str, Any]
    id: str
    label: str  # "" when the block has nothing to call it by


class Context:
    """What renderers share: asset lookup, draft mode, the libraries the page needs, and
    the numbers and link targets of the blocks."""

    def __init__(self, assets: AssetLookup, *, draft: bool) -> None:
        self.assets = assets
        self.draft = draft
        self.libraries: set[str] = set()
        self.part = ""
        self._ids: dict[str, int] = {}
        self.numbers: dict[int, int] = {}  # id(node) -> its figure, table or section number
        self.targets: dict[str, Target] = {}
        self.referenced: set[str] = set()

    def uid(self, kind: str) -> str:
        """An id stable for this position in this part, so patched parts never collide."""
        count = self._ids.get(self.part + kind, 0)
        self._ids[self.part + kind] = count + 1
        digest = hashlib.sha1(f"{self.part}/{kind}/{count}".encode()).hexdigest()[:10]
        return f"cf-{kind}-{digest}"

    def uri(self, sha: str) -> str:
        return self.assets(sha).data_uri()

    def resolve_html(self, html: str) -> str:
        """Swaps `cfasset:<sha>` image references (from Markdown) for data URIs, and points
        cross-references at their blocks."""
        if REF_ATTR in html:
            html = _REF.sub(self._resolve_ref, html)
        if ASSET_SCHEME not in html:
            return html
        out, rest = [], html
        while (start := rest.find(ASSET_SCHEME)) >= 0:
            end = start + len(ASSET_SCHEME) + 64
            out.append(rest[:start])
            out.append(escape(self.uri(rest[start + len(ASSET_SCHEME) : end])))
            rest = rest[end:]
        out.append(rest)
        return "".join(out)

    def _resolve_ref(self, match: re.Match[str]) -> str:
        before, key, after, text = match.groups()
        target = self.targets.get(unescape(key))
        if target is None or not (text or target.label):
            # Not written yet (in a draft) or a typo: `check` reports it.
            return f'<a class="cf-ref cf-ref-missing" data-cf-missing-ref="{key}">{text or key + "?"}</a>'
        attrs = _HREF.sub("", before + after)
        return f'<a href="#{escape(target.id)}"{attrs} {REF_ATTR}="{key}">{text or escape(target.label)}</a>'

    def number(self, nodes: list[dict[str, Any]]) -> None:
        """Numbers figures, tables and top-level sections in reading order and collects the
        blocks that references can lead to: the first block with each key."""
        counts: dict[str, int] = {}
        sections = 0
        for node, depth in walk(nodes):
            kind = node["type"]
            label = ""
            if kind == "section" and depth == 0:
                sections += 1
                self.numbers[id(node)] = sections
                label = f"Section {sections}"
            elif kind == "section":
                label = node.get("title", "")
            elif kind in NUMBERED:
                word = NUMBERED[kind]
                counts[word] = counts.get(word, 0) + 1
                self.numbers[id(node)] = counts[word]
                label = f"{word} {counts[word]}"
            elif kind == "explorer":
                label = node["spec"].get("title") or ""
            elif kind in ("callout", "code", "tab"):
                label = node.get("title") or ""
            for html in (node.get("html"), node.get("caption_html")):
                if html and kind not in ("table", "code", "html"):
                    self.referenced.update(unescape(k) for k in _REF_KEY.findall(html))
            key = node.get("key")
            if key and key not in self.targets and kind != "tab":
                anchor = f"s-{key}" if kind == "section" else f"b-{key}"
                self.targets[key] = Target(node, anchor, label)

    def anchor(self, node: dict[str, Any]) -> str | None:
        """The id of this block's element, if references can lead to it."""
        target = self.targets.get(node.get("key") or "")
        return target.id if target and target.node is node else None

    def caption_label(self, node: dict[str, Any], has_caption: bool) -> str:
        """`Figure 3. ` before the caption: shown with a caption, or when text refers to the block."""
        number = self.numbers.get(id(node))
        if number is None or not (has_caption or node.get("key") in self.referenced):
            return ""
        return f'<span class="cf-caption-label">{NUMBERED[node["type"]]} {number}.</span> '

    def salt(self, node: dict[str, Any]) -> list[Any]:
        """The numbers and link targets a part shows, which can change when other parts do,
        so its hash changes with them and live updates re-render it."""
        out: list[Any] = []
        for child, _ in walk([node]):
            out.append([self.numbers.get(id(child)), child.get("key") in self.referenced])
            for html in (child.get("html"), child.get("caption_html")):
                if html and REF_ATTR in html:
                    for key in _REF_KEY.findall(html):
                        target = self.targets.get(unescape(key))
                        out.append([key, target and target.id, target and target.label])
        return out


def walk(nodes: list[dict[str, Any]], depth: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    """Every node in reading order with its section depth, into grids, tabs and sections."""
    for node in nodes:
        yield node, depth
        yield from walk(node.get("children", []), depth + 1 if node["type"] == "section" else depth)


# -- nodes ----------------------------------------------------------------------------


def render_nodes(nodes: list[dict[str, Any]], ctx: Context, depth: int) -> str:
    return "".join(render_node(node, ctx, depth) for node in nodes)


def render_node(node: dict[str, Any], ctx: Context, depth: int, attrs: str = "") -> str:
    renderer = RENDERERS.get(node["type"])
    if renderer is None:
        return f'<div class="cf-block cf-placeholder"{attrs}>Unknown block type {escape(node["type"])}</div>'
    anchor = ctx.anchor(node)
    if anchor and node["type"] != "section":  # sections set their own id
        attrs += f' id="{escape(anchor)}"'
    return renderer(node, ctx, depth, attrs)


def _caption(node: dict[str, Any], ctx: Context, tag: str = "figcaption") -> str:
    caption = ctx.resolve_html(node.get("caption_html") or "")
    label = ctx.caption_label(node, bool(caption))
    return f'<{tag} class="cf-caption">{label}{caption}</{tag}>' if label or caption else ""


def _section(node, ctx: Context, depth: int, attrs: str) -> str:
    status = node.get("status", "ready")
    heading = "h2" if depth == 0 else "h3"
    anchor = f"s-{node['key']}"
    head = ['<header class="cf-section-head">']
    if depth == 0:
        number = ctx.numbers.get(id(node))
        head.append(f'<span class="cf-section-number" aria-hidden="true">{f"{number:02d}" if number else ""}</span>')
    if status != "ready" and (ctx.draft or status == "error"):
        head.append(
            f'<span class="cf-pill cf-pill-{status}"><span class="cf-dot"></span>{STATUS_LABELS[status]}</span>'
        )
    head.append(f"<{heading}>{escape(node['title'])}</{heading}>")
    if node.get("description"):
        head.append(f'<p class="cf-section-description">{escape(node["description"])}</p>')
    head.append("</header>")
    body = render_nodes(node.get("children", []), ctx, depth + 1)
    if status == "pending":
        note = escape(node.get("note") or "This part of the analysis has not run yet.")
        body += f'<div class="cf-pending-body"><p>{note}</p><div class="cf-skeleton"></div><div class="cf-skeleton"></div></div>'
    elif status == "running" and not node.get("children"):
        note = escape(node.get("note") or "Running…")
        body += f'<div class="cf-pending-body"><p>{note}</p><div class="cf-skeleton"></div></div>'
    elif status == "error":
        message = escape(node.get("error") or "The script that writes this section failed.")
        body += (
            '<div class="cf-callout cf-callout-critical" role="alert"><span class="cf-callout-icon">✕</span>'
            f'<p class="cf-callout-title">This section failed to update</p><div class="cf-text"><pre>{message}</pre></div></div>'
        )
    source = ""
    if depth == 0 and node.get("source"):
        when = ""
        if ctx.draft and node.get("updated"):
            when = f" · updated {escape(_clock(node['updated']))}"
        source = f'<p class="cf-section-source">From <code>{escape(node["source"])}</code>{when}</p>'
    classes = f"cf-section cf-status-{status}"
    return f'<section class="{classes}" id="{escape(anchor)}"{attrs}>{"".join(head)}{body}{source}</section>'


def _text(node, ctx: Context, depth: int, attrs: str) -> str:
    return f'<div class="cf-block cf-text"{attrs}>{ctx.resolve_html(node["html"])}</div>'


def _summary(node, ctx: Context, depth: int, attrs: str) -> str:
    title = escape(node.get("title") or "Summary")
    return (
        f'<aside class="cf-block cf-summary"{attrs}><p class="cf-eyebrow">{title}</p>'
        f'<div class="cf-text">{ctx.resolve_html(node["html"])}</div></aside>'
    )


def _metrics(node, ctx: Context, depth: int, attrs: str) -> str:
    tiles = []
    for item in node["items"]:
        status = f' data-status="{escape(item["status"])}"' if item.get("status") else ""
        note = f'<span class="cf-metric-note">{escape(item["note"])}</span>' if item.get("note") else ""
        tiles.append(
            f'<div class="cf-metric"{status}><span class="cf-metric-value">{escape(item["value"])}</span>'
            f'<span class="cf-metric-label">{escape(item["label"])}</span>{note}</div>'
        )
    return f'<div class="cf-block cf-metrics"{attrs}>{"".join(tiles)}</div>'


def _figure(node, ctx: Context, depth: int, attrs: str) -> str:
    asset = ctx.assets(node["asset"])
    size = ""
    if asset.display_width and asset.display_height:
        size = f' width="{asset.display_width}" height="{asset.display_height}"'
    alt = escape(node.get("alt") or node.get("caption_text") or "")
    width = node.get("width", "normal")
    return (
        f'<figure class="cf-block cf-figure cf-figure-{escape(width)}"{attrs}>'
        f'<img src="{asset.data_uri()}" alt="{alt}"{size} loading="lazy" decoding="async">'
        f"{_caption(node, ctx)}</figure>"
    )


def _table(node, ctx: Context, depth: int, attrs: str) -> str:
    shape = node.get("shape", {})
    caption = ctx.resolve_html(node.get("caption_html") or "")
    if shape.get("rows", 0) > shape.get("shown", 0):
        more = f"Showing {shape['shown']:,} of {shape['rows']:,} rows."
        caption = f"{caption} {more}" if caption else more
    label = ctx.caption_label(node, bool(caption))
    caption_html = f'<p class="cf-caption">{label}{caption}</p>' if label or caption else ""
    return f'<div class="cf-block cf-table-block"{attrs}><div class="cf-table-wrap">{node["html"]}</div>{caption_html}</div>'


def _callout(node, ctx: Context, depth: int, attrs: str) -> str:
    kind = node.get("kind", "note")
    title = escape(node.get("title") or CALLOUT_TITLES.get(kind, kind.title()))
    role = ' role="alert"' if kind == "critical" else ""
    return (
        f'<div class="cf-block cf-callout cf-callout-{escape(kind)}"{role}{attrs}>'
        f'<span class="cf-callout-icon" aria-hidden="true">{CALLOUT_ICONS.get(kind, "•")}</span>'
        f'<p class="cf-callout-title">{title}</p><div class="cf-text">{ctx.resolve_html(node["html"])}</div></div>'
    )


def _code(node, ctx: Context, depth: int, attrs: str) -> str:
    code = node.get("html") or escape(node["code"])
    language = f' data-language="{escape(node["language"])}"' if node.get("language") else ""
    pre = f"<pre{language}><code>{code}</code></pre>"
    title = escape(node.get("title") or "Code")
    if node.get("collapsed"):
        return f'<details class="cf-block cf-code"{attrs}><summary>{title}</summary>{pre}</details>'
    heading = f'<p class="cf-callout-title">{title}</p>' if node.get("title") else ""
    return f'<div class="cf-block cf-code"{attrs}>{heading}{pre}</div>'


def _grid(node, ctx: Context, depth: int, attrs: str) -> str:
    columns = int(node.get("columns", 2))
    inner = render_nodes(node.get("children", []), ctx, depth)
    return f'<div class="cf-block cf-grid" style="--cf-columns: {columns}"{attrs}>{inner}</div>'


def _tabs(node, ctx: Context, depth: int, attrs: str) -> str:
    name = ctx.uid("tabs")
    tabs = node.get("children", [])
    radios, labels, panels = [], [], []
    for i, tab in enumerate(tabs):
        tab_id = f"{name}-{i}"
        checked = " checked" if i == 0 else ""
        radios.append(f'<input type="radio" name="{name}" id="{tab_id}"{checked}>')
        labels.append(f'<label for="{tab_id}">{escape(tab["title"])}</label>')
        panels.append(
            f'<div class="cf-tab-panel"><p class="cf-tab-print-title cf-callout-title">{escape(tab["title"])}</p>'
            f"{render_nodes(tab.get('children', []), ctx, depth)}</div>"
        )
    return (
        f'<div class="cf-block cf-tabs"{attrs}>{"".join(radios)}<div class="cf-tab-labels">{"".join(labels)}</div>'
        f'<div class="cf-tab-panels">{"".join(panels)}</div></div>'
    )


def _static_or_placeholder(node, ctx: Context, message: str) -> str:
    if node.get("static"):
        asset = ctx.assets(node["static"])
        size = ""
        if asset.display_width and asset.display_height:
            size = f' width="{asset.display_width}" height="{asset.display_height}"'
        alt = escape(node.get("caption_text") or "Static snapshot of an interactive chart")
        return f'<img src="{asset.data_uri()}" alt="{alt}"{size}>'
    return f'<div class="cf-placeholder">{escape(message)}</div>'


INTERACTIVE_NOTE = "Interactive chart: open the report in a browser (or with “Open interactive” in VS Code) to use it."


def _plotly(node, ctx: Context, depth: int, attrs: str) -> str:
    traces = set(node.get("traces", []))
    ctx.libraries.add("plotly-cartesian" if traces <= CARTESIAN_TRACES else "plotly-strict")
    spec_id = ctx.uid("plotly")
    spec = json.loads(ctx.assets(node["spec"]).data)
    height = int(node.get("height") or 420)
    static = _static_or_placeholder(node, ctx, INTERACTIVE_NOTE)
    width = node.get("width", "normal")
    return (
        f'<figure class="cf-block cf-figure cf-figure-{escape(width)} cf-plotly-block"{attrs}>'
        f'<div class="cf-static">{static}</div>'
        f'<div class="cf-live"><div class="cf-plotly-frame" data-cf-plotly data-cf-spec="{spec_id}" '
        f'data-height="{height}" style="min-height:{height}px"></div></div>'
        f'{_caption(node, ctx)}<script type="application/json" id="{spec_id}">{json_script(spec)}</script></figure>'
    )


def _explorer(node, ctx: Context, depth: int, attrs: str) -> str:
    ctx.libraries.add("plotly-cartesian")
    spec = node["spec"]
    spec_id = ctx.uid("explorer")
    data_id = ctx.uid("data")
    data = base64.b64encode(ctx.assets(node["data"]).data).decode("ascii")
    title = escape(spec.get("title") or "Explore the data")
    actions = (
        '<div class="cf-explorer-actions">'
        '<button type="button" class="cf-button cf-js-only" data-cf-action="reset">Reset</button>'
        '<button type="button" class="cf-button cf-js-only" data-cf-action="copy">Copy settings</button>'
        '<button type="button" class="cf-button cf-button-primary cf-host-only" data-cf-action="send">Send to chat</button>'
        "</div>"
    )
    static_stats = "".join(f'<p class="cf-stat">{escape(text)}</p>' for text in node.get("static_stats", []))
    static = (
        f'<div class="cf-static">{static_stats}<p class="cf-settings-line">Settings: {escape(node.get("settings_text", ""))}</p>'
        f"{_static_or_placeholder(node, ctx, INTERACTIVE_NOTE) if node.get('static') else ''}"
        f'<p class="cf-static-note">{escape(INTERACTIVE_NOTE)}</p></div>'
    )
    controls = "".join(_control(control, spec) for control in spec["controls"])
    views = []
    for index, view in enumerate(spec["views"]):
        if view["kind"] == "stat":
            views.append(f'<p class="cf-stat cf-view-full" data-cf-view="{index}"></p>')
        elif view["kind"] == "table":
            views.append(f'<div class="cf-view-full" data-cf-view="{index}"></div>')
        else:
            full = " cf-view-full" if view.get("full") or len(_chart_views(spec)) == 1 else ""
            views.append(f'<div class="cf-view-chart{full}" data-cf-view="{index}" data-height="300"></div>')
    stats = [v for v in views if 'class="cf-stat' in v]
    others = [v for v in views if 'class="cf-stat' not in v]
    live = (
        f'<div class="cf-live"><div class="cf-controls">{controls}</div>{"".join(stats)}'
        f'<p class="cf-settings-line" data-cf-settings></p><div class="cf-views">{"".join(others)}</div></div>'
    )
    return (
        f'<div class="cf-block cf-explorer" data-cf-explorer data-cf-spec="{spec_id}" data-cf-data="{data_id}"{attrs}>'
        f'<div class="cf-explorer-head"><span class="cf-explorer-badge">Interactive</span>'
        f'<h3 class="cf-explorer-title">{title}</h3>{actions}</div>{static}{live}{_caption(node, ctx, "p")}'
        f'<script type="application/json" id="{spec_id}">{json_script(spec)}</script>'
        f'<script type="application/octet-stream" id="{data_id}">{data}</script></div>'
    )


def _chart_views(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in spec["views"] if v["kind"] not in ("stat", "table")]


def _control(control: dict[str, Any], spec: dict[str, Any]) -> str:
    column = control["column"]
    label = escape(control.get("label") or column)
    meta = next((c for c in spec["columns"] if c["name"] == column), {})
    if control["kind"] == "threshold":
        low, high = meta.get("min", 0.0), meta.get("max", 1.0)
        step = control.get("step") or _step(low, high)
        rows = []
        for bound, op in (("min", "≥"), ("max", "≤")):
            value = control.get(bound)
            if value is None:
                continue
            common = f'data-cf-bound="{bound}" step="{step:g}" value="{value:g}" aria-label="{label} {op}"'
            rows.append(
                f'<div class="cf-bound"><span class="cf-bound-op">{op}</span>'
                f'<input type="range" min="{min(low, value):g}" max="{max(high, value):g}" {common}>'
                f'<input type="number" {common}></div>'
            )
        return f'<div class="cf-control" data-cf-column="{escape(column)}"><span class="cf-control-label">{label}</span>{"".join(rows)}</div>'
    if control["kind"] == "select":
        chosen = control.get("value")
        chosen = set([chosen] if isinstance(chosen, str) else chosen or [])
        multiple = control.get("multiple")
        options = [] if multiple else [f'<option value=""{"" if chosen else " selected"}>All</option>']
        for category in meta.get("categories", []):
            selected = " selected" if category in chosen else ""
            options.append(f'<option value="{escape(category)}"{selected}>{escape(category)}</option>')
        return (
            f'<label class="cf-control" data-cf-column="{escape(column)}"><span class="cf-control-label">{label}</span>'
            f'<select data-cf-select="{escape(column)}"{" multiple" if multiple else ""}>{"".join(options)}</select></label>'
        )
    checked = " checked" if control.get("value") else ""
    return (
        f'<label class="cf-control" data-cf-column="{escape(column)}"><span class="cf-control-label">{label}</span>'
        f'<span><input type="checkbox" data-cf-toggle="{escape(column)}"{checked}> only rows where it is true</span></label>'
    )


def _step(low: float, high: float) -> float:
    span = high - low
    if span <= 0:
        return 1.0
    import math

    return 10 ** math.floor(math.log10(span / 200))


def _html(node, ctx: Context, depth: int, attrs: str) -> str:
    for library in node.get("requires", []):
        ctx.libraries.add(library)
    return f'<div class="cf-block cf-html"{attrs}>{node["html"]}</div>'


def _provenance(node, ctx: Context, depth: int, attrs: str) -> str:
    rows = [
        ("Generated", node.get("generated", "")),
        ("Python", node.get("python", "")),
        ("Platform", node.get("platform", "")),
    ]
    packages = ", ".join(f"{name} {version}" for name, version in node.get("packages", {}).items())
    if packages:
        rows.append(("Packages", packages))
    scripts = node.get("scripts") or []
    if scripts:
        rows.append(("Scripts", ", ".join(scripts)))
    items = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows if v)
    return (
        f'<details class="cf-block cf-provenance cf-code"{attrs}><summary>Provenance: software and scripts</summary>'
        f"<dl>{items}</dl></details>"
    )


RENDERERS: dict[str, Callable[..., str]] = {
    "section": _section,
    "text": _text,
    "summary": _summary,
    "metrics": _metrics,
    "figure": _figure,
    "table": _table,
    "callout": _callout,
    "code": _code,
    "grid": _grid,
    "tabs": _tabs,
    "plotly": _plotly,
    "explorer": _explorer,
    "html": _html,
    "provenance": _provenance,
}

# -- page ---------------------------------------------------------------------------------


def _clock(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return iso


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
        return f"{moment.day} {moment:%B %Y}"
    except ValueError:
        return iso


def _font_faces() -> str:
    faces = [
        ("DM Sans", "dm-sans-latin-wght-normal.woff2", "normal", "100 1000"),
        ("DM Sans", "dm-sans-latin-wght-italic.woff2", "italic", "100 1000"),
        ("Fredoka", "fredoka-latin-wght-normal.woff2", "normal", "300 700"),
    ]
    rules = []
    for family, file, style, weight in faces:
        data = base64.b64encode(asset_bytes(f"fonts/{file}")).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
        )
    return "".join(rules)


def _theme_css(meta: dict[str, Any]) -> str:
    light, dark = palette.css_variables("light"), palette.css_variables("dark")
    page = meta.get("page", "A4")
    title = css_string(meta.get("title", ""))
    return (
        f".cf-report{{{light}}}"
        f"@media (prefers-color-scheme: dark){{.cf-report:not([data-theme=light]){{{dark}}}}}"
        f".cf-report[data-theme=dark]{{{dark}}}"
        f"@media print{{.cf-report,.cf-report[data-theme=dark]{{{light}}}}}"
        f"@page{{size:{page};margin:16mm 14mm 18mm;"
        f"@bottom-left{{content:{title};font:8pt 'DM Sans',sans-serif;color:#6b5d45}}"
        f"@bottom-right{{content:counter(page) ' / ' counter(pages);font:8pt 'DM Sans',sans-serif;color:#6b5d45}}}}"
    )


def theme_json() -> dict[str, Any]:
    out = {}
    for mode in ("light", "dark"):
        out[mode] = {
            "plotly": plotly_theme.layout(mode),
            "categorical": palette.categorical(mode=mode),
            "neutral": palette.NEUTRAL[mode],
            "pass": palette.CATEGORICAL[mode][0],
            "ink": palette.TOKENS[mode]["ink"],
        }
    return out


LIBRARIES = {
    "plotly-cartesian": "vendor/plotly-cartesian.min.js",
    "plotly-strict": "vendor/plotly-strict.min.js",
    "d3": "vendor/d3.min.js",
}


def _with_part(name: str, digest: str) -> str:
    return f' data-cf-part="{escape(name)}" data-cf-hash="{digest}"'


def _header(meta: dict[str, Any], draft: bool, sections: list[dict[str, Any]]) -> str:
    pills = []
    if meta.get("date"):
        pills.append(f'<li class="cf-pill">{escape(_date(meta["date"]))}</li>')
    if meta.get("author"):
        pills.append(f'<li class="cf-pill">{escape(meta["author"])}</li>')
    written_by = " · ".join(meta[name] for name in ("agent", "model") if meta.get(name))
    if written_by:
        pills.append(
            f'<li class="cf-pill cf-pill-model" title="Agent and model that wrote the report">{escape(written_by)}</li>'
        )
    if draft:
        counts = {
            status: sum(1 for s in sections if s.get("status") == status) for status in ("pending", "running", "error")
        }
        detail = ", ".join(f"{n} {STATUS_LABELS[status]}" for status, n in counts.items() if n)
        updated = f" · updated {_clock(meta['updated'])}" if meta.get("updated") else ""
        pills.append(
            f'<li class="cf-pill cf-pill-draft">Draft{escape(updated)}{escape(" · " + detail) if detail else ""}</li>'
        )
    else:
        pills.append('<li class="cf-pill cf-pill-final">Final</li>')
    subtitle = f'<p class="cf-subtitle">{escape(meta["subtitle"])}</p>' if meta.get("subtitle") else ""
    toolbar = (
        '<div class="cf-toolbar">'
        '<button type="button" class="cf-button cf-js-only" data-cf-action="theme" '
        'title="Toggle light/dark theme (follows system preference by default)">'
        '<span class="cf-theme-icon" aria-hidden="true"></span>Theme</button>'
        '<button type="button" class="cf-button cf-js-only" data-cf-action="print" title="Print or save as PDF">Print / PDF</button>'
        "</div>"
    )
    eyebrow = escape(meta.get("eyebrow") or "code flower · storyteller report")
    html = (
        f'<span class="cf-logo">{asset_text("logo.svg")}</span><p class="cf-eyebrow">{eyebrow}</p>'
        f'<h1 class="cf-title">{escape(meta.get("title", "Report"))}</h1>{subtitle}{toolbar}'
        f'<ul class="cf-meta">{"".join(pills)}</ul>'
    )
    return f'<header class="cf-header"{_with_part("header", node_hash(html))}>{html}</header>'


def _toc(sections: list[dict[str, Any]], draft: bool) -> str:
    if not sections:
        return '<nav class="cf-toc" aria-label="Contents" data-cf-part="toc" data-cf-hash="empty"></nav>'
    items = []
    for number, section in enumerate(sections, 1):
        status = section.get("status", "ready")
        mark = (
            f'<span class="cf-status" title="{STATUS_LABELS[status]}">{TOC_MARKS[status]}</span>'
            if draft and status in TOC_MARKS
            else ""
        )
        items.append(
            f'<li><a href="#s-{escape(section["key"])}"><span class="cf-number">{number:02d}</span>'
            f"<span>{escape(section['title'])}</span>{mark}</a></li>"
        )
    html = f'<p class="cf-toc-title">Contents</p><ol>{"".join(items)}</ol>'
    return f'<nav class="cf-toc" aria-label="Contents"{_with_part("toc", node_hash(html))}>{html}</nav>'


def render_document(
    document: dict[str, Any],
    assets: AssetLookup,
    *,
    draft: bool | None = None,
    live_url: str | None = None,
    fonts: bool = True,
    comments: list[dict[str, Any]] | None = None,
) -> str:
    """The report as one self-contained HTML page, with the readers' `comments` (see comments.py)."""
    meta = document.get("meta", {})
    nodes = document.get("nodes", [])
    draft = (not meta.get("final", False)) if draft is None else draft
    ctx = Context(assets, draft=draft)
    sections = [node for node in nodes if node["type"] == "section"]
    visible = [node for node in nodes if draft or node.get("status") != "pending"]
    ctx.number(visible)

    parts = []
    for node in visible:
        part = f"{node['type']}:{node['key']}"
        ctx.part = part
        parts.append(render_node(node, ctx, 0, _with_part(part, node_hash(node, draft, ctx.salt(node)))))
    toc_sections = [s for s in sections if draft or s.get("status") != "pending"]
    footer_html = (
        f"{asset_text('logo-mono.svg')}<span>Made with code flower · storyteller {escape(__version__)}</span>"
        '<a class="cf-github" href="https://github.com/lutrarutra/storyteller" target="_blank" '
        'rel="noopener noreferrer" aria-label="Storyteller on GitHub">'
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<path fill="currentColor" d="M12 .5C5.65.5.5 5.65.5 12c0 5.09 3.29 9.41 7.86 10.94.58.11.79-.25.79-.56v-2.02c-3.2.7-3.87-1.36-3.87-1.36-.53-1.33-1.28-1.68-1.28-1.68-1.04-.7.08-.69.08-.69 1.15.08 1.75 1.19 1.75 1.19 1.02 1.75 2.67 1.25 3.32.95.1-.74.4-1.25.72-1.54-2.55-.29-5.24-1.29-5.24-5.72 0-1.26.45-2.29 1.2-3.1-.12-.29-.52-1.47.11-3.07 0 0 .98-.31 3.2 1.19a10.9 10.9 0 0 1 5.83 0c2.22-1.5 3.2-1.19 3.2-1.19.63 1.6.23 2.78.11 3.07.75.81 1.2 1.84 1.2 3.1 0 4.44-2.7 5.42-5.27 5.71.41.36.77 1.04.77 2.1v3.11c0 .31.21.67.8.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z"/>'
        "</svg></a>"
    )
    libraries = sorted(ctx.libraries)
    if "plotly-strict" in libraries and "plotly-cartesian" in libraries:
        libraries.remove("plotly-cartesian")
    scripts = "".join(
        f'<script data-cf-lib="{name}">{asset_text(LIBRARIES[name])}</script>'
        for name in libraries
        if name in LIBRARIES
    )
    live = f'<meta name="cf-live" content="{escape(live_url)}">' if live_url else ""
    title = escape(meta.get("title", "Report"))
    favicon = base64.b64encode(asset_bytes("logo.svg")).decode("ascii")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f'<meta name="generator" content="storyteller {escape(__version__)}">{live}'
        f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon}">'
        f"<title>{title}</title>"
        f"<style>{_font_faces() if fonts else ''}</style>"
        f"<style>{_theme_css(meta)}{asset_text('report.css')}</style></head>"
        '<body><div class="cf-report">'
        f'<div class="cf-layout">{_toc(toc_sections, draft)}<main class="cf-sheet">'
        f"{_header(meta, draft, sections)}"
        f'<div data-cf-slot="main">{"".join(parts)}</div>'
        f'<footer class="cf-footer"{_with_part("footer", node_hash(footer_html))}>{footer_html}</footer>'
        "</main><div></div></div>"
        # Before the runtime: comments.js keeps a copy of the page as built, for "Save copy".
        f'<script type="application/json" id="cf-comments">{comments_json(comments or [])}</script>'
        f"<script>{asset_text('comments.js')}</script>"
        f'<script type="application/json" id="cf-theme">{json_script(theme_json())}</script>'
        f"{scripts}<script>{asset_text('runtime.js')}</script>"
        "</div></body></html>"
    )
