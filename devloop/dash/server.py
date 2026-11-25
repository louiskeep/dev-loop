"""Routes for the dashboard: five exact paths, GET and HEAD only, no state writes.

`make_server` builds the HTTP server without touching signals or files, so tests can run
it in a thread. The lifecycle (lock, port, pid file, CLI) is in `lifecycle.py`.
"""

import hashlib
import http.server
import json
import sys
import threading
import time
from pathlib import Path

from . import render, view

WEB = Path(__file__).parent / "web"
CACHE_SECS = 2.0
# Fixed bodies for every error, so no exception text or request text is echoed back.
FIXED = {
    400: "bad request",
    404: "not found",
    500: "state unreadable",
    501: "not supported",
}
HEADERS = (
    ("Cache-Control", "no-cache"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
)
HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"
TEXT = "text/plain; charset=utf-8"


class ViewCache:
    """The sealed view, rebuilt at most every two seconds however many pages poll."""

    def __init__(self, project, proc="/proc"):
        self.project, self.proc = project, proc
        self._lock = threading.Lock()
        self._built = 0.0
        self._value = None

    def get(self):
        with self._lock:
            if self._value is None or time.monotonic() - self._built > CACHE_SECS:
                self._value = view.build_view(self.project, proc=self.proc)
                self._built = time.monotonic()
            return self._value


def _etag(v) -> str:
    return '"' + hashlib.sha256(v["version"].encode()).hexdigest()[:16] + '"'


def _matches(header, etag) -> bool:
    if not header:
        return False
    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "devloop-dash"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- the one place a response is written ---------------------------------

    def _send(self, code, body, ctype=TEXT, etag=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        if code != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
        for name, value in HEADERS:
            self.send_header(name, value)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD" and code != 304:
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._send(code, FIXED.get(code, "error"))

    def log_message(self, fmt, *args):
        pass  # replaced by log_request below: method, path and status only

    def log_request(self, code="-", size="-"):
        self.server.log(f"{self.command} {self._path()} {code}")

    def log_error(self, fmt, *args):
        pass

    # --- routing ---------------------------------------------------------------

    def _path(self):
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _dispatch(self):
        handler = ROUTES.get(self._path())
        if handler is None:
            return self.send_error(404)
        try:
            handler(self)
        except Exception as exc:  # noqa: BLE001  the boundary: never leak, never die
            self.server.log(f"error {type(exc).__name__}")
            self._send(500, FIXED[500])

    def do_GET(self):
        self._dispatch()

    do_HEAD = do_GET

    # --- handlers --------------------------------------------------------------

    def _conditional(self, make_body, ctype):
        v = self.server.cache.get()
        etag = _etag(v)
        if _matches(self.headers.get("If-None-Match"), etag):
            return self._send(304, b"", ctype, etag)
        self._send(200, make_body(v), ctype, etag)

    def page(self):
        self._conditional(render.render_page, HTML)

    def fragment(self):
        self._conditional(render.render_fragment, HTML)

    def api_state(self):
        self._conditional(lambda v: json.dumps(v, sort_keys=True), JSON)

    def static(self, name, ctype):
        self._send(200, self.server.assets[name], ctype)


ROUTES = {
    "/": Handler.page,
    "/fragment": Handler.fragment,
    "/api/state": Handler.api_state,
    "/static/dash.css": lambda h: h.static("dash.css", "text/css; charset=utf-8"),
    "/static/dash.js": lambda h: h.static("dash.js", "text/javascript; charset=utf-8"),
}


class DashServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, project, proc="/proc", log=None):
        super().__init__(addr, Handler)
        self.cache = ViewCache(project, proc)
        # Read once: a request never builds a path from its URL.
        self.assets = {n: (WEB / n).read_bytes() for n in ("dash.css", "dash.js")}
        self._log = log or sys.stderr
        self._log_lock = threading.Lock()

    def log(self, line):
        with self._log_lock:
            print(line, file=self._log, flush=True)


def make_server(project, host="127.0.0.1", port=0, proc="/proc", log=None):
    """A server for one project. It installs no signal handlers and writes no files."""
    return DashServer((host, port), project, proc, log)
