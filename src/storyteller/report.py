"""The Report: build a standalone HTML report piece by piece.

Two ways to use it:

- ``Report.open("reports/pbmc", title=...)`` keeps the report in a store next to the
  HTML (``reports/pbmc.report/``). Every change is saved and the HTML re-rendered
  within half a second, so the user watches the report grow. Sections have keys:
  re-running the script that writes a section replaces it in place, and several
  scripts can each fill their own sections of one report.
- ``Report("Title", path="reports/x.html")`` builds a report in memory and writes it
  with ``save()``, for one-off scripts.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import weakref
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from . import adapters, markdown, render, tables
from . import comments as reader_comments
from .images import Asset, shrink
from .interactive import explorer as ex
from .store import DiskStore, MemoryStore, asset_meta, empty_document, referenced_assets

DEFAULT_MAX_BYTES = 9_500_000  # VS Code's preview reads files up to 10 MB
SAVE_INTERVAL_S = 0.5
EXPLORER_MAX_ROWS = 200_000

CalloutKind = Literal["note", "tip", "success", "warning", "critical"]
Width = Literal["normal", "wide", "narrow"]

PROVENANCE_PACKAGES = (
    "storyteller",
    "numpy",
    "pandas",
    "polars",
    "scipy",
    "scikit-learn",
    "statsmodels",
    "matplotlib",
    "seaborn",
    "plotly",
    "anndata",
    "scanpy",
    "scvi-tools",
    "torch",
)


@dataclass(frozen=True)
class Metric:
    label: str
    value: Any
    note: str | None = None
    status: Literal["good", "warning", "critical"] | None = None


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "section"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _script_name() -> str | None:
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None) or (sys.argv[0] if sys.argv and sys.argv[0] else None)
    if not path or path in ("-c", "-m") or not str(path).endswith(".py"):
        return None
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except ValueError:
        return Path(path).name


def _find(nodes: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], int] | None:
    """The list holding the keyed node and its index, searching nested sections too."""
    for index, node in enumerate(nodes):
        if node.get("key") == key:
            return nodes, index
        found = _find(node.get("children", []), key)
        if found:
            return found
    return None


class Report:
    """A report under construction. See the module docstring and the skill for the workflow."""

    def __init__(
        self,
        title: str,
        path: str | Path | None = None,
        *,
        subtitle: str | None = None,
        author: str | None = None,
        date: str | None = None,
        page: Literal["A4", "letter"] = "A4",
        max_bytes: int = DEFAULT_MAX_BYTES,
        fonts: bool = True,
        base_dir: str | Path | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_bytes = max_bytes
        self.fonts = fonts
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._store: MemoryStore | DiskStore = MemoryStore()
        self._doc = empty_document()
        self._meta_changes: dict[str, Any] = {}
        # A report written once is final; a live report (Report.open) is a draft until finalize().
        self._set_meta(
            title=title,
            subtitle=subtitle,
            author=author,
            page=page,
            date=date or datetime.now().date().isoformat(),
            final=True,
        )
        self._stack: list[dict[str, Any]] = []  # open containers (sections, grids, tabs)
        self._touched: dict[str, None] = {}  # top-level or section keys this process wrote, in order
        self._removed: set[str] = set()
        self._auto_keys: dict[str, int] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_save = 0.0
        self._timer: threading.Timer | None = None
        self._live = False

    # -- opening and saving ---------------------------------------------------------------

    @classmethod
    def open(
        cls,
        path: str | Path,
        title: str | None = None,
        *,
        subtitle: str | None = None,
        author: str | None = None,
        page: Literal["A4", "letter"] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        fonts: bool = True,
        base_dir: str | Path | None = None,
    ) -> Report:
        """Opens (or creates) the report at `path` (``reports/pbmc`` or ``reports/pbmc.html``),
        stored in ``reports/pbmc.report/`` and saved live as it changes."""
        html_path = Path(path)
        if html_path.suffix.lower() not in (".html", ".htm"):
            html_path = html_path.with_name(html_path.name + ".html")
        store = DiskStore(html_path.with_suffix(".report"))
        existing = store.load()
        if existing is None and title is None:
            raise FileNotFoundError(f"no report at {store.directory}; pass title= to create one")
        report = cls(
            title or existing["meta"].get("title", "Report"),
            html_path,
            max_bytes=max_bytes,
            fonts=fonts,
            base_dir=base_dir,
        )
        report._store = store
        report._live = True
        report._meta_changes = {}
        report._doc["meta"]["final"] = False
        if existing is not None:
            report._doc = existing
        if existing is None or title is not None:
            report._set_meta(title=title or existing["meta"].get("title"))
        report._set_meta(subtitle=subtitle, author=author, page=page)
        if existing is None:
            report._set_meta(date=datetime.now().date().isoformat(), page=page or "A4")
        _open_reports.add(report)
        report.save()
        return report

    def _set_meta(self, **values: Any) -> None:
        for name, value in values.items():
            if value is None:
                continue
            self._doc["meta"][name] = value
            self._meta_changes[name] = value

    @property
    def store_dir(self) -> Path | None:
        return self._store.directory

    def _asset(self, sha: str) -> Asset:
        return self._store.get(sha, self._doc.get("assets"))

    def _put(self, asset: Asset) -> str:
        sha = self._store.put(asset)
        self._doc.setdefault("assets", {})[sha] = asset_meta(asset)
        return sha

    def render(
        self,
        *,
        draft: bool | None = None,
        live_url: str | None = None,
        comments: list[dict[str, Any]] | None = None,
    ) -> str:
        with self._lock:
            return render.render_document(
                self._ordered(), self._asset, draft=draft, live_url=live_url, fonts=self.fonts, comments=comments
            )

    def _comments(self, target: Path) -> list[dict[str, Any]]:
        """The readers' comments to keep in the page about to be written over `target`."""
        if isinstance(self._store, DiskStore) and target == self.path:
            return reader_comments.for_build(self._store.directory, target)
        try:
            return reader_comments.embedded(target.read_text(encoding="utf-8")) or []
        except (FileNotFoundError, UnicodeDecodeError):
            return []

    def _ordered(self) -> dict[str, Any]:
        """The document with summary and metrics first and provenance last, wherever they were added."""
        nodes = self._doc["nodes"]
        first = [n for n in nodes if n["type"] in ("summary",)]
        second = [n for n in nodes if n["type"] == "metrics" and n.get("key") == "metrics"]
        last = [n for n in nodes if n["type"] == "provenance"]
        middle = [n for n in nodes if n not in first and n not in second and n not in last]
        return {**self._doc, "nodes": first + second + middle + last}

    def save(self, path: str | Path | None = None) -> Path:
        """Writes the HTML (and, for a stored report, its document) now."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            target = Path(path) if path else self.path
            if target is None:
                raise ValueError("no path: pass save(path) or Report(..., path=...)")
            with self._store.lock():
                if self._live:
                    self._merge_from_disk()
                    self._doc["meta"]["updated"] = _now()
                    self._store.write(self._doc)
                html = self.render(comments=self._comments(target))
                from .store import atomic_write

                atomic_write(target, html.encode("utf-8"))
            self._dirty = False
            self._last_save = time.monotonic()
            # What was written is on disk now; later saves only carry later changes (and the open section).
            open_key = self._stack[0].get("key") if self._stack else None
            self._touched = {open_key: None} if open_key else {}
            self._removed.clear()
            self._meta_changes = {}
            return target

    def _merge_from_disk(self) -> None:
        """Applies this process's changes onto the document on disk, which other scripts may have changed."""
        disk = self._store.load()
        if disk is None:
            return
        local = self._doc
        merged = disk
        merged["meta"].update(self._meta_changes)
        merged.setdefault("assets", {}).update(local.get("assets", {}))
        for key in self._removed:
            found = _find(merged["nodes"], key)
            if found:
                del found[0][found[1]]
        local_keys = [node.get("key") for node in local["nodes"]]
        for key in self._touched:
            found_local = _find(local["nodes"], key)
            if not found_local:
                continue
            node = found_local[0][found_local[1]]
            found = _find(merged["nodes"], key)
            if found:
                found[0][found[1]] = node
                continue
            # New on disk: after the nearest earlier top-level sibling that disk already has.
            position = 0
            if key in local_keys:
                for previous in reversed(local_keys[: local_keys.index(key)]):
                    hit = next((i for i, n in enumerate(merged["nodes"]) if n.get("key") == previous), None)
                    if hit is not None:
                        position = hit + 1
                        break
                else:
                    position = len(merged["nodes"]) if local_keys.index(key) else 0
            else:
                position = len(merged["nodes"])
            merged["nodes"].insert(position, node)
        # Keep the open containers pointing into the merged tree.
        self._doc = merged
        self._rebind_stack()

    def _rebind_stack(self) -> None:
        if not self._stack:
            return
        top = self._stack[0]
        found = _find(self._doc["nodes"], top["key"]) if top.get("key") else None
        if found and found[0][found[1]] is not top:
            found[0][found[1]] = top  # this process's open section wins

    def _changed(self) -> None:
        """Schedules a save: at once if the last one was a while ago, else at the end of the interval."""
        self._dirty = True
        if not self._live:
            return
        wait = SAVE_INTERVAL_S - (time.monotonic() - self._last_save)
        if wait <= 0:
            self.save()
        elif self._timer is None:
            self._timer = threading.Timer(wait, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            self._timer = None
            if self._dirty:
                self.save()

    def close(self) -> None:
        """Saves pending changes (also done automatically when the script ends)."""
        with self._lock:
            if self._dirty and (self._live or self.path):
                self.save()

    def __enter__(self) -> Report:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- structure ---------------------------------------------------------------------------

    def _container(self) -> list[dict[str, Any]] | None:
        """The children list new blocks go into, or None at the top level."""
        if not self._stack:
            return None
        return self._stack[-1]["children"]

    def _auto_key(self, kind: str) -> str:
        script = Path(_script_name() or "session").stem
        count = self._auto_keys.get(kind, 0) + 1
        self._auto_keys[kind] = count
        return f"{slugify(script)}-{kind}-{count}"

    def _add(self, node: dict[str, Any], key: str | None = None) -> str | None:
        with self._lock:
            container = self._container()
            if container is not None:
                if key:
                    node["key"] = key
                container.append(node)
                self._touch_open()
            else:
                node["key"] = key or self._auto_key(node["type"])
                self._upsert_top(node)
            self._changed()
            return node.get("key")

    def _upsert_top(self, node: dict[str, Any]) -> None:
        found = _find(self._doc["nodes"], node["key"])
        if found:
            found[0][found[1]] = node
        else:
            self._doc["nodes"].append(node)
        self._touched[node["key"]] = None
        self._removed.discard(node["key"])

    def _touch_open(self) -> None:
        if self._stack:
            outermost = self._stack[0]
            outermost["updated"] = _now()
            self._touched[outermost["key"]] = None

    @contextlib.contextmanager
    def section(
        self, key_or_title: str, title: str | None = None, *, description: str | None = None
    ) -> Iterator[Report]:
        """A section: ``with report.section("qc", "Quality control"): ...``.

        Its content is replaced as a whole each time the block runs. If the block
        raises, the section shows the error and the exception propagates.
        """
        key, heading = (key_or_title, title) if title else (slugify(key_or_title), key_or_title)
        key = slugify(key)
        with self._lock:
            if self._live and self._doc["meta"].get("final"):
                self._set_meta(final=False)
            node = {
                "type": "section",
                "key": key,
                "title": heading,
                "status": "running",
                "children": [],
                "updated": _now(),
            }
            if description:
                node["description"] = description
            source = _script_name()
            if source:
                node["source"] = source
            container = self._container()
            if container is None:
                found = _find(self._doc["nodes"], key)
                if found and found[0][found[1]].get("description") and not description:
                    node["description"] = found[0][found[1]]["description"]
                self._upsert_top(node)
            else:
                container.append(node)
                self._touch_open()
            self._stack.append(node)
            self._changed()
        try:
            yield self
        except BaseException as error:
            with self._lock:
                node["status"] = "error"
                node["error"] = "".join(traceback.format_exception_only(type(error), error)).strip()[-2000:]
                self._stack.pop()
                if not self._stack:
                    self._touched[key] = None
                self._dirty = True
                if self._live:
                    self.save()
            raise
        else:
            with self._lock:
                node["status"] = "ready"
                node["updated"] = _now()
                self._stack.pop()
                self._touch_open()
                self._dirty = True
                if self._live and not self._stack:
                    self.save()
                else:
                    self._changed()

    def pending(self, key: str, title: str, *, note: str | None = None, description: str | None = None) -> None:
        """Announces a section that a later script will fill. An existing section keeps its content."""
        key = slugify(key)
        with self._lock:
            found = _find(self._doc["nodes"], key)
            if found and found[0][found[1]].get("status") not in ("pending", None):
                return
            node = {"type": "section", "key": key, "title": title, "status": "pending", "children": []}
            if note:
                node["note"] = note
            if description:
                node["description"] = description
            self._upsert_top(node)
            self._changed()

    @contextlib.contextmanager
    def grid(self, columns: int = 2) -> Iterator[Report]:
        """Blocks side by side: ``with report.grid(2): report.figure(a); report.figure(b)``."""
        node: dict[str, Any] = {"type": "grid", "columns": columns, "children": []}
        yield from self._open_container(node)

    @contextlib.contextmanager
    def tabs(self) -> Iterator[Report]:
        """Alternative views of one thing; each ``with report.tab("Title"):`` inside is one tab."""
        node: dict[str, Any] = {"type": "tabs", "children": []}
        yield from self._open_container(node)

    @contextlib.contextmanager
    def tab(self, title: str) -> Iterator[Report]:
        if not self._stack or self._stack[-1]["type"] != "tabs":
            raise RuntimeError("report.tab() belongs inside `with report.tabs():`")
        node: dict[str, Any] = {"type": "tab", "title": title, "children": []}
        with self._lock:
            self._stack[-1]["children"].append(node)
            self._stack.append(node)
        try:
            yield self
        finally:
            with self._lock:
                self._stack.pop()
                self._changed()

    def _open_container(self, node: dict[str, Any]) -> Iterator[Report]:
        with self._lock:
            container = self._container()
            if container is None:
                node["key"] = self._auto_key(node["type"])
                self._upsert_top(node)
            else:
                container.append(node)
                self._touch_open()
            self._stack.append(node)
        try:
            yield self
        finally:
            with self._lock:
                self._stack.pop()
                self._changed()

    def remove(self, key: str) -> bool:
        """Removes a section or keyed block. Returns whether it existed."""
        with self._lock:
            found = _find(self._doc["nodes"], key)
            if not found:
                return False
            del found[0][found[1]]
            self._removed.add(key)
            self._touched.pop(key, None)
            self._changed()
            return True

    def move(self, key: str, *, before: str | None = None, after: str | None = None) -> None:
        """Moves a top-level section or block before or after another one."""
        with self._lock:
            nodes = self._doc["nodes"]
            index = next((i for i, n in enumerate(nodes) if n.get("key") == key), None)
            if index is None:
                raise KeyError(f"no top-level section or block {key!r}")
            node = nodes.pop(index)
            anchor = before or after
            target = next((i for i, n in enumerate(nodes) if n.get("key") == anchor), None)
            if target is None:
                nodes.insert(index, node)
                raise KeyError(f"no top-level section or block {anchor!r}")
            nodes.insert(target if before else target + 1, node)
            # Order is not merged node by node: write the whole order.
            for other in nodes:
                if other.get("key"):
                    self._touched[other["key"]] = None
            self._changed()

    def outline(self) -> str:
        """The report's structure, one line per section or top-level block, with keys and status.
        Keyed blocks inside sections are listed as ``#key``: text links to them with ``[](#key)``."""
        final = self._doc["meta"].get("final", False)
        lines = [f"{self._doc['meta'].get('title', 'Report')}  ({'final' if final else 'draft'})"]
        nodes = self._ordered()["nodes"]
        numbering = render.Context(self._asset, draft=not final)
        numbering.number([n for n in nodes if not final or n.get("status") != "pending"])

        def keyed(children: list[dict[str, Any]], indent: str) -> None:
            for child in children:
                if child["type"] == "section":
                    continue
                target = numbering.targets.get(child.get("key") or "")
                if target and target.node is child:
                    label = f" ({target.label})" if target.label else ""
                    lines.append(f"{indent}#{child['key']}  {child['type']}{label}")
                keyed(child.get("children", []), indent)

        def walk(nodes: list[dict[str, Any]], depth: int) -> None:
            for node in nodes:
                indent = "  " * (depth + 1)
                if node["type"] == "section":
                    status = node.get("status", "ready")
                    blocks = sum(1 for child in node.get("children", []) if child["type"] != "section")
                    source = f"  from {node['source']}" if node.get("source") else ""
                    lines.append(f"{indent}[{node['key']}] {node['title']}  ({status}, {blocks} blocks){source}")
                    keyed(node.get("children", []), indent + "    ")
                    walk([c for c in node.get("children", []) if c["type"] == "section"], depth + 1)
                elif depth == 0:
                    lines.append(f"{indent}[{node.get('key')}] {node['type']}")

        walk(nodes, 0)
        return "\n".join(lines)

    # -- blocks --------------------------------------------------------------------------------

    def _markdown(self, text: str, allow_html: bool = False, heading_shift: int = 3) -> tuple[str, list[str]]:
        embedded: list[str] = []

        def embed(path: Path) -> str:
            sha = self._put(adapters.to_asset(path))
            embedded.append(sha)
            return markdown.ASSET_SCHEME + sha

        html = markdown.render(
            text, base_dir=self.base_dir, embed=embed, allow_html=allow_html, heading_shift=heading_shift
        )
        return html, embedded

    def text(self, text: str, *, key: str | None = None, allow_html: bool = False) -> str | None:
        """Markdown text. Local images (``![x](figures/a.png)``) are embedded."""
        html, embedded = self._markdown(text, allow_html)
        node: dict[str, Any] = {"type": "text", "html": html}
        if embedded:
            node["embedded"] = embedded
        return self._add(node, key)

    def summary(self, text: str, *, title: str = "Summary") -> None:
        """The findings box at the top of the report (one per report)."""
        html, embedded = self._markdown(text)
        node: dict[str, Any] = {"type": "summary", "html": html, "title": title, "key": "summary"}
        if embedded:
            node["embedded"] = embedded
        with self._lock:
            self._upsert_top(node)
            self._changed()

    def metrics(
        self,
        items: Mapping[str, Any] | Sequence[Metric | Mapping[str, Any] | tuple[Any, ...]],
        *,
        key: str | None = None,
    ) -> str | None:
        """Headline numbers as tiles: ``{"Cells": 10234}`` or ``[Metric("Cells", 10234, note="after QC")]``.

        Outside a section, one metrics row with the default key sits under the summary."""
        entries: list[dict[str, Any]] = []
        source = items.items() if isinstance(items, Mapping) else items
        for item in source:
            if isinstance(item, Metric):
                entry = {"label": item.label, "value": item.value, "note": item.note, "status": item.status}
            elif isinstance(item, Mapping):
                entry = dict(item)
            else:
                entry = dict(zip(("label", "value", "note", "status"), item, strict=False))
            value = entry["value"]
            entry["value"] = value if isinstance(value, str) else tables.format_value(value)
            entries.append({k: str(v) for k, v in entry.items() if v is not None})
        node = {"type": "metrics", "items": entries}
        if self._container() is None and key is None:
            key = "metrics"
        return self._add(node, key)

    def figure(
        self,
        figure: Any,
        caption: str | None = None,
        *,
        alt: str | None = None,
        width: Width = "normal",
        key: str | None = None,
        lossless: bool | None = None,
        max_px: int = 2000,
        close: bool = True,
        height: int | None = None,
    ) -> str | None:
        """A figure: a matplotlib figure or axes, seaborn grid, PIL image, array, image path or bytes.

        Plotly figures become interactive charts (see `plotly`). matplotlib figures are
        closed after embedding unless ``close=False``."""
        if adapters.is_plotly(figure) or (isinstance(figure, dict) and "data" in figure):
            return self.plotly(figure, caption, height=height, width=width, key=key)
        asset = adapters.to_asset(figure, max_px=max_px, lossless=lossless, close=close)
        node: dict[str, Any] = {"type": "figure", "asset": self._put(asset), "width": width}
        self._caption(node, caption)
        if alt:
            node["alt"] = alt
        return self._add(node, key)

    def _caption(self, node: dict[str, Any], caption: str | None) -> None:
        if caption:
            node["caption_html"] = markdown.render_inline(caption)
            node["caption_text"] = re.sub(r"[*_`]", "", caption)

    def plotly(
        self,
        figure: Any,
        caption: str | None = None,
        *,
        height: int | None = None,
        static: Any = None,
        width: Width = "normal",
        key: str | None = None,
    ) -> str | None:
        """An interactive plotly chart (a plotly Figure or its dict), themed like the report.

        `static` (a matplotlib figure or image) is shown where scripts cannot run."""
        spec, traces = adapters.plotly_spec(figure)
        data = json.dumps(spec, separators=(",", ":"), sort_keys=True).encode()
        node: dict[str, Any] = {
            "type": "plotly",
            "spec": self._put(Asset(data, "application/json")),
            "traces": sorted(traces),
            "height": height or spec["layout"].get("height") or 420,
            "width": width,
        }
        if static is not None:
            node["static"] = self._put(adapters.to_asset(static))
        self._caption(node, caption)
        return self._add(node, key)

    def table(
        self,
        data: Any,
        caption: str | None = None,
        *,
        format: Mapping[str, Any] | None = None,
        flags: Any = None,
        max_rows: int = 50,
        index: bool | None = None,
        sortable: bool = True,
        key: str | None = None,
    ) -> str | None:
        """A table from a DataFrame, dict of columns or list of rows.

        `format` maps column names to format strings (``"{:.1%}"``) or functions; `flags`
        highlights cells (a boolean per row, a frame of booleans, or a function(row, column))."""
        html, shape = tables.render(data, format=format, flags=flags, max_rows=max_rows, index=index, sortable=sortable)
        node: dict[str, Any] = {"type": "table", "html": html, "shape": shape}
        self._caption(node, caption)
        return self._add(node, key)

    def callout(self, kind: CalloutKind, text: str, *, title: str | None = None, key: str | None = None) -> str | None:
        """A highlighted note: note, tip, success, warning or critical."""
        if kind not in render.CALLOUT_ICONS:
            raise ValueError(f"callout kind must be one of {', '.join(render.CALLOUT_ICONS)}")
        html, embedded = self._markdown(text)
        node: dict[str, Any] = {"type": "callout", "kind": kind, "html": html}
        if title:
            node["title"] = title
        if embedded:
            node["embedded"] = embedded
        return self._add(node, key)

    def code(
        self,
        source: str | Path,
        *,
        language: str | None = None,
        title: str | None = None,
        collapsed: bool = False,
        key: str | None = None,
    ) -> str | None:
        """Source code: a string, or the path of a script (shown with its name)."""
        code = source
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and len(source) < 512):
            candidate = Path(source)
            if not candidate.is_absolute():
                candidate = self.base_dir / candidate
            if candidate.is_file():
                code = candidate.read_text(encoding="utf-8", errors="replace")
                title = title or str(source)
                language = language or {".py": "python", ".r": "r", ".R": "r", ".sh": "bash", ".sql": "sql"}.get(
                    candidate.suffix
                )
        node: dict[str, Any] = {"type": "code", "code": str(code), "collapsed": collapsed}
        if language:
            node["language"] = language
        if title:
            node["title"] = title
        highlighted = _highlight(str(code), language)
        if highlighted:
            node["html"] = highlighted
        return self._add(node, key)

    def html(
        self, html: str, *, requires: Sequence[Literal["d3", "plotly-cartesian"]] = (), key: str | None = None
    ) -> str | None:
        """Raw HTML, inserted as is (scripts included): for custom D3 graphics. Prefer the other blocks."""
        return self._add({"type": "html", "html": html, "requires": list(requires)}, key)

    def provenance(self, packages: Sequence[str] = PROVENANCE_PACKAGES) -> None:
        """An appendix with the date, Python, platform, package versions and the scripts used."""
        versions = {}
        for name in packages:
            with contextlib.suppress(metadata.PackageNotFoundError):
                versions[name] = metadata.version(name)
        scripts = sorted({node["source"] for node in self._doc["nodes"] if node.get("source")})
        node = {
            "type": "provenance",
            "key": "provenance",
            "generated": _now(),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "packages": versions,
            "scripts": scripts,
        }
        with self._lock:
            self._upsert_top(node)
            self._changed()

    def explorer(
        self,
        data: Any,
        controls: Sequence[ex.Control],
        views: Sequence[ex.View],
        *,
        title: str | None = None,
        caption: str | None = None,
        mode: ex.Mode = "highlight",
        max_rows: int = EXPLORER_MAX_ROWS,
        static: bool = True,
        key: str | None = None,
    ) -> str | None:
        """Linked controls and charts over one dataset, e.g. QC thresholds with live counts.

        The data (a DataFrame, or a dict of equal-length columns) is embedded once; rows
        beyond `max_rows` are sampled. A matplotlib snapshot at the initial settings is
        embedded for readers without scripts when `static` is true and matplotlib is installed."""
        import numpy as np

        from .interactive import pack as packing

        controls, views = list(controls), list(views)
        if not views:
            raise ValueError("an explorer needs at least one view")
        for view in views:
            if isinstance(view, ex.Stat):
                ex.check_template(view.template)
        names = ex.columns_used(controls, views)
        total = _row_count(data, names[0]) if names else 0
        note = None
        if total > max_rows:
            rows = np.sort(np.random.default_rng(0).choice(total, max_rows, replace=False))
            data = _take_rows(data, rows)
            note = f"A random sample of {max_rows:,} of {total:,} rows is embedded."
        blob, columns, n = packing.pack(data, names)
        values = packing.unpack(blob, columns)
        for column in columns:
            if column["kind"] == "f32":
                finite = values[column["name"]][np.isfinite(values[column["name"]])]
                column["min"] = float(finite.min()) if finite.size else 0.0
                column["max"] = float(finite.max()) if finite.size else 1.0
        kinds = {c["name"]: c["kind"] for c in columns}
        for control in controls:
            if isinstance(control, ex.Threshold) and kinds[control.column] != "f32":
                raise TypeError(f"Threshold({control.column!r}) needs a numeric column")
            if isinstance(control, ex.Select) and kinds[control.column] != "cat":
                raise TypeError(f"Select({control.column!r}) needs a categorical or text column")
            if isinstance(control, ex.Toggle) and kinds[control.column] != "u8":
                raise TypeError(f"Toggle({control.column!r}) needs a boolean column")
        subset, passes = ex.masks(values, controls)
        stats = ex.stats(subset, passes)
        spec = {
            "title": title,
            "mode": mode,
            "n": n,
            "columns": columns,
            "controls": [ex.spec_of(c) for c in controls],
            "views": [ex.spec_of(v) for v in views],
        }
        node: dict[str, Any] = {
            "type": "explorer",
            "spec": spec,
            "data": self._put(Asset(blob, "application/gzip")),
            "settings_text": ex.settings_text(controls),
            "static_stats": [ex.format_template(v.template, stats) for v in views if isinstance(v, ex.Stat)],
        }
        if static:
            snapshot = _explorer_snapshot(values, controls, views, subset, passes, mode)
            if snapshot is not None:
                node["static"] = self._put(snapshot)
        self._caption(node, " ".join(part for part in (caption, note) if part) or None)
        return self._add(node, key)

    # -- finishing -----------------------------------------------------------------------------------

    def finalize(self, *, provenance: bool = True, gc: bool = True) -> Path:
        """Marks the report final (no draft marks, no planned sections), fits it into the size
        budget, checks it is self-contained, and writes it. Returns the HTML path."""
        from . import check

        with self._lock:
            if self._live:
                with self._store.lock():
                    self._merge_from_disk()
            if provenance:
                self.provenance()
            self._set_meta(final=True)
            for message in self._section_warnings():
                print(f"storyteller: {message}", file=sys.stderr)
            for message in self.fit_budget():
                print(f"storyteller: {message}", file=sys.stderr)
            path = self.save()
            problems = check.check_file(path)
            for problem in problems:
                print(f"storyteller: {problem}", file=sys.stderr)
            if gc and isinstance(self._store, DiskStore):
                self._store.collect_garbage(self._doc)
            size = path.stat().st_size
            print(f"storyteller: wrote {path} ({size / 1e6:.1f} MB)", file=sys.stderr)
            return path

    def reopen_draft(self) -> None:
        """Turns a finalized report back into a draft (it is also done by any new section)."""
        self._set_meta(final=False)
        self._changed()

    def _section_warnings(self) -> list[str]:
        out = []
        for node in self._doc["nodes"]:
            if node["type"] != "section":
                continue
            if node.get("status") == "pending":
                out.append(f"section {node['key']!r} was planned but never written; it is left out of the final report")
            elif node.get("status") == "error":
                out.append(f"section {node['key']!r} failed: {node.get('error', '').splitlines()[-1:]}")
            elif node.get("status") == "running":
                out.append(f"section {node['key']!r} is still marked as running (its script did not finish)")
        seen: set[str] = set()
        for node, _ in render.walk(self._doc["nodes"]):
            key = node.get("key")
            if key and node["type"] != "tab":
                if key in seen:
                    out.append(f"key {key!r} is used by more than one block; links [](#{key}) lead to the first")
                seen.add(key)
        return out

    def fit_budget(self) -> list[str]:
        """Shrinks the largest raster images until the HTML fits `max_bytes`. Returns what it did."""
        messages: list[str] = []
        size = len(self.render(draft=False).encode())
        if size <= self.max_bytes:
            return messages
        original = size
        for _ in range(40):
            candidates = sorted(
                {sha for sha in referenced_assets(self._doc["nodes"]) if self._asset(sha).is_raster},
                key=lambda sha: len(self._asset(sha).data),
                reverse=True,
            )
            if not candidates:
                break
            sha = candidates[0]
            smaller = shrink(self._asset(sha))
            if len(smaller.data) >= len(self._asset(sha).data):
                break
            new_sha = self._put(smaller)
            _replace_asset(self._doc["nodes"], sha, new_sha)
            size = len(self.render(draft=False).encode())
            if size <= self.max_bytes:
                break
        if size < original:
            messages.append(f"shrank images to fit: {original / 1e6:.1f} MB → {size / 1e6:.1f} MB")
        if size > self.max_bytes:
            breakdown = self.size_breakdown()[:5]
            parts = ", ".join(f"{name} {n / 1e6:.1f} MB" for name, n in breakdown)
            messages.append(
                f"the report is {size / 1e6:.1f} MB, over the {self.max_bytes / 1e6:.1f} MB budget "
                f"(VS Code previews files up to 10 MB). Largest parts: {parts}"
            )
        # The shrunk images are part of the document now: save the change.
        for node in self._doc["nodes"]:
            if node.get("key"):
                self._touched[node["key"]] = None
        return messages

    def size_breakdown(self) -> list[tuple[str, int]]:
        """(part, bytes) of the largest parts, biggest first: plotly library, each section, fonts."""
        parts: list[tuple[str, int]] = []
        for node in self._doc["nodes"]:
            ctx = render.Context(self._asset, draft=False)
            html = render.render_node(node, ctx, 0)
            parts.append((f"{node['type']} {node.get('key')}", len(html.encode())))
            for library in ctx.libraries:
                if library in render.LIBRARIES:
                    parts.append((f"{library} library", len(render.asset_text(render.LIBRARIES[library]).encode())))
        unique = dict(sorted(parts, key=lambda p: p[1]))
        return sorted(unique.items(), key=lambda p: p[1], reverse=True)

    def show(self, message: str | None = None) -> None:
        """Opens the report in the user's VS Code (code flower sandbox only), saving it first."""
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
        sections = sum(1 for node in self._doc["nodes"] if node["type"] == "section")
        return f"<Report {self._doc['meta'].get('title')!r}: {sections} sections, {self.path}>"


def _replace_asset(nodes: list[dict[str, Any]], old: str, new: str) -> None:
    for node in nodes:
        for name in ("asset", "static"):
            if node.get(name) == old:
                node[name] = new
        if old in node.get("embedded", []):
            node["embedded"] = [new if sha == old else sha for sha in node["embedded"]]
            for field in ("html",):
                if field in node:
                    node[field] = node[field].replace(markdown.ASSET_SCHEME + old, markdown.ASSET_SCHEME + new)
        _replace_asset(node.get("children", []), old, new)


def _row_count(data: Any, column: str) -> int:
    from .interactive.pack import column_values

    return len(column_values(data, column))


def _take_rows(data: Any, rows: Any) -> Any:
    if hasattr(data, "to_pandas") and not hasattr(data, "iloc"):
        data = data.to_pandas()
    if hasattr(data, "iloc"):
        return data.iloc[rows]
    import numpy as np

    return {name: np.asarray(values)[rows] for name, values in data.items()}


def _explorer_snapshot(values, controls, views, subset, passes, mode) -> Asset | None:
    import importlib.util

    if importlib.util.find_spec("matplotlib") is None:
        return None
    from .interactive import static
    from .theme import mpl as theme_mpl

    theme_mpl.register_colormaps()
    figure = static.render(values, controls, views, subset, passes, mode)
    if figure is None:
        return None
    return adapters.from_matplotlib(figure, svg_max_bytes=0)


def _highlight(code: str, language: str | None) -> str | None:
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound
    except ImportError:
        return None
    if not language:
        return None
    try:
        lexer = get_lexer_by_name(language)
    except ClassNotFound:
        return None
    return highlight(code, lexer, HtmlFormatter(nowrap=True))


# Reports opened live are saved when the interpreter exits, in case the script ended
# between autosaves.
_open_reports: weakref.WeakSet[Report] = weakref.WeakSet()


@atexit.register
def _save_open_reports() -> None:
    for report in list(_open_reports):
        with contextlib.suppress(Exception):
            report.close()


def escape_text(text: str) -> str:  # re-exported for custom html blocks
    return escape(text)
