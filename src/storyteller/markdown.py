"""Markdown for text blocks: CommonMark with tables, raw HTML disabled, local images embedded."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt

# An image source that names an asset; render.py swaps it for the asset's data URI.
ASSET_SCHEME = "cfasset:"
# The attribute that marks a `[](#key)` link as a cross-reference to the block with that key.
REF_ATTR = "data-cf-ref"


def _parser(allow_html: bool) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": allow_html, "typographer": True}).enable(["table", "strikethrough"])
    default_heading = md.renderer.rules.get("heading_open")

    def heading_open(self, tokens, idx, options, env):
        # Text sits inside a section (h2/h3), so its own headings start at h4.
        token = tokens[idx]
        level = min(6, int(token.tag[1]) + env.get("heading_shift", 3))
        token.tag = tokens[idx + 2].tag = f"h{level}"  # open, inline, close
        if default_heading:
            return default_heading(tokens, idx, options, env)
        return md.renderer.renderToken(tokens, idx, options, env)

    def link_open(self, tokens, idx, options, env):
        token = tokens[idx]
        href = token.attrGet("href") or ""
        if href.startswith(("http://", "https://")):
            token.attrSet("target", "_blank")
            token.attrSet("rel", "noopener noreferrer")
        elif href.startswith("#") and len(href) > 1:
            # A cross-reference to a block's key; render.py points it at the block and,
            # when the link has no text, fills in the block's label ("Figure 3").
            token.attrSet("class", "cf-ref")
            token.attrSet(REF_ATTR, unquote(href[1:]))
        return md.renderer.renderToken(tokens, idx, options, env)

    md.add_render_rule("heading_open", heading_open)
    md.add_render_rule("link_open", link_open)
    return md


_PARSERS = {flag: _parser(flag) for flag in (False, True)}


def render(
    text: str,
    *,
    base_dir: Path | None = None,
    embed: Callable[[Path], str] | None = None,
    allow_html: bool = False,
    heading_shift: int = 3,
) -> str:
    """HTML for `text`. Local image paths (relative to `base_dir`) are passed to `embed`, which
    returns the asset reference to put in their place; missing images are left as their alt text."""
    md = _PARSERS[allow_html]
    env = {"heading_shift": heading_shift}
    tokens = md.parse(text, env)
    for token in tokens:
        for child in token.children or ():
            if child.type != "image":
                continue
            source = child.attrGet("src") or ""
            if source.startswith(("data:", ASSET_SCHEME, "http://", "https://", "//")) or embed is None:
                continue
            path = Path(source)
            if not path.is_absolute() and base_dir is not None:
                path = base_dir / path
            if path.is_file():
                child.attrSet("src", embed(path))
            else:
                child.attrSet("src", "")
                child.attrSet("data-missing", source)
    return md.renderer.render(tokens, md.options, env)


def render_inline(text: str) -> str:
    """Inline Markdown (emphasis, code, links) for captions and labels, without a paragraph."""
    return _PARSERS[False].renderInline(text)
