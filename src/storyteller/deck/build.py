"""Builds a deck's .pptx from its document, with python-pptx.

A slide's blocks fill the placeholders of its template layout: the title, then one
content region per block (text, a figure, a table, metrics). Empty placeholders are
removed, so nothing reads "Click to add text" in PowerPoint. Colours are the theme's
own (accent 1, text 2), so a user's template restyles everything.

`build` also lists the slides in order, each with the part of the viewer it belongs
to and a hash of what it shows, which the renderer and the viewer go by.
"""

from __future__ import annotations

import hashlib
import io
import math
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from PIL import Image

from ..images import Asset
from ..render import node_hash
from . import fonts as deck_fonts
from . import template as templates

CONTENT = ("text", "figure", "table", "metrics")
STATUS_RGB = {"good": "3A9D4B", "warning": "D9822B", "critical": "D6453A"}
ERROR_RGB = "D6453A"
MONO = "Courier New"
MIN_TITLE_SCALE = 0.65  # a one-line title shrinks this far before it wraps
TABLE_SIZES = (16, 14, 12, 11, 10)
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


@dataclass
class Built:
    data: bytes
    width: int  # EMU
    height: int
    slides: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str | None = None
    line_break: bool = False


@dataclass
class Paragraph:
    runs: list[Run]
    level: int = 0
    bullet: str | None = None  # "bullet", "number" or None


_markdown = MarkdownIt("commonmark")


def paragraphs(text: str) -> list[Paragraph]:
    """Markdown as slide paragraphs: lists become bullet levels, emphasis and code become runs."""
    out: list[Paragraph] = []
    lists: list[str] = []
    for token in _markdown.parse(text):
        if token.type in ("bullet_list_open", "ordered_list_open"):
            lists.append("bullet" if token.type == "bullet_list_open" else "number")
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif token.type == "inline":
            out.append(Paragraph(_runs(token.children or []), max(len(lists) - 1, 0), lists[-1] if lists else None))
        elif token.type in ("fence", "code_block"):
            for line in token.content.rstrip("\n").split("\n"):
                out.append(Paragraph([Run(line, code=True)]))
    return out


def _runs(children: list[Any]) -> list[Run]:
    runs: list[Run] = []
    bold = italic = False
    link: str | None = None
    for child in children:
        kind = child.type
        if kind == "strong_open":
            bold = True
        elif kind == "strong_close":
            bold = False
        elif kind == "em_open":
            italic = True
        elif kind == "em_close":
            italic = False
        elif kind == "link_open":
            link = child.attrs.get("href")
        elif kind == "link_close":
            link = None
        elif kind == "code_inline":
            runs.append(Run(child.content, bold, italic, True, link))
        elif kind == "softbreak":
            runs.append(Run(" ", bold, italic, False, link))
        elif kind == "hardbreak":
            runs.append(Run("", line_break=True))
        elif kind in ("text", "html_inline") and child.content:
            runs.append(Run(child.content, bold, italic, False, link))
    return runs


def plain(text: str) -> str:
    """Markdown without its marks, for titles and the viewer's outline."""
    return " ".join("".join(run.text for run in p.runs) for p in paragraphs(text)).strip()


# -- the slide sequence ------------------------------------------------------------------------


def sequence(document: dict[str, Any], draft: bool) -> list[dict[str, Any]]:
    """Every slide of the deck in order: {part, node, section, kind} with kind "slide" or "head"
    (a section's header slide). Planned sections show as a header slide in drafts only."""
    out: list[dict[str, Any]] = []
    for node in document.get("nodes", []):
        if node["type"] == "slide":
            out.append({"part": f"slide:{node['key']}", "node": node, "section": None, "kind": "slide"})
        elif node["type"] == "section":
            status = node.get("status", "ready")
            if status == "pending" and not draft:
                continue
            if node.get("header", True) or status in ("pending", "error") or not node.get("children"):
                out.append({"part": f"head:{node['key']}", "node": node, "section": node, "kind": "head"})
            for child in node.get("children", []):
                if child["type"] == "slide":
                    out.append({"part": f"slide:{child['key']}", "node": child, "section": node, "kind": "slide"})
    return out


def slide_layout(node: dict[str, Any], kind: str, first: bool) -> str:
    """The storyteller layout a slide uses: its own, or one that fits its blocks."""
    if kind == "head":
        return "section"
    chosen = templates.resolve_layout(node.get("layout"))
    if chosen:
        return chosen
    count = sum(1 for child in node.get("children", []) if child["type"] in CONTENT)
    if count == 0:
        return "title" if first or node.get("subtitle") else "statement"
    return "content" if count == 1 else "two"


# -- building ---------------------------------------------------------------------------------------


def build(
    document: dict[str, Any],
    assets: Callable[[str], Asset],
    template: bytes,
    *,
    draft: bool,
    layouts: Mapping[str, str] | None = None,
    fonts: Sequence[Path] = (),
) -> Built:
    """The deck as .pptx bytes, with its slides listed. `fonts` are folders of the template's fonts."""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(template))
    heading, body = deck_fonts.theme_fonts(template)
    _drop_slides(prs)
    template_sha = hashlib.sha256(template).hexdigest()[:16]
    built = Built(b"", prs.slide_width, prs.slide_height)
    box = templates.content_box(prs)
    sections = 0
    for index, entry in enumerate(sequence(document, draft)):
        node, kind = entry["node"], entry["kind"]
        layout = slide_layout(node, kind, index == 0)
        mode = node.get("mode") or document.get("meta", {}).get("mode", "light")
        slide = prs.slides.add_slide(templates.choose(prs, layout, layouts, mode))
        filler = _Slide(prs, slide, assets, box, draft, (heading, body), list(fonts))
        title = node.get("title") or ""
        problem = None
        sections += kind == "head"
        try:
            filler.prepare()
            if kind == "head":
                filler.head(node, sections)
            else:
                filler.fill(node, layout)
        except Exception as error:  # one slide that cannot be built shows why; the deck still saves
            problem = f"{type(error).__name__}: {error}"
            filler._error(problem)
        filler.finish()
        built.slides.append(
            {
                "part": entry["part"],
                "key": node["key"],
                "kind": kind,
                "section": entry["section"]["key"] if entry["section"] else None,
                "title": title,
                "layout": layout,
                "problem": problem,
                "warnings": filler.warnings,
                "hash": node_hash(
                    {k: v for k, v in node.items() if k not in ("children", "updated")}
                    if kind == "head"
                    else {k: v for k, v in node.items() if k != "updated"},
                    template_sha,
                    layout,
                    mode,
                    draft,
                ),
            }
        )
    buffer = io.BytesIO()
    prs.save(buffer)
    built.data = buffer.getvalue()
    return built


def _drop_slides(prs) -> None:
    """A template that is a presentation keeps its masters and layouts, not its slides."""
    ids = prs.slides._sldIdLst
    for slide_id in list(ids):
        prs.part.drop_rel(slide_id.rId)
        ids.remove(slide_id)


class _Slide:
    def __init__(
        self,
        prs,
        slide,
        assets: Callable[[str], Asset],
        box: templates.Box,
        draft: bool,
        theme: tuple[str, str] = ("Arial", "Arial"),
        fonts: list[Path] | None = None,
    ) -> None:
        self.theme = theme
        self.fonts = fonts or []
        self.prs = prs
        self.slide = slide
        self.assets = assets
        self.box = box
        self.draft = draft
        self.warnings: list[str] = []  # what the agent should fix on this slide
        self.title = self.number = self.subtitle = self.real_subtitle = None
        self.regions: list[Any] = []

    def prepare(self) -> None:
        """Finds what the slide's placeholders are for (see templates.roles)."""
        prs, slide = self.prs, self.slide
        layout = slide.slide_layout
        prompts = {p.placeholder_format.idx: p.text_frame.text if p.has_text_frame else "" for p in layout.placeholders}
        _inherit_bodies(slide, layout)
        found = templates.roles(list(slide.placeholders), prompts, prs.slide_width, prs.slide_height)
        self.title = found.title
        self.number = found.number
        self.subtitle = found.subtitle
        self.real_subtitle = (
            found.subtitle if found.subtitle is not None and templates._kind(found.subtitle) == "SUBTITLE" else None
        )
        self.regions: list[Any] = found.regions

    # -- whole slides --

    def head(self, node: dict[str, Any], number: int) -> None:
        self._set_title(node.get("title", ""))
        if self.number is not None:
            self._text_into(self.number, f"{number:02d}")
        status = node.get("status", "ready")
        lines = node.get("description") or ""
        if status == "pending" and self.draft:
            lines = node.get("note") or "Planned: a later script writes this part."
        target = self.subtitle or (self.regions.pop(0) if self.regions else None)
        if lines and target is not None:
            self._text_into(target, lines)
        if status == "error" and self.draft:
            self._error(node.get("error") or "The script that writes this section failed.")
        self._notes(node.get("notes"))

    def fill(self, node: dict[str, Any], layout: str) -> None:
        self._set_title(node.get("title") or "")
        if layout == "statement":
            self._statement(node.get("title") or "")
        blocks = [child for child in node.get("children", []) if child["type"] in CONTENT]
        if node.get("subtitle"):
            # On a content slide only a real subtitle box will do: others are a template's sources or kickers.
            target = (
                (self.subtitle or (self.regions.pop(0) if self.regions else None))
                if layout in ("title", "section")
                else self.real_subtitle
            )
            if target is not None:
                self._text_into(target, node["subtitle"])
            else:
                blocks.insert(0, {"type": "text", "markdown": node["subtitle"]})
        boxes = self._boxes(len(blocks))
        for block, (placeholder, box) in zip(blocks, boxes, strict=True):
            self._block(block, placeholder, box)
        if node.get("status") == "error" and self.draft:
            self._error(node.get("error") or "The script that writes this slide failed.")
        self._notes(node.get("notes"))

    def finish(self) -> None:
        """Removes the placeholders nothing filled."""
        for placeholder in list(self.slide.placeholders):
            if placeholder.has_text_frame and not placeholder.text_frame.text.strip():
                placeholder._element.getparent().remove(placeholder._element)

    # -- regions --

    def _boxes(self, count: int) -> list[tuple[Any, templates.Box]]:
        """A (placeholder or None, box) per block: the layout's regions, else the content box split
        into columns."""
        if count == 0:
            return []
        if len(self.regions) >= count:
            chosen = sorted(self.regions[:count], key=lambda p: (p.left or 0, p.top or 0))
            return [(p, templates.Box(p.left, p.top, p.width, p.height)) for p in chosen]
        gap = round(0.4 * templates.EMU_PER_INCH)
        width = (self.box.w - gap * (count - 1)) // count
        return [
            (None, templates.Box(self.box.x + i * (width + gap), self.box.y, width, self.box.h)) for i in range(count)
        ]

    def _block(self, block: dict[str, Any], placeholder: Any, box: templates.Box) -> None:
        kind = block["type"]
        if kind == "text":
            if placeholder is not None and placeholder.has_text_frame:
                placeholder.fill.background()  # a template's grey well is for pictures
                self._text_into(placeholder, block["markdown"])
                body = placeholder.text_frame._txBody.find(f"{{{A}}}bodyPr")
                body.set("anchor", "t")
            else:
                shape = self.slide.shapes.add_textbox(box.x, box.y, box.w, box.h)
                shape.text_frame.word_wrap = True
                self._paragraphs(shape.text_frame, paragraphs(block["markdown"]))
            return
        self._remove(placeholder)
        if kind == "figure":
            self._figure(block, box)
        elif kind == "table":
            self._table(block, box)
        elif kind == "metrics":
            self._metrics(block, box)

    def _remove(self, placeholder: Any) -> None:
        if placeholder is not None:
            placeholder._element.getparent().remove(placeholder._element)

    # -- text --

    def _set_title(self, text: str) -> None:
        if self.title is not None and text:
            frame = self.title.text_frame
            frame.text = ""
            self._paragraphs(frame, [Paragraph(_runs_of(text))])
            self._fit_line(self.title, plain(text))

    def _fit_line(self, placeholder: Any, text: str) -> None:
        """A placeholder the template keeps on one line (``wrap="none"``) is widened to its text, up
        to the slide's margin. PowerPoint would run the line past the box and LibreOffice wraps it
        in the box: fitted, both show it as the template means. A longer line gets a smaller font,
        down to 65 %; past that it wraps downwards, and the slide is flagged to shorten it."""
        from pptx.util import Pt

        body = placeholder.text_frame._txBody.find(f"{{{A}}}bodyPr")
        if body is None or body.get("wrap") != "none":
            return
        family, size, bold = self._effective_font(placeholder)
        width_pt = deck_fonts.line_width(text, family, size, bold, self.fonts) or 0.55 * size * len(text)
        insets = int(body.get("lIns", 91440)) + int(body.get("rIns", 91440))
        needed = round(width_pt * 12700 * 1.04) + insets
        left, top, width, height = placeholder.left, placeholder.top, placeholder.width, placeholder.height
        if needed <= width:
            return
        widest = self.prs.slide_width - 2 * left
        placeholder.left, placeholder.top, placeholder.height = left, top, height
        placeholder.width = min(needed, widest)
        if needed <= widest:
            return
        scale = (widest - insets) / (needed - insets)
        smaller = Pt(max(size * max(scale, MIN_TITLE_SCALE) * 0.98, 8))
        for paragraph in placeholder.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = smaller
        if scale < MIN_TITLE_SCALE:
            body.set("wrap", "square")
            body.set("anchor", "t")  # grow downwards, not off the top of the slide
            self.warnings.append("the title is too long for this template's one-line titles: shorten it")

    def _effective_font(self, placeholder: Any) -> tuple[str, float, bool]:
        """The family, size (pt) and weight of a placeholder's first level, as it inherits them
        from its layout, its master and the master's text styles."""
        idx = placeholder.placeholder_format.idx
        kind = templates._kind(placeholder)
        is_title = kind in templates.TITLE_TYPES
        layout = self.slide.slide_layout
        chain = [placeholder]
        chain += [p for p in layout.placeholders if p.placeholder_format.idx == idx]
        chain += [p for p in layout.slide_master.placeholders if templates._kind(p) == kind]
        runs = []
        for shape in chain:
            styles = shape._element.find(f".//{{{A}}}lstStyle")
            if styles is not None:
                runs += styles.findall(f"{{{A}}}lvl1pPr/{{{A}}}defRPr")
        master = layout.slide_master._element.find(f".//{{{P}}}txStyles")
        if master is not None:
            style = master.find(f"{{{P}}}{'titleStyle' if is_title else 'bodyStyle'}")
            if style is not None:
                runs += style.findall(f"{{{A}}}lvl1pPr/{{{A}}}defRPr")
        size = next((int(r.get("sz")) / 100 for r in runs if r.get("sz")), 18.0)
        bold = next((r.get("b") in ("1", "true") for r in runs if r.get("b") is not None), False)
        family = next((latin.get("typeface") for r in runs if (latin := r.find(f"{{{A}}}latin")) is not None), None)
        heading, text = self.theme
        if family in (None, "+mj-lt"):
            family = heading if family == "+mj-lt" or is_title else text
        elif family == "+mn-lt":
            family = text
        return family, size, bold

    def _statement(self, text: str) -> None:
        """The slide's sentence, large, in the theme's heading font, centred in the content area;
        the title box stays empty (and goes)."""
        from pptx.util import Pt

        if self.title is not None:
            self.title.text_frame.text = ""
        box = self.box
        shape = self.slide.shapes.add_textbox(box.x, box.y, box.w, box.h)
        frame = shape.text_frame
        frame.word_wrap = True
        frame._txBody.find(f"{{{A}}}bodyPr").set("anchor", "ctr")
        self._paragraphs(frame, [Paragraph(_runs_of(text))])
        for paragraph in frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(40)
                run.font.bold = True
                run.font.name = "+mj-lt"

    def _text_into(self, placeholder: Any, text: str) -> None:
        frame = placeholder.text_frame
        frame.text = ""
        self._paragraphs(frame, paragraphs(text))

    def _paragraphs(self, frame, items: list[Paragraph]) -> None:
        """Fills a text frame with Markdown paragraphs."""
        first = True
        for item in items or [Paragraph([Run("")])]:
            paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
            first = False
            paragraph.level = min(item.level, 8)
            _bullet(paragraph, item.bullet, item.level)
            for run_spec in item.runs:
                if run_spec.line_break:
                    paragraph.add_line_break()
                    continue
                run = paragraph.add_run()
                run.text = run_spec.text
                font = run.font
                if run_spec.bold:
                    font.bold = True
                if run_spec.italic:
                    font.italic = True
                if run_spec.code:
                    font.name = MONO
                if run_spec.link:
                    run.hyperlink.address = run_spec.link

    # -- figures, tables, metrics --

    def _figure(self, block: dict[str, Any], box: templates.Box) -> None:
        from pptx.util import Pt

        asset = self.assets(block["asset"])
        data, width, height = _picture(asset)
        caption = block.get("caption")
        caption_h = round(0.45 * templates.EMU_PER_INCH) if caption else 0
        room_h = box.h - caption_h
        scale = min(box.w / width, room_h / height)
        w, h = round(width * scale), round(height * scale)
        x = box.x + (box.w - w) // 2
        y = box.y + (room_h - h) // 2
        picture = self.slide.shapes.add_picture(io.BytesIO(data), x, y, w, h)
        if block.get("alt"):
            picture._element.nvPicPr.cNvPr.set("descr", block["alt"])
        if caption:
            shape = self.slide.shapes.add_textbox(box.x, y + h + round(0.06 * templates.EMU_PER_INCH), box.w, caption_h)
            frame = shape.text_frame
            frame.word_wrap = True
            self._paragraphs(frame, [Paragraph(_runs_of(caption))])
            for paragraph in frame.paragraphs:
                paragraph.alignment = 2  # centre
                for run in paragraph.runs:
                    run.font.size = Pt(12)
                    _muted(run.font)

    def _table(self, block: dict[str, Any], box: templates.Box) -> None:
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Pt

        columns: list[str] = block["columns"]
        rows: list[list[str]] = block["rows"]
        numeric: list[bool] = block.get("numeric", [False] * len(columns))
        count = len(rows) + 1
        # The largest font at which every column fits its longest entry; columns as wide as those.
        padding = 0.2 * 72  # pt: the cell's left and right margins
        longest = [max([len(columns[c]), *(len(row[c]) for row in rows)], default=1) for c in range(len(columns))]
        box_pt = box.w / 12700
        sizes = [s for s in TABLE_SIZES if s <= (16 if count <= 7 else 14 if count <= 11 else 12)]
        size = next((s for s in sizes if sum(n * 0.55 * s + padding for n in longest) <= box_pt), sizes[-1])
        wanted = [n * 0.55 * size + padding for n in longest]
        if sum(wanted) > box_pt:
            self.warnings.append(
                f"the table's {len(columns)} columns do not fit their space: show fewer columns, shorter "
                "labels, or give the table a slide of its own"
            )
        row_h = min(round(size * 2.2 * 12700), box.h // count)
        caption = block.get("caption")
        caption_h = round(0.45 * templates.EMU_PER_INCH) if caption else 0
        height = min(row_h * count, box.h - caption_h)
        shape = self.slide.shapes.add_table(count, len(columns), box.x, box.y, box.w, height)
        table = shape.table
        total = sum(wanted)
        for c, column in enumerate(table.columns):
            column.width = round(box.w * wanted[c] / total)
        for c, name in enumerate(columns):
            _cell(table.cell(0, c), name, size, bold=True, align=PP_ALIGN.RIGHT if numeric[c] else None)
        for r, row in enumerate(rows, start=1):
            for c, value in enumerate(row):
                _cell(table.cell(r, c), value, size, align=PP_ALIGN.RIGHT if numeric[c] else None)
        if caption:
            text = self.slide.shapes.add_textbox(
                box.x, box.y + height + round(0.08 * templates.EMU_PER_INCH), box.w, caption_h
            )
            self._paragraphs(text.text_frame, [Paragraph(_runs_of(caption))])
            for run in text.text_frame.paragraphs[0].runs:
                run.font.size = Pt(12)
                _muted(run.font)

    def _metrics(self, block: dict[str, Any], box: templates.Box) -> None:
        from pptx.dml.color import RGBColor
        from pptx.util import Pt

        items = block["items"]
        columns = min(len(items), 4) or 1
        rows = math.ceil(len(items) / columns)
        gap = round(0.3 * templates.EMU_PER_INCH)
        cell_w = (box.w - gap * (columns - 1)) // columns
        cell_h = min((box.h - gap * (rows - 1)) // rows, round(2.2 * templates.EMU_PER_INCH))
        top = box.y + (box.h - (cell_h * rows + gap * (rows - 1))) // 2
        for index, item in enumerate(items):
            r, c = divmod(index, columns)
            x = box.x + c * (cell_w + gap)
            y = top + r * (cell_h + gap)
            shape = self.slide.shapes.add_textbox(x, y, cell_w, cell_h)
            frame = shape.text_frame
            frame.word_wrap = True
            value = frame.paragraphs[0].add_run()
            value.text = str(item["value"])
            value.font.size = Pt(54 if columns <= 3 else 44)
            value.font.bold = True
            if item.get("status") in STATUS_RGB:
                value.font.color.rgb = RGBColor.from_string(STATUS_RGB[item["status"]])
            else:
                _theme_color(value.font, "ACCENT_1")
            label = frame.add_paragraph().add_run()
            label.text = item["label"]
            label.font.size = Pt(20)
            if item.get("note"):
                note = frame.add_paragraph().add_run()
                note.text = item["note"]
                note.font.size = Pt(15)
                _muted(note.font)

    # -- marks and notes --

    def _error(self, message: str) -> None:
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Pt

        margin = round(0.4 * templates.EMU_PER_INCH)
        height = round(1.1 * templates.EMU_PER_INCH)
        shape = self.slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            margin,
            self.prs.slide_height - height - margin,
            self.prs.slide_width - 2 * margin,
            height,
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string("FDE4E1")
        shape.line.color.rgb = RGBColor.from_string(ERROR_RGB)
        frame = shape.text_frame
        frame.word_wrap = True
        head = frame.paragraphs[0].add_run()
        head.text = "This slide failed to update: "
        head.font.bold = True
        body = frame.paragraphs[0].add_run()
        body.text = message.strip().splitlines()[-1][:300]
        for run in (head, body):
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor.from_string("7A1F17")

    def _notes(self, notes: str | None) -> None:
        if notes:
            frame = self.slide.notes_slide.notes_text_frame
            frame.text = ""
            self._paragraphs(frame, paragraphs(notes))


def _inherit_bodies(slide, layout) -> None:
    """Spells out what each placeholder's text body inherits from its layout (wrapping, anchoring,
    insets, autofit). PowerPoint inherits them through the slide's empty ``<a:bodyPr/>``, but
    LibreOffice takes that as its defaults: a title the template keeps on one line would wrap."""
    import copy

    own = {p.placeholder_format.idx: p for p in layout.placeholders}
    for placeholder in slide.placeholders:
        source = own.get(placeholder.placeholder_format.idx)
        if source is None or not placeholder.has_text_frame or not source.has_text_frame:
            continue
        theirs = source.text_frame._txBody.find(f"{{{A}}}bodyPr")
        mine = placeholder.text_frame._txBody.find(f"{{{A}}}bodyPr")
        if theirs is None or mine is None:
            continue
        for name, value in theirs.attrib.items():
            if mine.get(name) is None:
                mine.set(name, value)
        if len(mine) == 0:
            for child in theirs:
                mine.append(copy.deepcopy(child))


def _runs_of(text: str) -> list[Run]:
    items = paragraphs(text)
    runs: list[Run] = []
    for index, item in enumerate(items):
        if index:
            runs.append(Run(" "))
        runs.extend(item.runs)
    return runs or [Run(text)]


def _bullet(paragraph, kind: str | None, level: int) -> None:
    """Bullet marks: none for plain paragraphs; list items get theirs, in the theme's first accent,
    whatever the template does at that level (some use level 1 for plain text)."""
    from lxml import etree

    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    props = paragraph._p.get_or_add_pPr()
    for name in ("buClr", "buNone", "buChar", "buAutoNum", "buFont"):
        for old in props.findall(f"{{{a}}}{name}"):
            props.remove(old)
    if kind is None:
        props.set("marL", "0")
        props.set("indent", "0")
        etree.SubElement(props, f"{{{a}}}buNone")
        return
    color = etree.SubElement(props, f"{{{a}}}buClr")
    etree.SubElement(color, f"{{{a}}}schemeClr", val="accent1")
    if kind == "number":
        etree.SubElement(props, f"{{{a}}}buAutoNum", type="arabicPeriod")
    else:
        etree.SubElement(props, f"{{{a}}}buChar", char="•")
    indent = 285750  # 0.3125 in
    props.set("marL", str(indent * (level + 1)))
    props.set("indent", str(-indent))


def _cell(cell, text: str, size: int, *, bold: bool = False, align=None) -> None:
    from pptx.util import Pt

    cell.text = ""
    paragraph = cell.text_frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    if bold:
        run.font.bold = True
    if align is not None:
        paragraph.alignment = align


def _muted(font) -> None:
    """Captions and notes: the theme's text colour, lighter."""
    _theme_color(font, "TEXT_1")
    font.color.brightness = 0.4


def _theme_color(font, name: str) -> None:
    from pptx.enum.dml import MSO_THEME_COLOR

    font.color.theme_color = getattr(MSO_THEME_COLOR, name)


def _picture(asset: Asset) -> tuple[bytes, int, int]:
    """Image bytes PowerPoint takes (PNG or JPEG), with the pixel size."""
    if asset.mime in ("image/png", "image/jpeg", "image/gif"):
        with Image.open(io.BytesIO(asset.data)) as image:
            return asset.data, image.width, image.height
    if asset.mime == "image/svg+xml":
        converter = shutil.which("rsvg-convert")
        if not converter:
            raise RuntimeError("an SVG figure needs rsvg-convert on a slide; pass a PNG or a matplotlib figure")
        result = subprocess.run(
            [converter, "--zoom", "2", "--format", "png"], input=asset.data, capture_output=True, check=True
        )
        data = result.stdout
    else:
        with Image.open(io.BytesIO(asset.data)) as image:
            image.load()
            buffer = io.BytesIO()
            lossless = image.getcolors(256) is not None
            if lossless or image.mode in ("RGBA", "LA", "P"):
                image.save(buffer, "PNG", optimize=True)
            else:
                image.convert("RGB").save(buffer, "JPEG", quality=90)
            data = buffer.getvalue()
    with Image.open(io.BytesIO(data)) as image:
        return data, image.width, image.height
