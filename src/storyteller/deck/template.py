"""Slide templates: code flower's default, the user's own, and which of a template's layouts a slide uses.

The default is made from python-pptx's built-in template on first use: 16:9, the
report's colours in the theme, Arial (Liberation Sans in the sandbox has the same
metrics, so text that fits here fits in PowerPoint). Nothing binary is kept in the
repository.

Slides name a storyteller layout (`title`, `section`, `content`, `two`, `statement`,
`blank`), which maps to a template layout by its standard PowerPoint name, else by
the placeholders it has, so most templates work without configuration.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from lxml import etree

from ..theme import palette

EMU_PER_INCH = 914400
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

# A storyteller layout: template layout names to try, then the placeholders a layout needs.
LAYOUT_NAMES: dict[str, tuple[str, ...]] = {
    "title": ("Title Slide",),
    "section": ("Section Header",),
    "content": ("Title and Content",),
    "two": ("Two Content",),
    "statement": ("Title Only",),
    "blank": ("Blank",),
}
# Names agents reach for; they pick the layout with the regions the blocks need.
ALIASES = {
    "bullets": "content",
    "figure": "content",
    "table": "content",
    "chart": "content",
    "metrics": "content",
    "figure-text": "two",
    "columns": "two",
    "quote": "statement",
    "closing": "title",
}
LAYOUTS = tuple(LAYOUT_NAMES)
LAYOUT_NAMES_ALL = {name for names in LAYOUT_NAMES.values() for name in names}
MODES = ("light", "dark")

TEMPLATE_SUFFIXES = (".pptx", ".potx")
DEFAULT_NAMES = ("codeflower", "code flower", "default")
POTX_TYPE = b"application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
PPTX_TYPE = b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"


def resolve_layout(name: str | None) -> str | None:
    if name is None:
        return None
    name = ALIASES.get(name, name)
    if name not in LAYOUT_NAMES:
        choices = ", ".join(LAYOUTS)
        raise ValueError(f"unknown slide layout {name!r}: use one of {choices} (or leave it out)")
    return name


# -- the default template ------------------------------------------------------------------


def _inches(value: float) -> str:
    return str(round(value * EMU_PER_INCH))


def _hex(color: str) -> str:
    return color.lstrip("#").upper()


def _set_xfrm(shape: etree._Element, x: float, y: float, w: float, h: float) -> None:
    """Gives a placeholder on a master or layout its own position (in inches)."""
    sp_pr = shape.find("p:spPr", NS)
    for old in sp_pr.findall("a:xfrm", NS):
        sp_pr.remove(old)
    xfrm = etree.Element(f"{{{NS['a']}}}xfrm")
    etree.SubElement(xfrm, f"{{{NS['a']}}}off", x=_inches(x), y=_inches(y))
    etree.SubElement(xfrm, f"{{{NS['a']}}}ext", cx=_inches(w), cy=_inches(h))
    sp_pr.insert(0, xfrm)


def _placeholders(tree: etree._Element) -> list[tuple[etree._Element, str, str]]:
    """(shape, type, idx) for each placeholder of a master or layout."""
    out = []
    for shape in tree.iterfind(".//p:sp", NS):
        ph = shape.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is not None:
            out.append((shape, ph.get("type", "obj"), ph.get("idx", "0")))
    return out


# 13.33 × 7.5 in, with 0.6 in margins.
TITLE_BOX = (0.6, 0.55, 12.13, 0.95)
BODY_BOX = (0.6, 1.7, 12.13, 5.05)
FOOTERS = {"dt": (0.6, 6.95, 3.0, 0.35), "ftr": (4.5, 6.95, 4.33, 0.35), "sldNum": (9.73, 6.95, 3.0, 0.35)}
GEOMETRY: dict[str, dict[tuple[str, str], tuple[float, float, float, float]]] = {
    "master": {("title", "0"): TITLE_BOX, ("body", "1"): BODY_BOX},
    "Title Slide": {("ctrTitle", "0"): (0.9, 2.35, 11.5, 1.55), ("subTitle", "1"): (0.9, 4.0, 11.5, 1.2)},
    "Title and Content": {("title", "0"): TITLE_BOX, ("obj", "1"): BODY_BOX},
    "Section Header": {("title", "0"): (0.9, 3.05, 11.5, 1.35), ("body", "1"): (0.9, 4.45, 11.5, 1.0)},
    "Two Content": {
        ("title", "0"): TITLE_BOX,
        ("obj", "1"): (0.6, 1.7, 5.9, 5.05),
        ("obj", "2"): (6.83, 1.7, 5.9, 5.05),
    },
    "Title Only": {("title", "0"): TITLE_BOX},
}
# Title and section slides are on paper; the others white, like the figures on them.
PAPER_LAYOUTS = ("Title Slide", "Section Header")


def _theme(xml: bytes) -> bytes:
    tree = etree.fromstring(xml)
    tree.set("name", "code flower")
    scheme = tree.find(".//a:clrScheme", NS)
    scheme.set("name", "code flower")
    tokens = palette.TOKENS["light"]
    accents = palette.CATEGORICAL["light"]
    colors = {
        "dk1": tokens["ink"],
        "lt1": "#ffffff",
        "dk2": tokens["muted"],
        "lt2": tokens["paper"],
        "accent1": accents[0],
        "accent2": accents[1],
        "accent3": accents[2],
        "accent4": accents[3],
        "accent5": accents[4],
        "accent6": accents[5],
        "hlink": tokens["indigo"],
        # Never a followed link on a slide: this slot holds dark slides' first accent (DARK_MAPPING).
        "folHlink": palette.TOKENS["dark"]["indigo"],
    }
    for name, color in colors.items():
        slot = scheme.find(f"a:{name}", NS)
        for child in list(slot):
            slot.remove(child)
        etree.SubElement(slot, f"{{{NS['a']}}}srgbClr", val=_hex(color))
    fonts = tree.find(".//a:fontScheme", NS)
    fonts.set("name", "code flower")
    for latin in fonts.iterfind(".//a:latin", NS):
        latin.set("typeface", "Arial")
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def _background(tree: etree._Element, scheme_color: str) -> None:
    c_sld = tree.find("p:cSld", NS)
    for old in c_sld.findall("p:bg", NS):
        c_sld.remove(old)
    bg = etree.Element(f"{{{NS['p']}}}bg")
    bg_pr = etree.SubElement(bg, f"{{{NS['p']}}}bgPr")
    fill = etree.SubElement(bg_pr, f"{{{NS['a']}}}solidFill")
    etree.SubElement(fill, f"{{{NS['a']}}}schemeClr", val=scheme_color)
    etree.SubElement(bg_pr, f"{{{NS['a']}}}effectLst")
    c_sld.insert(0, bg)


def _text_styles(master: etree._Element) -> None:
    """Left-aligned bold titles and calmer body sizes than Office's (44 / 32 pt)."""
    title = master.find(".//p:txStyles/p:titleStyle/a:lvl1pPr", NS)
    title.set("algn", "l")
    run = title.find("a:defRPr", NS)
    run.set("sz", "3200")
    run.set("b", "1")
    sizes = {"lvl1pPr": "2200", "lvl2pPr": "1900", "lvl3pPr": "1700", "lvl4pPr": "1600", "lvl5pPr": "1600"}
    body = master.find(".//p:txStyles/p:bodyStyle", NS)
    for level, size in sizes.items():
        props = body.find(f"a:{level}", NS)
        if props is not None:
            props.find("a:defRPr", NS).set("sz", size)
            props.set("marL", str(int(props.get("marL", "0")) * 2 // 3))
            props.set("indent", str(int(props.get("indent", "0")) * 2 // 3))
    # Anchor titles at the bottom of their box, so one- and two-line titles share a baseline.
    for shape, kind, _ in _placeholders(master):
        if kind == "title":
            shape.find("p:txBody/a:bodyPr", NS).set("anchor", "b")


def _layout_styles(name: str, tree: etree._Element) -> None:
    """Office's layouts restyle their placeholders (centred subtitles, capitals, larger two-column
    text): left-aligned, sentence case, muted text in the theme's second dark colour instead."""
    two_columns = {"lvl1pPr": "2000", "lvl2pPr": "1800", "lvl3pPr": "1600"}
    for shape, kind, _ in _placeholders(tree):
        if name == "Section Header" and kind == "body":
            shape.find("p:txBody/a:bodyPr", NS).set("anchor", "t")  # under the title, not at the bottom of its box
        styles = shape.find("p:txBody/a:lstStyle", NS)
        if styles is None:
            continue
        if name == "Title Slide" and kind == "ctrTitle":
            level = etree.SubElement(styles, f"{{{NS['a']}}}lvl1pPr", algn="l")
            etree.SubElement(level, f"{{{NS['a']}}}defRPr", sz="4400", b="1")
        for level in styles:
            if level.get("algn") == "ctr":
                level.set("algn", "l")
            run = level.find("a:defRPr", NS)
            if run is None:
                continue
            run.attrib.pop("cap", None)
            if name == "Two Content" and level.tag.endswith(tuple(two_columns)):
                run.set("sz", two_columns[etree.QName(level).localname])
            for color in run.iterfind(".//a:schemeClr", NS):
                if color.get("val") == "tx1" and color.find("a:tint", NS) is not None:
                    color.set("val", "tx2")
                    color.remove(color.find("a:tint", NS))


def _accent_bar(master: etree._Element) -> None:
    """code flower's mark on every slide: a short indigo bar above the title."""
    tree = master.find("p:cSld/p:spTree", NS)
    ids = [int(e.get("id")) for e in tree.iterfind(".//p:cNvPr", NS)]
    p, a = NS["p"], NS["a"]
    sp = etree.SubElement(tree, f"{{{p}}}sp")
    nv = etree.SubElement(sp, f"{{{p}}}nvSpPr")
    etree.SubElement(nv, f"{{{p}}}cNvPr", id=str(max(ids) + 1), name="Accent")
    etree.SubElement(nv, f"{{{p}}}cNvSpPr")
    etree.SubElement(nv, f"{{{p}}}nvPr")
    sp_pr = etree.SubElement(sp, f"{{{p}}}spPr")
    xfrm = etree.SubElement(sp_pr, f"{{{a}}}xfrm")
    etree.SubElement(xfrm, f"{{{a}}}off", x=_inches(0.62), y=_inches(0.36))
    etree.SubElement(xfrm, f"{{{a}}}ext", cx=_inches(0.7), cy=_inches(0.09))
    geometry = etree.SubElement(sp_pr, f"{{{a}}}prstGeom", prst="roundRect")
    etree.SubElement(geometry, f"{{{a}}}avLst")
    fill = etree.SubElement(sp_pr, f"{{{a}}}solidFill")
    etree.SubElement(fill, f"{{{a}}}schemeClr", val="accent1")
    line = etree.SubElement(sp_pr, f"{{{a}}}ln")
    etree.SubElement(line, f"{{{a}}}noFill")


# Dark slides swap the theme's light and dark colours, and take the brighter indigo of
# the report's dark mode (kept in the followed-link slot) as their first accent.
DARK_MAPPING = {
    "bg1": "dk1",
    "tx1": "lt1",
    "bg2": "dk2",
    "tx2": "lt2",
    "accent1": "folHlink",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}
DARK_SUFFIX = " (dark)"
# Dark content slides are the colour of storyteller's dark figures; title and section slides darker.
DARK_BACKGROUNDS = {"Title Slide": "dark-paper", "Section Header": "dark-paper"}
BRAND_LINE = "Made with code flower · storyteller"


def _solid_background(tree: etree._Element, color: str) -> None:
    c_sld = tree.find("p:cSld", NS)
    for old in c_sld.findall("p:bg", NS):
        c_sld.remove(old)
    bg = etree.Element(f"{{{NS['p']}}}bg")
    bg_pr = etree.SubElement(bg, f"{{{NS['p']}}}bgPr")
    fill = etree.SubElement(bg_pr, f"{{{NS['a']}}}solidFill")
    etree.SubElement(fill, f"{{{NS['a']}}}srgbClr", val=_hex(color))
    etree.SubElement(bg_pr, f"{{{NS['a']}}}effectLst")
    c_sld.insert(0, bg)


def _add_dark_layouts(prs) -> None:
    """A dark twin of each layout slides use, in the same theme: "Title and Content (dark)"."""
    import copy

    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.parts.slide import SlideLayoutPart

    master = prs.slide_master
    package = master.part.package
    ids = master._element.find("p:sldLayoutIdLst", NS)
    for layout in list(prs.slide_layouts):
        if layout.name not in LAYOUT_NAMES_ALL:
            continue
        element = copy.deepcopy(layout._element)
        element.find("p:cSld", NS).set("name", layout.name + DARK_SUFFIX)
        mapping = element.find("p:clrMapOvr", NS)
        if mapping is None:
            mapping = etree.SubElement(element, f"{{{NS['p']}}}clrMapOvr")
            element.insert(1, mapping)
        for child in list(mapping):
            mapping.remove(child)
        etree.SubElement(mapping, f"{{{NS['a']}}}overrideClrMapping", **DARK_MAPPING)
        dark = palette.TOKENS["dark"]
        background = dark["paper"] if DARK_BACKGROUNDS.get(layout.name) else dark["card"]
        _solid_background(element, background)
        part = SlideLayoutPart(
            package.next_partname("/ppt/slideLayouts/slideLayout%d.xml"), layout.part.content_type, package, element
        )
        part.relate_to(master.part, RT.SLIDE_MASTER)
        r_id = master.part.relate_to(part, RT.SLIDE_LAYOUT)
        number = max(int(entry.get("id")) for entry in ids) + 1
        entry = etree.SubElement(ids, f"{{{NS['p']}}}sldLayoutId", id=str(number))
        entry.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", r_id)


def _branding(prs) -> None:
    """The code flower mark and "Made with code flower · storyteller" at the foot of every slide,
    in the theme's second text colour (muted on light slides, light on dark ones)."""
    from pptx.oxml.shapes.picture import CT_Picture
    from pptx.util import Inches

    from ..render import asset_bytes

    master = prs.slide_master
    tree = master._element.find("p:cSld/p:spTree", NS)
    ids = [int(e.get("id")) for e in tree.iterfind(".//p:cNvPr", NS)]
    _, r_id = master.part.get_or_add_image_part(io.BytesIO(asset_bytes("deck/logo.png")))
    size, top = Inches(0.3), Inches(6.98)
    picture = CT_Picture.new_pic(max(ids) + 1, "code flower", "code flower", r_id, Inches(0.62), top, size, size)
    tree.append(picture)
    p, a = NS["p"], NS["a"]
    sp = etree.SubElement(tree, f"{{{p}}}sp")
    nv = etree.SubElement(sp, f"{{{p}}}nvSpPr")
    etree.SubElement(nv, f"{{{p}}}cNvPr", id=str(max(ids) + 2), name="Made with")
    etree.SubElement(nv, f"{{{p}}}cNvSpPr", txBox="1")
    etree.SubElement(nv, f"{{{p}}}nvPr")
    sp_pr = etree.SubElement(sp, f"{{{p}}}spPr")
    xfrm = etree.SubElement(sp_pr, f"{{{a}}}xfrm")
    etree.SubElement(xfrm, f"{{{a}}}off", x=_inches(1.0), y=str(top))
    etree.SubElement(xfrm, f"{{{a}}}ext", cx=_inches(5.0), cy=str(size))
    geometry = etree.SubElement(sp_pr, f"{{{a}}}prstGeom", prst="rect")
    etree.SubElement(geometry, f"{{{a}}}avLst")
    body = etree.SubElement(sp, f"{{{p}}}txBody")
    etree.SubElement(body, f"{{{a}}}bodyPr", wrap="none", lIns="0", tIns="0", rIns="0", bIns="0", anchor="ctr")
    etree.SubElement(body, f"{{{a}}}lstStyle")
    paragraph = etree.SubElement(body, f"{{{a}}}p")
    run = etree.SubElement(paragraph, f"{{{a}}}r")
    props = etree.SubElement(run, f"{{{a}}}rPr", lang="en-US", sz="1000")
    fill = etree.SubElement(props, f"{{{a}}}solidFill")
    etree.SubElement(fill, f"{{{a}}}schemeClr", val="tx2")
    etree.SubElement(run, f"{{{a}}}t").text = BRAND_LINE


@cache
def default_template() -> bytes:
    """code flower's template, as .pptx bytes."""
    from pptx import Presentation
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    prs = Presentation()
    scale = 13.333 / 10
    prs.slide_width = round(13.333 * EMU_PER_INCH)
    prs.slide_height = round(7.5 * EMU_PER_INCH)
    master = prs.slide_master
    theme = master.part.part_related_by(RT.THEME)
    theme._blob = _theme(theme.blob)
    trees = [("master", master._element)] + [(layout.name, layout._element) for layout in prs.slide_layouts]
    for name, tree in trees:
        # Office's 4:3 boxes, stretched to 16:9; then our own for the layouts slides use.
        for shape in tree.iterfind(".//p:sp", NS):
            off = shape.find("p:spPr/a:xfrm/a:off", NS)
            ext = shape.find("p:spPr/a:xfrm/a:ext", NS)
            if off is not None and ext is not None:
                off.set("x", str(round(int(off.get("x")) * scale)))
                ext.set("cx", str(round(int(ext.get("cx")) * scale)))
        geometry = {**GEOMETRY.get(name, {})}
        for shape, kind, idx in _placeholders(tree):
            box = geometry.get((kind, idx)) or FOOTERS.get(kind)
            if box and (name == "master" or shape.find("p:spPr/a:xfrm", NS) is not None or (kind, idx) in geometry):
                _set_xfrm(shape, *box)
        if name != "master":
            _layout_styles(name, tree)
        if name in PAPER_LAYOUTS:
            _background(tree, "bg2")
    _background(master._element, "bg1")
    _text_styles(master._element)
    _accent_bar(master._element)
    _branding(prs)
    _add_dark_layouts(prs)
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


# -- user templates --------------------------------------------------------------------------


def as_pptx(data: bytes) -> bytes:
    """A .potx as a .pptx (python-pptx opens only presentations); a .pptx unchanged."""
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(data))
    types = source.read("[Content_Types].xml")
    if POTX_TYPE not in types:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                content = content.replace(POTX_TYPE, PPTX_TYPE)
            target.writestr(item, content)
    return out.getvalue()


def load(source: str | Path | None) -> tuple[bytes, str]:
    """The template's .pptx bytes and its name: the default (also by the name "codeflower"),
    or a .pptx/.potx file."""
    if source is None or str(source).strip().lower() in DEFAULT_NAMES:
        return default_template(), "code flower"
    path = Path(source).expanduser()
    if path.suffix.lower() not in TEMPLATE_SUFFIXES:
        raise ValueError(f"{path}: a template is a PowerPoint .pptx or .potx file")
    if not path.is_file():
        raise FileNotFoundError(f"no template at {path}")
    return as_pptx(path.read_bytes()), path.stem


FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


def fonts_beside(source: str | Path | None) -> list[Path]:
    """The font files in a `fonts/` folder next to a template: a template's fonts travel with it,
    so LibreOffice draws its slides in them."""
    if source is None or str(source).strip().lower() in DEFAULT_NAMES:
        return []
    folder = Path(source).expanduser().parent / "fonts"
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in FONT_SUFFIXES and path.is_file())


# -- which template layout a slide uses ----------------------------------------------------------


@dataclass(frozen=True)
class Box:
    """A rectangle on the slide, in EMU."""

    x: int
    y: int
    w: int
    h: int


TITLE_TYPES = ("TITLE", "CENTER_TITLE")
TEXT_TYPES = ("BODY", "OBJECT", "SUBTITLE")
REGION_TYPES = ("BODY", "OBJECT", "PICTURE", "SUBTITLE")
FURNITURE = ("DATE", "FOOTER", "SLIDE_NUMBER", "HEADER")
REGION_SHARE = 0.10  # a placeholder covering this share of the slide can hold a block
NUMBER_PROMPT = re.compile(r"[#\d\s.]+")
# Layouts that document a template or hold logos rather than slides of content.
NOT_FOR_SLIDES = re.compile(r"how to|\bfonts?\b|\bcolou?rs\b|instruction|team|acknowledg|logo|agenda|^end$|thank", re.I)
KEYWORDS = {
    "title": re.compile(r"title slide|title page|cover|titel|opening", re.I),
    "section": re.compile(r"section|divider|chapter|break", re.I),
    "content": re.compile(r"title and content|content", re.I),
    "two": re.compile(r"two content|2 content|comparison|two column", re.I),
    "statement": re.compile(r"title only|statement|quote", re.I),
    "blank": re.compile(r"blank|empty", re.I),
}


def _kind(placeholder) -> str:
    return placeholder.placeholder_format.type.name if placeholder.placeholder_format.type else "OBJECT"


def _area(placeholder) -> int:
    return (placeholder.width or 0) * (placeholder.height or 0)


@dataclass
class Roles:
    """What a layout's (or slide's) placeholders are for."""

    title: object | None = None
    number: object | None = None  # a "##" box for the section's number
    subtitle: object | None = None
    regions: list = field(default_factory=list)  # can hold a block: largest first
    small: list = field(default_factory=list)  # other text boxes (sources, kickers), left empty


def roles(placeholders: list, prompts: Mapping[int, str], width: int, height: int) -> Roles:
    """Sorts placeholders by what they are for. `prompts` is each one's text on the layout
    (by idx): a title box prompting "##" holds a number. Without a title placeholder, the
    largest text box that cannot hold a block takes the title (a cover's "presentation title")."""
    out = Roles()
    slide = width * height or 1
    rest = []
    for placeholder in placeholders:
        kind = _kind(placeholder)
        if kind in FURNITURE:
            continue
        if kind in TITLE_TYPES and out.title is None:
            prompt = prompts.get(placeholder.placeholder_format.idx, "")
            if prompt.strip() and NUMBER_PROMPT.fullmatch(prompt.strip()) and out.number is None:
                out.number = placeholder
            else:
                out.title = placeholder
        elif kind == "SUBTITLE" and out.subtitle is None:
            out.subtitle = placeholder
        elif kind in REGION_TYPES:
            rest.append(placeholder)
    for placeholder in sorted(rest, key=_area, reverse=True):
        if _area(placeholder) >= REGION_SHARE * slide and _kind(placeholder) != "SUBTITLE":
            out.regions.append(placeholder)
        elif _kind(placeholder) in TEXT_TYPES:
            out.small.append(placeholder)
    if out.title is None and out.small:
        out.title = out.small.pop(0)
    if out.subtitle is None and out.title is not None:
        below = [p for p in out.small if (p.top or 0) >= (out.title.top or 0)]
        if below:
            out.subtitle = min(below, key=lambda p: p.top or 0)
    return out


def layout_roles(prs, layout) -> Roles:
    prompts = {p.placeholder_format.idx: p.text_frame.text if p.has_text_frame else "" for p in layout.placeholders}
    return roles(list(layout.placeholders), prompts, prs.slide_width, prs.slide_height)


def _fit(prs, layout, name: str) -> float | None:
    """How well `layout` suits storyteller layout `name`; None when it cannot hold it."""
    label = layout.name.strip()
    if NOT_FOR_SLIDES.search(label):
        return None
    r = layout_roles(prs, layout)
    slide = (prs.slide_width * prs.slide_height) or 1
    named = 4.0 if KEYWORDS[name].search(label) else 0.0

    def edge(placeholder) -> float:
        """Content boxes that bleed off the slide are for photos, not text and charts."""
        left, top = placeholder.left or 0, placeholder.top or 0
        right, bottom = left + (placeholder.width or 0), top + (placeholder.height or 0)
        bleeds = left < 0.02 * prs.slide_width or right > 0.98 * prs.slide_width or bottom > 0.98 * prs.slide_height
        return 3.0 if bleeds else 0.0

    if name == "blank":
        return 1.5 * named - len(r.regions) - len(r.small) - (r.title is not None)
    if r.title is None:
        return None
    if name == "title":
        centred = 2.0 if _kind(r.title) == "CENTER_TITLE" else 0.0
        return 1.5 * named + centred + (r.subtitle is not None) - len(r.regions)
    if name == "section":
        return 1.5 * named + (r.number is not None) - len(r.regions)
    if name == "statement":
        return named - 3 * len(r.regions) - 0.5 * len(r.small) - 2 * (r.number is not None)
    if name == "content":
        if not r.regions:
            return None
        first = r.regions[0]
        return named + 10 * _area(first) / slide - 3 * (len(r.regions) - 1) - 0.5 * len(r.small) - edge(first)
    if name == "two":
        if len(r.regions) < 2:
            return None
        a, b = sorted(r.regions[:2], key=lambda p: p.left or 0)
        if (a.left or 0) + (a.width or 0) > (b.left or 0) + 0.02 * prs.slide_width:
            return None  # stacked, not side by side
        share = (_area(a) + _area(b)) / slide
        return named + 10 * share - 3 * (len(r.regions) - 2) - 0.5 * len(r.small) - edge(a) - edge(b)
    return None


def template_has_dark(template: bytes) -> bool:
    from pptx import Presentation

    return has_dark(Presentation(io.BytesIO(template)))


def has_dark(prs) -> bool:
    """Whether the template has dark twins of its layouts ("Title and Content (dark)")."""
    return any(layout.name.strip().lower().endswith(DARK_SUFFIX) for layout in prs.slide_layouts)


def choose(prs, name: str, overrides: Mapping[str, str] | None = None, mode: str = "light") -> object:
    """The template layout for storyteller layout `name`: the one `overrides` names, else the
    one with the standard PowerPoint name, else the one whose placeholders fit best. In dark
    `mode`, the layout's dark twin when the template has one."""
    light = _choose(prs, name, overrides)
    if mode == "dark":
        twin = {layout.name.strip().lower(): layout for layout in prs.slide_layouts}.get(
            light.name.strip().lower() + DARK_SUFFIX
        )
        if twin is not None:
            return twin
    return light


def _choose(prs, name: str, overrides: Mapping[str, str] | None = None) -> object:
    layouts = [layout for layout in prs.slide_layouts if not layout.name.strip().lower().endswith(DARK_SUFFIX)]
    by_name = {layout.name.strip().lower(): layout for layout in layouts}
    wanted = (overrides or {}).get(name)
    if wanted:
        if wanted.strip().lower() not in by_name:
            names = ", ".join(repr(layout.name) for layout in layouts)
            raise ValueError(f"the template has no layout {wanted!r} (it has {names})")
        return by_name[wanted.strip().lower()]
    for candidate in LAYOUT_NAMES[name]:
        if candidate.lower() in by_name:
            return by_name[candidate.lower()]
    scored = [
        (score, -index, layout)
        for index, layout in enumerate(layouts)
        if (score := _fit(prs, layout, name)) is not None
    ]
    if scored:
        return max(scored, key=lambda item: item[:2])[2]
    return min(layouts, key=lambda layout: len(layout.placeholders))


def check_layouts(template: bytes, overrides: Mapping[str, str]) -> None:
    """Raises a ValueError naming what is wrong with `overrides` for this template."""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(template))
    for name in overrides:
        resolved = resolve_layout(name)
        if resolved != name:
            raise ValueError(f"layouts: use storyteller's layout name {resolved!r}, not {name!r}")
        choose(prs, name, overrides)


def content_box(prs) -> Box:
    """Where a slide's content goes when its layout has no placeholder for it: the body of
    the template's single-content layout, else the slide less its margins."""
    regions = layout_roles(prs, choose(prs, "content")).regions
    if regions:
        first = regions[0]
        return Box(first.left, first.top, first.width, first.height)
    margin = round(0.6 * EMU_PER_INCH)
    top = round(1.7 * EMU_PER_INCH)
    return Box(margin, top, prs.slide_width - 2 * margin, prs.slide_height - top - margin)
