"""The Deck: a PowerPoint deck built live, slide by slide, like a report.

    deck = sty.Deck.open("decks/pbmc", title="PBMC cell types")
    with deck.section("qc", "Quality control"):
        with deck.slide("qc-cut", "Quality control removed 8% of cells"):
            deck.figure(fig, caption="Genes per cell")

Every change is saved within half a second: the document to the store
(``decks/pbmc.report/deck.json``), the deck to ``decks/pbmc.pptx`` and the viewer to
``decks/pbmc.html``. Then LibreOffice draws the deck in the background
(``decks/pbmc.report/render/``) and the viewer shows the drawn slides. The script
waits for the drawing when it ends, so its last slides are visible when it returns.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import adapters, tables
from .. import comments as reader_comments
from ..document import LiveDocument, close_first_at_exit, now, running_agent, slugify
from ..render import _clock
from ..report import Metric, metric_entries
from ..store import DiskStore, atomic_write
from . import build, office, view
from . import fonts as deck_fonts
from . import template as templates

DOCUMENT = "deck.json"
BUILD = "build.json"
TEMPLATE = "template.pptx"
FONTS = "fonts"  # the template's fonts, for LibreOffice
RENDER = "render"
INDEX = "index.json"
DRAWN_PDF = "deck.pdf"
WIDTH_PX = 1600
WEBP_QUALITY = 82
TABLE_MAX_ROWS = 12
FIGURE_MAX_PX = 2400


def paths(path: str | Path) -> tuple[Path, Path, Path]:
    """The viewer, the .pptx and the store for ``decks/pbmc`` (or ``decks/pbmc.html``, ``.pptx``)."""
    base = Path(path)
    if base.suffix.lower() in (".html", ".htm", ".pptx", ".report"):
        base = base.with_suffix("")
    return (
        base.with_name(base.name + ".html"),
        base.with_name(base.name + ".pptx"),
        base.with_name(base.name + ".report"),
    )


def is_deck(path: str | Path) -> bool:
    """Whether `path` names a deck (its store holds a deck, or it is a .pptx with a store)."""
    return (paths(path)[2] / DOCUMENT).is_file()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=1, ensure_ascii=False).encode("utf-8"))


@contextlib.contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    with open(path, "a+") as handle:
        try:
            import fcntl
        except ImportError:
            yield
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# -- the viewer and the drawing, from the store ------------------------------------------------------


def write_viewer(
    store_dir: Path, html_path: Path, document: dict[str, Any], built: dict[str, Any] | None = None
) -> None:
    """Writes the viewer for `document`, with the slides LibreOffice drew last."""
    built = built if built is not None else _read_json(store_dir / BUILD)
    drawn = _read_json(store_dir / RENDER / INDEX)
    images: dict[str, str] = {}
    for slide in drawn.get("slides", []):
        try:
            data = (store_dir / RENDER / slide["image"]).read_bytes()
        except (FileNotFoundError, KeyError):
            continue
        images[slide["part"]] = "data:image/webp;base64," + base64.b64encode(data).decode("ascii")
    html = view.render_page(
        document,
        built,
        drawn,
        images,
        deck_file=html_path.with_suffix(".pptx").name,
        comments=reader_comments.for_build(store_dir, html_path),
    )
    atomic_write(html_path, html.encode("utf-8"))


def draw(store_dir: Path, html_path: Path) -> dict[str, Any]:
    """Has LibreOffice draw the deck as last built, unless it already has, then rewrites the
    viewer. Returns what was drawn (``render/index.json``): the slides' images, or the error."""
    store = DiskStore(store_dir, DOCUMENT)
    pptx_path = html_path.with_suffix(".pptx")
    render_dir = store_dir / RENDER
    render_dir.mkdir(parents=True, exist_ok=True)
    with _exclusive(store_dir / "render.lock"):
        with store.lock():
            built = _read_json(store_dir / BUILD)
            try:
                data = pptx_path.read_bytes()
            except FileNotFoundError:
                return {}
        drawn = _read_json(render_dir / INDEX)
        if built.get("build") and drawn.get("build") == built["build"] and not drawn.get("error"):
            return drawn
        if not built.get("slides"):  # LibreOffice cannot open a presentation without slides
            return drawn
        with tempfile.TemporaryDirectory(prefix="storyteller-deck-") as work:
            source = Path(work) / "deck.pptx"
            source.write_bytes(data)
            try:
                pdf = office.to_pdf(source, Path(work), fonts=store_dir / FONTS)
                images = office.pages(pdf, WIDTH_PX)
                if len(images) != len(built.get("slides", [])):
                    raise office.OfficeError(f"LibreOffice drew {len(images)} slides of {len(built.get('slides', []))}")
            except office.OfficeError as error:
                drawn = {**drawn, "build": built.get("build"), "error": str(error)}
            else:
                slides = []
                keep = {INDEX, DRAWN_PDF}
                for number, (entry, image) in enumerate(zip(built["slides"], images, strict=True), start=1):
                    png = f"{number:02d}-{slugify(entry['key'])}{'-head' if entry['kind'] == 'head' else ''}.png"
                    image.save(render_dir / png, "PNG", optimize=False)
                    buffer = _webp(image)
                    name = hashlib.sha256(buffer).hexdigest()[:32] + ".webp"
                    if not (render_dir / name).exists():
                        atomic_write(render_dir / name, buffer)
                    keep.update((png, name))
                    slides.append({"part": entry["part"], "hash": entry["hash"], "png": png, "image": name})
                shutil.copyfile(pdf, render_dir / DRAWN_PDF)
                for old in render_dir.iterdir():
                    if old.name not in keep and not old.name.startswith("."):
                        old.unlink(missing_ok=True)
                drawn = {"build": built.get("build"), "rendered": now(), "slides": slides, "error": None}
        _write_json(render_dir / INDEX, drawn)
    with store.lock():
        document = store.load()
        if document is not None:
            write_viewer(store_dir, html_path, document)
    return drawn


def _webp(image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "WEBP", quality=WEBP_QUALITY, method=4)
    return buffer.getvalue()


# -- the deck -------------------------------------------------------------------------------------------


class Deck(LiveDocument):
    """A slide deck under construction. Open one with `Deck.open`; see the module docstring."""

    def __init__(self, title: str, path: str | Path, *, layouts: Mapping[str, str] | None = None) -> None:
        super().__init__(paths(path)[0])
        running = running_agent()
        self._set_meta(title=title, agent=running.get("agent"), model=running.get("model"), final=True)
        if layouts:
            self._set_meta(layouts=dict(layouts))
        self._drawing = threading.Condition()
        self._draw_wanted = False
        self._draw_thread: threading.Thread | None = None
        self._written: dict[str, None] = {}  # slide parts this process wrote, for the closing summary
        self._drawn: dict[str, Any] = {}

    @classmethod
    def open(
        cls,
        path: str | Path,
        title: str | None = None,
        *,
        subtitle: str | None = None,
        author: str | None = None,
        template: str | Path | None = None,
        layouts: Mapping[str, str] | None = None,
        mode: str | None = None,
    ) -> Deck:
        """Opens (or creates) the deck at `path` (``decks/pbmc``), stored in ``decks/pbmc.report/``
        and saved live as it changes. `template` is a PowerPoint .pptx or .potx whose masters,
        layouts and theme the slides use (default: code flower's); `layouts` maps storyteller's
        layout names to the template's (``{"two": "Comparison"}``). `mode` is ``"light"`` (the
        default) or ``"dark"``: dark slides, where the template has them (code flower's does)."""
        if mode is not None and mode not in templates.MODES:
            raise ValueError(f"mode is 'light' or 'dark', not {mode!r}")
        import pypdfium2  # noqa: F401 - it tears down at exit, after which the last slides could not be drawn

        close_first_at_exit()
        html_path, _, store_dir = paths(path)
        if (store_dir / "report.json").is_file():
            raise ValueError(f"{store_dir} belongs to a report, not a deck: choose another name")
        store = DiskStore(store_dir, DOCUMENT)
        existing = store.load()
        if existing is None and title is None:
            raise FileNotFoundError(f"no deck at {store_dir}; pass title= to create one")
        deck = cls(title or existing["meta"].get("title", "Deck"), html_path, layouts=layouts)
        deck._go_live(store, existing)
        if existing is None or title is not None:
            deck._set_meta(title=title or existing["meta"].get("title"))
        running = running_agent()
        deck._set_meta(subtitle=subtitle, author=author, agent=running.get("agent"), model=running.get("model"))
        if layouts:
            deck._set_meta(layouts=dict(layouts))
        if existing is None:
            deck._set_meta(date=datetime.now().date().isoformat())
        if template is not None or not (store_dir / TEMPLATE).is_file():
            data, name = templates.load(template)
            with store.lock():
                atomic_write(store_dir / TEMPLATE, data)
                shutil.rmtree(store_dir / FONTS, ignore_errors=True)
                for font in templates.fonts_beside(template):
                    (store_dir / FONTS).mkdir(exist_ok=True)
                    shutil.copyfile(font, store_dir / FONTS / font.name)
            deck._set_meta(template=name)
        if layouts:
            templates.check_layouts((store_dir / TEMPLATE).read_bytes(), layouts)
        if mode is not None:
            deck._set_meta(mode=mode)
        if (mode or deck._doc["meta"].get("mode")) == "dark" and not templates.template_has_dark(
            (store_dir / TEMPLATE).read_bytes()
        ):
            print("storyteller: the template has no dark layouts; the slides stay light", file=sys.stderr)
        absent = deck.missing_fonts()
        if absent:
            print(
                f"storyteller: the template's fonts {', '.join(absent)} are not installed, so LibreOffice draws "
                "its slides in other fonts and they will not look as in PowerPoint. Install them (the font "
                "files in ~/.local/share/fonts/, or in a fonts/ folder next to the template), then open the "
                "deck again.",
                file=sys.stderr,
            )
        deck.save()
        return deck

    def missing_fonts(self) -> list[str]:
        """The fonts the deck's template names that are not installed (see deck/fonts.py)."""
        store_dir = self._store.directory
        return deck_fonts.missing((store_dir / TEMPLATE).read_bytes(), [store_dir / FONTS])

    @property
    def pptx_path(self) -> Path:
        return self.path.with_suffix(".pptx")

    # -- saving and drawing --

    def _write(self, target: Path) -> None:
        store_dir = self._store.directory
        draft = not self._doc["meta"].get("final", False)
        template = (store_dir / TEMPLATE).read_bytes()
        built = build.build(
            self._doc,
            self._asset,
            template,
            draft=draft,
            layouts=self._doc["meta"].get("layouts"),
            fonts=[store_dir / FONTS],
        )
        atomic_write(target.with_suffix(".pptx"), built.data)
        manifest = {
            # What the slides show, not the file's bytes (python-pptx stamps the time into them):
            # a save that changes nothing is not drawn again.
            "build": build.node_hash([s["hash"] for s in built.slides], built.width, built.height),
            "width": built.width,
            "height": built.height,
            "slides": built.slides,
        }
        _write_json(store_dir / BUILD, manifest)
        write_viewer(store_dir, target, self._doc, manifest)

    def _saved(self) -> None:
        if not self._live or os.environ.get("STORYTELLER_DRAW") == "0":
            return
        with self._drawing:
            self._draw_wanted = True
            if self._draw_thread is None:
                thread = threading.Thread(target=self._draw_loop, name="storyteller-draw", daemon=True)
                try:
                    thread.start()
                except RuntimeError:  # the interpreter is exiting: close() draws instead
                    return
                self._draw_thread = thread

    def _draw_once(self) -> None:
        try:
            self._drawn = draw(self._store.directory, self.path)
        except Exception as error:  # drawing is a preview: never break the script for it
            self._drawn = {"error": f"{type(error).__name__}: {error}"}

    def _draw_loop(self) -> None:
        while True:
            with self._drawing:
                if not self._draw_wanted:
                    self._draw_thread = None
                    self._drawing.notify_all()
                    return
                self._draw_wanted = False
            self._draw_once()

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """Waits until LibreOffice has drawn the latest save. Returns what was drawn."""
        with self._drawing:
            self._drawing.wait_for(lambda: self._draw_thread is None, timeout)
            pending = self._draw_wanted and self._draw_thread is None
            self._draw_wanted = self._draw_wanted and not pending
        if pending:
            self._draw_once()
        return self._drawn

    def close(self) -> None:
        """Saves pending changes and waits for the drawing (also done when the script ends)."""
        super().close()
        if not self._live:
            return
        drawn = self.wait()
        if not self._written:
            return
        self._written, written = {}, self._written
        if drawn.get("error"):
            print(f"storyteller: {self.pptx_path} saved; its slides were not drawn: {drawn['error']}", file=sys.stderr)
            return
        render_dir = self._store.directory / RENDER
        by_part = {slide["part"]: slide for slide in drawn.get("slides", [])}
        pictures = [str(render_dir / by_part[part]["png"]) for part in written if part in by_part]
        if pictures:
            print(f"storyteller: drew {len(pictures)} slide(s) of {self.pptx_path}; look at them:", file=sys.stderr)
            for picture in pictures:
                print(f"  {picture}", file=sys.stderr)
        built = _read_json(self._store.directory / BUILD).get("slides", [])
        for entry in built:
            if entry["part"] in written:
                for warning in entry.get("warnings", []):
                    print(f"storyteller: slide {entry['key']!r}: {warning}", file=sys.stderr)

    # -- structure --

    @contextlib.contextmanager
    def section(
        self, key_or_title: str, title: str | None = None, *, description: str | None = None, header: bool = True
    ) -> Iterator[Deck]:
        """A group of slides: ``with deck.section("qc", "Quality control"): ...``. It opens with a
        header slide (its title and `description`) unless ``header=False``. Its slides are
        replaced as a whole each time the block runs."""
        if self._stack:
            raise RuntimeError("deck.section() goes at the top level: sections do not nest, and hold slides")
        key, heading = (key_or_title, title) if title else (slugify(key_or_title), key_or_title)
        key = slugify(key)
        node: dict[str, Any] = {
            "type": "section",
            "key": key,
            "title": heading,
            "status": "running",
            "header": header,
            "children": [],
            "updated": now(),
        }
        if description:
            node["description"] = description
        self._written[f"head:{key}"] = None
        with self._filling(node):
            yield self

    @contextlib.contextmanager
    def slide(
        self,
        key_or_title: str | None = None,
        title: str | None = None,
        *,
        layout: str | None = None,
        subtitle: str | None = None,
        mode: str | None = None,
    ) -> Iterator[Deck]:
        """One slide: ``with deck.slide("qc-cut", "Quality control removed 8% of cells"): ...``.

        Its blocks fill the `layout`: ``title`` (title and subtitle), ``section``, ``content`` (one
        block), ``two`` (two blocks side by side), ``statement`` (the title alone, large) or
        ``blank``. Left out, it follows the blocks. `mode` (``"light"``, ``"dark"``) sets this slide
        apart from the deck's. Re-running the block replaces the slide."""
        if mode is not None and mode not in templates.MODES:
            raise ValueError(f"mode is 'light' or 'dark', not {mode!r}")
        if self._stack and self._stack[-1]["type"] == "slide":
            raise RuntimeError("slides do not nest: close one `with deck.slide(...)` before the next")
        if key_or_title is None:
            key, heading = self._auto_key("slide"), title
        elif title is not None:
            key, heading = slugify(key_or_title), title
        else:
            key, heading = slugify(key_or_title), key_or_title
        node: dict[str, Any] = {
            "type": "slide",
            "key": key,
            "title": heading or "",
            "layout": templates.resolve_layout(layout),
            "status": "running",
            "children": [],
            "updated": now(),
        }
        if subtitle:
            node["subtitle"] = subtitle
        if mode:
            node["mode"] = mode
        self._written[f"slide:{key}"] = None
        with self._filling(node):
            yield self

    def pending(self, key: str, title: str, *, note: str | None = None) -> None:
        """Announces a section a later script will fill; it shows as a header slide in the draft.
        An existing section keeps its slides."""
        from ..document import find

        key = slugify(key)
        with self._lock:
            found = find(self._doc["nodes"], key)
            if found and found[0][found[1]].get("status") not in ("pending", None):
                return
            node: dict[str, Any] = {"type": "section", "key": key, "title": title, "status": "pending", "children": []}
            if note:
                node["note"] = note
            self._upsert_top(node)
            self._changed()

    # -- blocks --

    def _open_slide(self, call: str) -> dict[str, Any]:
        if not self._stack or self._stack[-1]["type"] != "slide":
            raise RuntimeError(f"deck.{call}() goes inside `with deck.slide(...):`")
        return self._stack[-1]

    def title(self, text: str) -> None:
        """The slide's title (also the second argument of `slide`)."""
        with self._lock:
            self._open_slide("title")["title"] = text
            self._touch_open()
            self._changed()

    def subtitle(self, text: str) -> None:
        """A line under the title: on title and section slides, else above the content."""
        with self._lock:
            self._open_slide("subtitle")["subtitle"] = text
            self._touch_open()
            self._changed()

    def notes(self, text: str) -> None:
        """Speaker notes (Markdown): what to say with the slide. Shown in PowerPoint's notes and the viewer."""
        with self._lock:
            node = self._open_slide("notes")
            node["notes"] = f"{node['notes']}\n\n{text}" if node.get("notes") else text
            self._touch_open()
            self._changed()

    def text(self, text: str, *, key: str | None = None) -> str | None:
        """Markdown: a few short bullet points (``- one``), or a sentence or two."""
        self._open_slide("text")
        return self._add({"type": "text", "markdown": text}, key)

    def figure(
        self,
        figure: Any,
        caption: str | None = None,
        *,
        alt: str | None = None,
        key: str | None = None,
        close: bool = True,
    ) -> str | None:
        """A picture: a matplotlib figure or axes, seaborn grid, PIL image, array, or an image file."""
        self._open_slide("figure")
        if adapters.is_plotly(figure):
            raise TypeError(
                "plotly figures cannot go on a slide: draw it with matplotlib, or save it with fig.write_image()"
            )
        mpl = adapters.matplotlib_figure(figure)
        if mpl is not None:
            asset = adapters.from_matplotlib(mpl, svg_max_bytes=0, max_px=FIGURE_MAX_PX, lossless=True, close=close)
        else:
            asset = adapters.to_asset(figure, max_px=FIGURE_MAX_PX)
        node: dict[str, Any] = {"type": "figure", "asset": self._put(asset)}
        if caption:
            node["caption"] = caption
        if alt:
            node["alt"] = alt
        return self._add(node, key)

    image = figure

    def table(
        self,
        data: Any,
        caption: str | None = None,
        *,
        format: Mapping[str, Any] | None = None,  # noqa: A002 - matches Report.table
        index: bool | None = None,
        key: str | None = None,
    ) -> str | None:
        """A small table (a DataFrame, dict of columns or list of rows), at most 12 rows: a slide
        shows the few rows that make the point; the full table belongs in a report."""
        self._open_slide("table")
        columns, rows, labels = tables._columns_and_rows(data, index)
        if len(rows) > TABLE_MAX_ROWS:
            raise ValueError(
                f"a slide table shows at most {TABLE_MAX_ROWS} rows, this one has {len(rows)}: "
                "pass the rows that matter (df.head(8), the top hits), or put the full table in a report"
            )
        formats = format or {}
        cells = [
            [tables.format_value(value, formats.get(column)) for column, value in zip(columns, row, strict=False)]
            for row in rows
        ]
        numeric = [
            all(
                tables._is_missing(row[i]) or (isinstance(row[i], (int, float)) and not isinstance(row[i], bool))
                for row in rows
            )
            for i in range(len(columns))
        ]
        if labels is not None:
            columns = [""] + columns
            cells = [[label] + row for label, row in zip(labels, cells, strict=True)]
            numeric = [False] + numeric
        node: dict[str, Any] = {"type": "table", "columns": columns, "rows": cells, "numeric": numeric}
        if caption:
            node["caption"] = caption
        return self._add(node, key)

    def metrics(
        self,
        items: Mapping[str, Any] | Sequence[Metric | Mapping[str, Any] | tuple[Any, ...]],
        *,
        key: str | None = None,
    ) -> str | None:
        """Headline numbers, large: ``{"Cells": 10234}`` or ``[sty.Metric("Cells", 10234, note="after QC")]``.
        Two to four read best."""
        self._open_slide("metrics")
        return self._add({"type": "metrics", "items": metric_entries(items)}, key)

    # -- reading and finishing --

    def outline(self) -> str:
        """The deck's slides in order, with their keys, layouts, status and the picture of each
        LibreOffice drew (look at those)."""
        store_dir = self._store.directory
        built = _read_json(store_dir / BUILD)
        drawn = _read_json(store_dir / RENDER / INDEX)
        by_part = {slide["part"]: slide for slide in drawn.get("slides", [])}
        final = self._doc["meta"].get("final", False)
        slides = built.get("slides", [])
        head = f"{self._doc['meta'].get('title', 'Deck')}  ({'final' if final else 'draft'}) · {self.pptx_path} · {len(slides)} slides"
        if drawn.get("error"):
            head += f" · not drawn: {drawn['error']}"
        elif drawn.get("rendered"):
            head += f" · drawn {_clock(drawn['rendered'])}"
        absent = self.missing_fonts()
        if absent:
            head += f"\n  fonts not installed (drawn in substitutes): {', '.join(absent)}"
        lines = [head]
        sections = {node["key"]: node for node in self._doc["nodes"] if node["type"] == "section"}
        current = None
        for number, entry in enumerate(slides, start=1):
            if entry["section"] != current:
                current = entry["section"]
                if current is not None:
                    section = sections.get(current, {})
                    source = f"  from {section['source']}" if section.get("source") else ""
                    lines.append(
                        f"  [{current}] {section.get('title', current)}  ({section.get('status', 'ready')}){source}"
                    )
            indent = "    " if entry["section"] else "  "
            mark = (
                f"§{entry['key']}  section header" if entry["kind"] == "head" else f"#{entry['key']}  {entry['layout']}"
            )
            title = f'  "{entry["title"]}"' if entry["title"] and entry["kind"] != "head" else ""
            drawn_slide = by_part.get(entry["part"])
            picture = ""
            if drawn_slide:
                picture = f"  {store_dir / RENDER / drawn_slide['png']}"
                if drawn_slide["hash"] != entry["hash"]:
                    picture += "  (being redrawn)"
            problem = f"  PROBLEM: {entry['problem']}" if entry.get("problem") else ""
            problem += "".join(f"\n{indent}   ! {warning}" for warning in entry.get("warnings", []))
            lines.append(f"{indent}{number:02d} {mark}{title}{picture}{problem}")
        return "\n".join(lines)

    def finalize(self) -> Path:
        """Marks the deck final (no planned sections or draft marks), writes it, has LibreOffice draw
        it and saves the PDF beside it. Returns the .pptx path."""
        with self._lock:
            with self._store.lock():
                self._merge_from_disk()
            self._set_meta(final=True)
            for message in self._warnings():
                print(f"storyteller: {message}", file=sys.stderr)
            self.save()
        drawn = self.wait()
        if not drawn or drawn.get("build") != _read_json(self._store.directory / BUILD).get("build"):
            drawn = draw(self._store.directory, self.path)
        pdf = self._store.directory / RENDER / DRAWN_PDF
        if drawn.get("error") or not pdf.is_file():
            print(f"storyteller: wrote {self.pptx_path}; no PDF: {drawn.get('error', 'not drawn')}", file=sys.stderr)
        else:
            shutil.copyfile(pdf, self.pptx_path.with_suffix(".pdf"))
            print(f"storyteller: wrote {self.pptx_path} and {self.pptx_path.with_suffix('.pdf')}", file=sys.stderr)
        if isinstance(self._store, DiskStore):
            self._store.collect_garbage(self._doc)
        return self.pptx_path

    def _warnings(self) -> list[str]:
        out = []
        for node in self._doc["nodes"]:
            # Sections and slides have a status; a slide's blocks (its children) do not.
            parts = [node, *node.get("children", [])] if node["type"] == "section" else [node]
            for item in parts:
                status = item.get("status")
                what = f"{item['type']} {item['key']!r}"
                if status == "pending":
                    out.append(f"{what} was planned but never written; it is left out of the final deck")
                elif status == "error":
                    out.append(f"{what} failed: {(item.get('error') or '').splitlines()[-1:]}")
                elif status == "running":
                    out.append(f"{what} is still marked as running (its script did not finish)")
        for entry in _read_json(self._store.directory / BUILD).get("slides", []):
            if entry.get("problem"):
                out.append(f"slide {entry['key']!r} could not be built: {entry['problem']}")
        return out

    def show(self, message: str | None = None) -> None:
        """Opens the viewer in the user's VS Code (code flower sandbox only), saving it first."""
        path = self.save()
        command = shutil.which("codeflower-show")
        if not command:
            print(f"storyteller: {path} (codeflower-show is not available here)", file=sys.stderr)
            return
        arguments = [command, str(path)]
        if message:
            arguments += ["--message", message]
        subprocess.run(arguments, check=False)

    def __repr__(self) -> str:
        slides = len(_read_json(self._store.directory / BUILD).get("slides", [])) if self._store.directory else 0
        return f"<Deck {self._doc['meta'].get('title')!r}: {slides} slides, {self.pptx_path}>"
