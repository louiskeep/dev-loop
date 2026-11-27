"""Routes for the dashboard: one table of exact (method, path) entries.

GET and HEAD never write state. The four POST routes go through `actions.check_post`.

`make_server` builds the HTTP server without touching signals or files, so tests can run
it in a thread. The lifecycle (lock, port, pid file, CLI) is in `lifecycle.py`.
"""

import collections
import hashlib
import http.server
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from .. import notes, store
from . import actions, render, transcript, view

WEB = Path(__file__).parent / "web"
CACHE_SECS = 2.0
# Fixed bodies for every error, so no exception text or request text is echoed back.
FIXED = {
    400: "bad request",
    404: "not found",
    405: "method not allowed",
    500: "state unreadable",
    501: "not supported",
}
HEADERS = (
    ("Cache-Control", "no-cache"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        ),
    ),
)
HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"
TEXT = "text/plain; charset=utf-8"


Route = collections.namedtuple("Route", "handler fields")


class ViewCache:
    """The sealed view, rebuilt at most every two seconds however many pages poll."""

    def __init__(self, project, proc="/proc"):
        self.project, self.proc = project, proc
        self._lock = threading.Lock()
        self._built = 0.0
        self._value = None
        # One reader cache for the server's life, so a poll reads only new bytes.
        self.transcripts = transcript.Cache()

    def get(self):
        with self._lock:
            if self._value is None or time.monotonic() - self._built > CACHE_SECS:
                self._value = view.build_view(
                    self.project, proc=self.proc, cache=self.transcripts
                )
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
    timeout = 20  # a stalled client cannot hold a thread forever

    # --- the one place a response is written ---------------------------------

    def _send(self, code, body, ctype=TEXT, etag=None, extra=(), no_store=False):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        if code != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
        for name, value in HEADERS:
            if not (no_store and name == "Cache-Control"):
                self.send_header(name, value)
        if no_store:
            self.send_header("Cache-Control", "no-store")
        for name, value in extra:
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
        route = ROUTES.get(("GET", self._path()))
        if route is None:
            return self.send_error(405 if ("POST", self._path()) in ROUTES else 404)
        try:
            route.handler(self)
        except Exception as exc:  # noqa: BLE001  the boundary: never leak, never die
            self.server.log(f"error {type(exc).__name__}")
            self._send(500, FIXED[500])

    def do_GET(self):
        self._dispatch()

    do_HEAD = do_GET

    def do_POST(self):
        path = self._path()
        route = ROUTES.get(("POST", path))
        if route is None:
            self.close_connection = True
            return self.send_error(405 if ("GET", path) in ROUTES else 404)
        action = path.rsplit("/", 1)[-1]
        try:
            fields = actions.check_post(self, route)
            message = route.handler(self, fields)
            self.server.log(f"post {action} ok")
            self._reply(200, True, message)
        except actions.Refused as exc:
            self.close_connection = True
            self.server.log(f"post {action} refused")
            self._reply(exc.code, False, exc.message)
        except Exception as exc:  # noqa: BLE001  the boundary: never leak, never die
            self.close_connection = True
            self.server.log(f"post {action} error {type(exc).__name__}")
            self._reply(500, False, SOMETHING_WRONG)

    def _reply(self, code, ok, message):
        if "application/json" in (self.headers.get("Accept") or ""):
            body = json.dumps({"ok": ok, "message": message})
            return self._send(code, body, JSON)
        if ok:
            return self._send(303, b"", extra=(("Location", "/"),))
        self._send(code, message)

    # --- handlers --------------------------------------------------------------

    def _html(self, render_fn):
        # The page carries the write token, so it is never cached or revalidated.
        v = self.server.cache.get()
        body = render_fn(v, token=self.server.token, copy_path=self._questions_path())
        self._send(200, body, HTML, no_store=True)

    def _questions_path(self):
        """Where the questions live, for the "Copy for Claude" text only."""
        try:
            return str(notes.questions_path(self.server.project))
        except (OSError, ValueError, notes.NotesError, store.StoreError):
            return ""

    def _conditional(self, make_body, ctype):
        v = self.server.cache.get()
        etag = _etag(v)
        if _matches(self.headers.get("If-None-Match"), etag):
            return self._send(304, b"", ctype, etag)
        self._send(200, make_body(v), ctype, etag)

    def page(self):
        self._html(render.render_page)

    def fragment(self):
        self._html(render.render_fragment)

    def api_state(self):
        self._conditional(lambda v: json.dumps(v, sort_keys=True), JSON)

    def static(self, name, ctype):
        self._send(200, self.server.assets[name], ctype)


SOMETHING_WRONG = "Something went wrong. Reload to see what was saved."
ROUTES = {
    ("GET", "/"): Route(Handler.page, None),
    ("GET", "/fragment"): Route(Handler.fragment, None),
    ("GET", "/api/state"): Route(Handler.api_state, None),
    ("GET", "/static/dash.css"): Route(
        lambda h: h.static("dash.css", "text/css; charset=utf-8"), None
    ),
    ("GET", "/static/dash.js"): Route(
        lambda h: h.static("dash.js", "text/javascript; charset=utf-8"), None
    ),
    ("POST", "/act/answer"): Route(
        actions.answer, (("key", "version"), ("choice", "other"))
    ),
    ("POST", "/act/pause"): Route(actions.pause, ((), ())),
    ("POST", "/act/resume"): Route(actions.resume, ((), ())),
    ("POST", "/act/tick"): Route(actions.run_tick, ((), ())),
}


class DashServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, project, proc="/proc", log=None):
        super().__init__(addr, Handler)
        self.project = project
        self.cache = ViewCache(project, proc)
        # One token per server process: a page from an older server is refused.
        self.token = secrets.token_urlsafe(32)
        self.tick_lock = threading.Lock()
        self.last_tick = None
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
