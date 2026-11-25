"""The tick: lock, collect, snapshot, skip or call the model, render, post."""

import datetime
import json
import re

CAP_TEXT = re.compile(r"(usage|rate)[ _]limit", re.IGNORECASE)
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?")


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _reset_time(value, text=""):
    """A reset moment from an epoch, an ISO string or free text; one hour on failure."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
    for candidate in (value, *(ISO.findall(text) if isinstance(text, str) else [])):
        if isinstance(candidate, str):
            try:
                at = datetime.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=datetime.timezone.utc)
            if at > _utc_now():
                return at
    return _utc_now() + datetime.timedelta(hours=1)


def detect_cap(rc, output):
    """When a usage cap ends, or None. Exit 124 is a timeout, never a cap."""
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
            return _reset_time(event.get("resets_at"), line)
        if (
            event.get("type") == "result"
            and (event.get("is_error") or rc not in (0, None))
            and CAP_TEXT.search(str(event.get("result", "")))
        ):
            return _reset_time(None, str(event.get("result", "")))
    return None
