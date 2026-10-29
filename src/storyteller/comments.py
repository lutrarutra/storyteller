"""Comments readers leave on a report: notes for other readers, and requests for the agent.

In the page (assets/comments.js) a reader selects text, right-clicks and writes a
comment. A comment is anchored by the text it is about, not by its place in the page,
so it finds that text again after the report changes:

    {"id": "c-k3x9p2aa", "kind": "note" | "request", "status": "open" | "resolved",
     "body": "Is 500 genes too strict for donor B?",
     "anchor": {"part": "section:qc", "near": "b-qc-text-2", "exact": "6,000 cells pass QC",
                "prefix": "…32 characters before", "suffix": "32 characters after…", "start": 120},
     "created": "2026-10-29T10:00:00Z", "updated": "…",
     "replies": [{"author": "agent", "body": "Kept it: …", "created": "…"}]}

A stored report keeps them in ``<name>.report/comments.json``, apart from report.json,
so the reader and the analysis scripts never overwrite each other's changes; every
build embeds them in the page's ``<script type="application/json" id="cf-comments">``.
An HTML file on its own (a copy the reader saved, a report built with ``save()``)
carries them only there.

Comments reach the store from the VS Code tab (through the code flower worker), from
``storyteller serve`` (a POST to /comments), or from a saved copy (``storyteller
comments import``).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .store import atomic_write

FILE_NAME = "comments.json"
SLOT_OPEN = '<script type="application/json" id="cf-comments">'
_SLOT = re.compile(re.escape(SLOT_OPEN) + r"(.*?)</script>", re.DOTALL)
KINDS = ("note", "request")
STATUSES = ("open", "resolved")
MAX_COMMENTS = 1000
MAX_BODY = 20_000
_ANCHOR_FIELDS = {"part": 200, "near": 200, "exact": 5000, "prefix": 200, "suffix": 200}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_json(comments: list[dict[str, Any]]) -> str:
    """The slot's text: JSON with every ``<`` escaped, so nothing in a comment can end the script."""
    return json.dumps(comments, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def clean(value: Any) -> list[dict[str, Any]]:
    """The comments in `value` that have the expected shape, with only the known fields.
    They come from a page anyone may have edited, so nothing else is trusted."""
    if not isinstance(value, list):
        raise ValueError("comments must be a JSON list")
    result = []
    for item in value[:MAX_COMMENTS]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("body"), str):
            continue
        anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
        comment: dict[str, Any] = {
            "id": item["id"][:40],
            "kind": item.get("kind") if item.get("kind") in KINDS else "note",
            "status": item.get("status") if item.get("status") in STATUSES else "open",
            "body": item["body"][:MAX_BODY],
            "anchor": {
                name: anchor[name][:limit]
                for name, limit in _ANCHOR_FIELDS.items()
                if isinstance(anchor.get(name), str)
            },
        }
        if isinstance(anchor.get("start"), int):
            comment["anchor"]["start"] = anchor["start"]
        for name in ("created", "updated"):
            if isinstance(item.get(name), str):
                comment[name] = item[name][:40]
        replies = [
            {
                "author": entry["author"][:80] if isinstance(entry.get("author"), str) else "agent",
                "body": entry["body"][:MAX_BODY],
                **({"created": entry["created"][:40]} if isinstance(entry.get("created"), str) else {}),
            }
            for entry in (item["replies"] if isinstance(item.get("replies"), list) else [])
            if isinstance(entry, dict) and isinstance(entry.get("body"), str)
        ]
        if replies:
            comment["replies"] = replies
        result.append(comment)
    return result


# -- in the HTML ---------------------------------------------------------------------------


def embedded(html: str) -> list[dict[str, Any]] | None:
    """The comments in a page's slot; None for a page without one (not storyteller's, or older)."""
    match = _SLOT.search(html)
    if not match:
        return None
    try:
        return clean(json.loads(match.group(1) or "[]"))
    except ValueError:
        return []


def embed(html: str, comments: list[dict[str, Any]]) -> str:
    """The page with `comments` in its slot."""
    text = to_json(comments)
    replaced, count = _SLOT.subn(lambda _: SLOT_OPEN + text + "</script>", html, count=1)
    if not count:
        raise ValueError("this page has no comment slot: rebuild it with storyteller 0.2 or later")
    return replaced


# -- where a report's comments live ----------------------------------------------------------


def paths(report: str | Path) -> tuple[Path, Path]:
    """The HTML file and the store directory for ``reports/pbmc``, ``reports/pbmc.html`` or
    ``reports/pbmc.report``."""
    path = Path(report)
    if path.suffix.lower() in (".html", ".htm"):
        return path, path.with_suffix(".report")
    if path.suffix == ".report":
        return path.with_suffix(".html"), path
    return path.with_name(path.name + ".html"), path.with_name(path.name + ".report")


def _stored(store: Path) -> bool:
    return (store / "report.json").is_file()


def _read_store(store: Path) -> list[dict[str, Any]] | None:
    try:
        return clean(json.loads((store / FILE_NAME).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except ValueError:
        return []


def _write_store(store: Path, comments: list[dict[str, Any]]) -> None:
    text = json.dumps(comments, indent=1, ensure_ascii=False)
    atomic_write(store / FILE_NAME, text.encode("utf-8"))


def for_build(store: Path, html_path: Path) -> list[dict[str, Any]]:
    """The comments to embed when a stored report is built (called under the store's lock).

    The store's file wins, except when the HTML is newer and carries other comments: the
    reader saved over it from a browser. Those are adopted into the store, so the build
    that is about to overwrite the HTML keeps them."""
    stored = _read_store(store)
    try:
        page_newer = stored is None or html_path.stat().st_mtime > (store / FILE_NAME).stat().st_mtime
        in_page = embedded(html_path.read_text(encoding="utf-8")) if page_newer else None
    except FileNotFoundError:
        in_page = None
    if in_page is not None and in_page != (stored or []):
        _write_store(store, in_page)
        return in_page
    return stored or []


def load(report: str | Path) -> list[dict[str, Any]]:
    """A report's comments: from its store when it has one, else from the HTML file."""
    html_path, store = paths(report)
    if _stored(store):
        stored = _read_store(store)
        if stored is not None:
            return stored
    if not html_path.is_file():
        raise FileNotFoundError(f"no report at {html_path}")
    return embedded(html_path.read_text(encoding="utf-8")) or []


def save(report: str | Path, comments: Any) -> list[dict[str, Any]]:
    """Replaces a report's comments: in its store (when it has one) and in the HTML file.
    Returns them as stored."""
    from .store import DiskStore

    html_path, store = paths(report)
    comments = clean(comments)
    if _stored(store):
        with DiskStore(store).lock():
            _write_store(store, comments)
            _embed_file(html_path, comments)
    else:
        if not html_path.is_file():
            raise FileNotFoundError(f"no report at {html_path}")
        _embed_file(html_path, comments)
    return comments


def _embed_file(html_path: Path, comments: list[dict[str, Any]]) -> None:
    try:
        html = html_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return  # the next build embeds them
    atomic_write(html_path, embed(html, comments).encode("utf-8"))


# -- for the agent ------------------------------------------------------------------------------


def find(comments: list[dict[str, Any]], comment_id: str) -> dict[str, Any]:
    for comment in comments:
        if comment["id"] == comment_id:
            return comment
    raise KeyError(f"no comment {comment_id!r}")


def reply(
    report: str | Path, comment_id: str, body: str, *, resolve: bool = False, author: str = "agent"
) -> dict[str, Any]:
    """Adds a reply to a comment, and marks it resolved if asked. Returns the comment."""
    comments = load(report)
    comment = find(comments, comment_id)
    if body:
        comment.setdefault("replies", []).append({"author": author, "body": body, "created": _now()})
    if resolve:
        comment["status"] = "resolved"
    comment["updated"] = _now()
    save(report, comments)
    return comment


def merge(report: str | Path, other: list[dict[str, Any]]) -> tuple[int, int]:
    """Adds the comments in `other` (e.g. from a copy a reader saved) to the report's: new ids
    are added, and known ones replaced when the other copy changed them later. Returns
    (added, updated)."""
    comments = load(report)
    by_id = {comment["id"]: index for index, comment in enumerate(comments)}
    added = updated = 0
    for comment in clean(other):
        index = by_id.get(comment["id"])
        if index is None:
            comments.append(comment)
            added += 1
        elif comment != comments[index] and _stamp(comment) > _stamp(comments[index]):
            comments[index] = comment
            updated += 1
    if added or updated:
        save(report, comments)
    return added, updated


def _stamp(comment: dict[str, Any]) -> str:
    return comment.get("updated") or comment.get("created") or ""


def describe(comment: dict[str, Any]) -> str:
    """A comment as the agent reads it in `storyteller comments list`."""
    anchor = comment.get("anchor", {})
    where = anchor.get("part", "?")
    if anchor.get("near"):
        where += f" (near #{anchor['near']})"
    kind = "request for you" if comment["kind"] == "request" else "note"
    lines = [f"{comment['id']}  {kind}, {comment['status']}  in {where}"]
    if anchor.get("exact"):
        quote = " ".join(anchor["exact"].split())
        lines.append(f'  on: "{quote[:300]}{"…" if len(quote) > 300 else ""}"')
    lines.extend("  > " + line for line in comment["body"].splitlines() or [""])
    for item in comment.get("replies", []):
        lines.append(f"  {item.get('author', 'agent')}: {item['body']}")
    return "\n".join(lines)
