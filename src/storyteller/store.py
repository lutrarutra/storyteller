"""Where a report's document and assets live: in memory, or in a directory next to the HTML.

A store directory (``reports/pbmc.report/``) holds:

- ``report.json``: the document (metadata, the tree of sections and blocks, and an
  index of the assets it uses);
- ``assets/<sha256>.<ext>``: encoded images, plotly specs and packed datasets,
  written once and named by their content;
- ``.lock``: taken while a process reads, merges and writes the document, so
  several scripts can fill one report.

Files are written to a temporary name and renamed into place, so a reader (the
VS Code preview polling the HTML) never sees half a file.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .images import EXTENSION_MIMES, MIME_EXTENSIONS, Asset

SCHEMA = 1


def empty_document() -> dict[str, Any]:
    return {"schema": SCHEMA, "meta": {}, "nodes": [], "assets": {}}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def asset_meta(asset: Asset) -> dict[str, Any]:
    meta: dict[str, Any] = {"mime": asset.mime}
    if asset.width:
        meta["width"] = asset.width
    if asset.height:
        meta["height"] = asset.height
    if asset.density != 1.0:
        meta["density"] = asset.density
    return meta


class MemoryStore:
    """Assets kept in memory, for a report written once with `save()`."""

    directory: Path | None = None

    def __init__(self) -> None:
        self._assets: dict[str, Asset] = {}

    def put(self, asset: Asset) -> str:
        self._assets.setdefault(asset.sha, asset)
        return asset.sha

    def get(self, sha: str, index: dict[str, Any] | None = None) -> Asset:
        try:
            return self._assets[sha]
        except KeyError:
            raise KeyError(f"unknown asset {sha}") from None

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        yield

    def load(self) -> dict[str, Any] | None:
        return None

    def write(self, document: dict[str, Any]) -> None:
        pass


class DiskStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.assets_dir = directory / "assets"
        self.document_path = directory / "report.json"
        self._cache: dict[str, Asset] = {}

    def _asset_path(self, sha: str, mime: str) -> Path:
        return self.assets_dir / f"{sha}.{MIME_EXTENSIONS.get(mime, 'bin')}"

    def put(self, asset: Asset) -> str:
        sha = asset.sha
        path = self._asset_path(sha, asset.mime)
        if not path.exists():
            atomic_write(path, asset.data)
        self._cache[sha] = asset
        return sha

    def get(self, sha: str, index: dict[str, Any] | None = None) -> Asset:
        if sha in self._cache:
            return self._cache[sha]
        meta = (index or {}).get(sha)
        if meta is None:
            matches = list(self.assets_dir.glob(f"{sha}.*"))
            if not matches:
                raise KeyError(f"asset {sha} is missing from {self.assets_dir}")
            meta = {"mime": EXTENSION_MIMES.get(matches[0].suffix[1:], "application/octet-stream")}
        path = self._asset_path(sha, meta["mime"])
        asset = Asset(path.read_bytes(), meta["mime"], meta.get("width"), meta.get("height"), meta.get("density", 1.0))
        self._cache[sha] = asset
        return asset

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / ".lock", "a+") as handle:
            try:
                import fcntl
            except ImportError:  # Windows: one writer at a time is the caller's job
                yield
                return
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> dict[str, Any] | None:
        try:
            document = json.loads(self.document_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if document.get("schema", SCHEMA) > SCHEMA:
            raise RuntimeError(f"{self.document_path} was written by a newer storyteller; upgrade to read it")
        return document

    def write(self, document: dict[str, Any]) -> None:
        text = json.dumps(document, indent=1, ensure_ascii=False, sort_keys=False)
        atomic_write(self.document_path, text.encode("utf-8"))

    def collect_garbage(self, document: dict[str, Any], min_age_s: float = 600) -> int:
        """Deletes asset files no node uses that are older than `min_age_s` (newer ones may belong
        to a script that has not saved yet). Returns how many were removed."""
        import time

        used = set(referenced_assets(document["nodes"]))
        removed = 0
        now = time.time()
        for path in self.assets_dir.glob("*.*"):
            if path.stem not in used and now - path.stat().st_mtime > min_age_s:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


ASSET_FIELDS = ("asset", "static", "spec", "data")


def referenced_assets(nodes: list[dict[str, Any]]) -> Iterator[str]:
    for node in nodes:
        for name in ASSET_FIELDS:
            value = node.get(name)
            if isinstance(value, str) and len(value) == 64:
                yield value
        yield from node.get("embedded", [])
        yield from referenced_assets(node.get("children", []))
