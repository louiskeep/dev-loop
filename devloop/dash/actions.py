"""POST actions: the checks every write passes, and the handlers behind them.

`check_post` is the only reader of a POST body. It runs before any handler, in a fixed
order, so a new route cannot skip the length, token, origin or field checks. Handlers
call the same state functions the command line uses: `notes.answer`,
`store.set_paused` and `tick.spawn_detached`.
"""

import hmac
import re
import time
import urllib.parse

from .. import notes, store, tick

POST_BYTES = 8192
TICK_DEBOUNCE_S = 10
BAD = "Bad request."
STALE_TOKEN = "This page is out of date. Reload."
FOREIGN = "Refused: not sent from this page."
BAD_CHOICE = "That answer is not allowed for this question."
_DIGITS = re.compile(r"[0-9]+")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


class Refused(Exception):
    """A request the page answers with a fixed message and this status."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _read_form(handler):
    length = handler.headers.get("Content-Length")
    if length is None or _DIGITS.fullmatch(length) is None or int(length) > POST_BYTES:
        raise Refused(400, BAD)
    raw = handler.rfile.read(int(length))
    if len(raw) != int(length):
        raise Refused(400, BAD)
    try:
        text = raw.decode("utf-8")
        if _BAD_PERCENT.search(text):
            raise ValueError("percent escape")
        pairs = urllib.parse.parse_qs(
            text,
            keep_blank_values=True,
            strict_parsing=bool(text),
            max_num_fields=8,
            encoding="utf-8",
            errors="strict",
        )
    except ValueError:
        raise Refused(400, BAD) from None
    if any(len(values) != 1 for values in pairs.values()):
        raise Refused(400, BAD)
    return {name: values[0] for name, values in pairs.items()}


def _origin_ok(handler):
    sent = handler.headers.get("Origin") or handler.headers.get("Referer")
    host = handler.headers.get("Host")
    if not sent or not host:
        return False
    try:
        got = urllib.parse.urlsplit(sent)
        want = urllib.parse.urlsplit("//" + host)
        return (
            got.scheme == "http"
            and got.hostname is not None
            and got.hostname == want.hostname
            and (got.port or 80) == (want.port or 80)
        )
    except ValueError:
        return False


def check_post(handler, route) -> dict:
    """The form fields of a POST that passed every check; raises Refused otherwise."""
    form = _read_form(handler)
    token = form.get("t", "")
    if not hmac.compare_digest(token.encode(), handler.server.token.encode()):
        raise Refused(403, STALE_TOKEN)
    if not _origin_ok(handler):
        raise Refused(403, FOREIGN)
    required, one_of = route.fields
    given = set(form) - {"t"}
    modes = given & set(one_of)
    if one_of and len(modes) != 1:
        raise Refused(400, BAD)
    if given != set(required) | modes:
        raise Refused(400, BAD)
    return {name: form[name] for name in given}


def answer(handler, f) -> str:
    choice = f["choice"] if "choice" in f else notes.OTHER_PREFIX + f["other"].strip()
    try:
        return notes.answer(
            handler.server.project,
            f["key"],
            choice,
            expect_version=f["version"],
            via="dashboard",
        )
    except notes.NotesError as exc:
        if exc.status == 400:
            raise Refused(400, BAD_CHOICE) from None
        raise Refused(exc.status, str(exc)) from None


def pause(handler, f) -> str:
    store.set_paused(handler.server.project, True, "dashboard")
    return "Loop paused."


def resume(handler, f) -> str:
    store.set_paused(handler.server.project, False, "dashboard")
    return "Loop resumed."


def run_tick(handler, f) -> str:
    srv = handler.server
    # One lock across the check and the spawn, so two posts start one tick.
    with srv.tick_lock:
        if tick.running(srv.project, proc=srv.cache.proc):
            raise Refused(409, "A tick is running now.")
        last = srv.last_tick
        if last is not None and time.monotonic() - last < TICK_DEBOUNCE_S:
            raise Refused(409, "A tick was just started.")

        def started():
            srv.last_tick = time.monotonic()

        try:
            tick.spawn_detached(srv.project, "dashboard", on_started=started)
        except tick.TickEventError:
            return "Tick started, but its event could not be recorded."
        except OSError:
            raise Refused(500, "Could not start a tick.") from None
    return "Tick started."
