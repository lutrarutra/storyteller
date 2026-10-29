"""`storyteller serve`: the report in a browser, updated in place as scripts change it.

A small standard-library HTTP server on 127.0.0.1. It serves the report's HTML
and an event stream (Server-Sent Events) that announces each new version; the
page's runtime then fetches it and patches only the changed sections, keeping
scroll position and explorer settings. Inside code flower's sandbox the VS Code
preview does the same without a server; this is for running elsewhere.

Comments the reader leaves in the page come back as a POST to /comments and are
saved into the report (see comments.py).
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import comments

EVENTS_PATH = "/events"
COMMENTS_PATH = "/comments"
MAX_COMMENTS_BYTES = 2 * 1024 * 1024
POLL_S = 0.5
LIVE_META = f'<meta name="cf-live" content="{EVENTS_PATH}">'


def _version(path: Path) -> tuple[float, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0.0, 0)
    return (stat.st_mtime, stat.st_size)


def serve(html_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    html_path = Path(html_path).resolve()
    changed = threading.Condition()
    state = {"version": _version(html_path)}

    def watch() -> None:
        while True:
            time.sleep(POLL_S)
            version = _version(html_path)
            if version != state["version"]:
                with changed:
                    state["version"] = version
                    changed.notify_all()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet server
            pass

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?")[0] == EVENTS_PATH:
                self._events()
                return
            if self.path.split("?")[0] not in ("/", f"/{html_path.name}"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                html = html_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                html = "<!doctype html><title>Waiting</title><p>The report has not been written yet.</p>"
            html = html.replace("<head>", f"<head>{LIVE_META}", 1) if '<meta name="cf-live"' not in html else html
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?")[0] != COMMENTS_PATH:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            # Only this page may write: a JSON body needs a CORS preflight from other sites,
            # which this server never grants, and a browser names the origin of any POST.
            origin = self.headers.get("Origin")
            same = origin is None or origin == f"http://{self.headers.get('Host')}"
            if not same or self.headers.get_content_type() != "application/json":
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_COMMENTS_BYTES:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            try:
                saved = comments.save(html_path, json.loads(self.rfile.read(length)))
            except (ValueError, FileNotFoundError) as error:
                self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            body = json.dumps({"saved": len(saved)}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = state["version"]
            try:
                while True:
                    with changed:
                        changed.wait(timeout=15)
                        version = state["version"]
                    if version != seen:
                        seen = version
                        self.wfile.write(b"event: update\ndata: {}\n\n")
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    threading.Thread(target=watch, daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"Serving {html_path} at http://{host}:{server.server_port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
