"""What reports and decks share: a JSON document of keyed nodes, kept in a store and saved live.

A live document (``Report.open``, ``Deck.open``) keeps its document in a store next to
its output. Every change is saved within half a second. Keyed containers (a report's
sections, a deck's sections and slides) are replaced as a whole each time the block
that fills them runs, and several scripts can each write their own parts: a save
merges this process's changes onto what is on disk.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
import weakref
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .images import Asset
from .store import DiskStore, MemoryStore, asset_meta, empty_document

SAVE_INTERVAL_S = 0.5


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "section"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def script_name() -> str | None:
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None) or (sys.argv[0] if sys.argv and sys.argv[0] else None)
    if not path or path in ("-c", "-m") or not str(path).endswith(".py"):
        return None
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except ValueError:
        return Path(path).name


def find(nodes: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], int] | None:
    """The list holding the keyed node and its index, searching nested containers too."""
    for index, node in enumerate(nodes):
        if node.get("key") == key:
            return nodes, index
        found = find(node.get("children", []), key)
        if found:
            return found
    return None


SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def running_agent(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The agent and model that run this script, as code flower's bridge records them
    (``$CODEFLOWER_SESSION_INFO_DIR/<session>.json``). The session is the one the agent
    names in its environment, or else the only chat with a turn running; outside code
    flower, nothing."""
    directory = environ.get("CODEFLOWER_SESSION_INFO_DIR")
    if not directory:
        return {}
    session = next((environ[name] for name in SESSION_ENV if environ.get(name)), None)
    if session is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session):
        return {}
    paths = [Path(directory) / f"{session}.json"] if session else sorted(Path(directory).glob("*.json"))
    found = []
    for path in paths:
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and (session or info.get("turn") is True):
            found.append(info)
    if len(found) != 1:
        return {}
    return {name: value for name in ("agent", "model") if isinstance(value := found[0].get(name), str) and value}


class LiveDocument:
    """The document, its store and live saving. Subclasses write their outputs in `_write`."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._store: MemoryStore | DiskStore = MemoryStore()
        self._doc = empty_document()
        self._meta_changes: dict[str, Any] = {}
        self._stack: list[dict[str, Any]] = []  # open containers (sections, slides, grids, tabs)
        self._touched: dict[str, None] = {}  # top-level or section keys this process wrote, in order
        self._removed: set[str] = set()
        self._auto_keys: dict[str, int] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_save = 0.0
        self._timer: threading.Timer | None = None
        self._live = False

    # -- opening and saving ---------------------------------------------------------------

    def _go_live(self, store: DiskStore, existing: dict[str, Any] | None) -> None:
        """Keeps the document in `store` from now on, starting from what it holds."""
        self._store = store
        self._live = True
        self._meta_changes = {}
        self._doc["meta"]["final"] = False
        if existing is not None:
            self._doc = existing
        _open_documents.add(self)

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

    def _write(self, target: Path) -> None:
        """Writes the outputs to `target`, under the store's lock, after the document."""
        raise NotImplementedError

    def _saved(self) -> None:
        """Called after each save, outside the store's lock."""

    def save(self, path: str | Path | None = None) -> Path:
        """Writes the outputs (and, for a stored document, the document) now."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            target = Path(path) if path else self.path
            if target is None:
                raise ValueError(f"no path: pass save(path) or {type(self).__name__}(..., path=...)")
            with self._store.lock():
                if self._live:
                    self._merge_from_disk()
                    self._doc["meta"]["updated"] = now()
                    self._store.write(self._doc)
                self._write(target)
            self._dirty = False
            self._last_save = time.monotonic()
            # What was written is on disk now; later saves only carry later changes (and the open container).
            open_key = self._stack[0].get("key") if self._stack else None
            self._touched = {open_key: None} if open_key else {}
            self._removed.clear()
            self._meta_changes = {}
        self._saved()
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
            found = find(merged["nodes"], key)
            if found:
                del found[0][found[1]]
        local_keys = [node.get("key") for node in local["nodes"]]
        for key in self._touched:
            found_local = find(local["nodes"], key)
            if not found_local:
                continue
            node = found_local[0][found_local[1]]
            found = find(merged["nodes"], key)
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
        found = find(self._doc["nodes"], top["key"]) if top.get("key") else None
        if found and found[0][found[1]] is not top:
            found[0][found[1]] = top  # this process's open container wins

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

    def __enter__(self):
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
        script = Path(script_name() or "session").stem
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
        found = find(self._doc["nodes"], node["key"])
        if found:
            found[0][found[1]] = node
        else:
            self._doc["nodes"].append(node)
        self._touched[node["key"]] = None
        self._removed.discard(node["key"])

    def _touch_open(self) -> None:
        if self._stack:
            outermost = self._stack[0]
            outermost["updated"] = now()
            self._touched[outermost["key"]] = None

    @contextlib.contextmanager
    def _filling(self, node: dict[str, Any]) -> Iterator[None]:
        """Runs the block that fills a keyed container (a section, a slide). The container is
        replaced as a whole; if the block raises, it shows the error and the exception propagates."""
        key = node["key"]
        with self._lock:
            if self._live and self._doc["meta"].get("final"):
                self._set_meta(final=False)
            source = script_name()
            if source:
                node["source"] = source
            container = self._container()
            if container is None:
                self._upsert_top(node)
            else:
                container.append(node)
                self._touch_open()
            self._stack.append(node)
            self._changed()
        try:
            yield
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
                node["updated"] = now()
                self._stack.pop()
                self._touch_open()
                self._dirty = True
                if self._live and not self._stack:
                    self.save()
                else:
                    self._changed()

    def remove(self, key: str) -> bool:
        """Removes a keyed container or block. Returns whether it existed."""
        with self._lock:
            found = find(self._doc["nodes"], key)
            if not found:
                return False
            del found[0][found[1]]
            self._removed.add(key)
            self._touched.pop(key, None)
            self._changed()
            return True

    def move(self, key: str, *, before: str | None = None, after: str | None = None) -> None:
        """Moves a top-level part before or after another one."""
        with self._lock:
            nodes = self._doc["nodes"]
            index = next((i for i, n in enumerate(nodes) if n.get("key") == key), None)
            if index is None:
                raise KeyError(f"no top-level part {key!r}")
            node = nodes.pop(index)
            anchor = before or after
            target = next((i for i, n in enumerate(nodes) if n.get("key") == anchor), None)
            if target is None:
                nodes.insert(index, node)
                raise KeyError(f"no top-level part {anchor!r}")
            nodes.insert(target if before else target + 1, node)
            # Order is not merged node by node: write the whole order.
            for other in nodes:
                if other.get("key"):
                    self._touched[other["key"]] = None
            self._changed()


_open_documents: weakref.WeakSet[LiveDocument] = weakref.WeakSet()


@atexit.register
def _save_open_documents() -> None:
    for document in list(_open_documents):
        with contextlib.suppress(Exception):
            document.close()


def close_first_at_exit() -> None:
    """Has open documents saved before the exit handlers registered so far run (the newest
    run first): a deck still draws its last slides with libraries that tear down at exit."""
    atexit.unregister(_save_open_documents)
    atexit.register(_save_open_documents)
