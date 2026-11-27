"""Usage-cap detection, shared by the worker wrapper and the tick."""

import datetime
import json
import re

CAP_TEXT = re.compile(r"(usage|rate)[ _]limit", re.IGNORECASE)
TRY_AGAIN = re.compile(r"try again|resets?\b", re.IGNORECASE)
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?")
TAIL_CHARS = 20000


def utc_now():
    return datetime.datetime.now(datetime.UTC)


def reset_time(value, text=""):
    """A reset moment from an epoch, an ISO string or free text; one hour on failure."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.datetime.fromtimestamp(value, datetime.UTC)
    for candidate in (value, *(ISO.findall(text) if isinstance(text, str) else [])):
        if isinstance(candidate, str):
            try:
                at = datetime.datetime.fromisoformat(candidate)
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=datetime.UTC)
            if at > utc_now():
                return at
    return utc_now() + datetime.timedelta(hours=1)


def detect_cap(rc, output):
    """When a Claude usage cap ends, or None. Exit 124 is a timeout, never a cap."""
    if rc == 124:
        return None
    for line in str(output).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "rate_limit":
            return reset_time(event.get("resets_at"), line)
        if (
            event.get("type") == "result"
            and (event.get("is_error") or rc not in (0, None))
            and CAP_TEXT.search(str(event.get("result", "")))
        ):
            return reset_time(None, str(event.get("result", "")))
    return None


def detect_cap_text(rc, text):
    """Plain-text cap message (Codex): a usage or rate limit with a try-again or reset hint.

    Only for a failed run; a clean review may talk about limits in its findings.
    """
    if rc in (0, None, 124):
        return None
    tail = str(text)[-TAIL_CHARS:]
    if CAP_TEXT.search(tail) and TRY_AGAIN.search(tail):
        return reset_time(None, tail)
    return None
