"""STATUS.md and the one-line tick post.

Public text is built from counts, IDs and fixed words only. Free text a model wrote
(next actions, purposes, errors) is never read here, so nothing planted in it can
reach Slack or the vault copy; `scrub` is the second layer, not the first.
"""

import collections
import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import runs, slices, store
from . import project as projmod
from .scrub import plain, scrub

CHICAGO = ZoneInfo("America/Chicago")
LINE_LIMIT = 300

__all__ = ["scrub", "render_status", "write_status", "tick_line", "summarize"]


def clock(dt=None) -> str:
    """HH:MM with the zone's own CST or CDT label."""
    dt = dt or datetime.datetime.now(datetime.timezone.utc)
    return dt.astimezone(CHICAGO).strftime("%H:%M %Z")


def events(project):
    path = store.state_path(project) / "events.jsonl"
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("at"), str):
            out.append(ev)
    return out


def _when(ev):
    try:
        t = datetime.datetime.fromisoformat(ev["at"])
    except (ValueError, TypeError, KeyError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)  # naive is UTC


def counts_from(merges, closures, now=None) -> dict:
    """Merge and closure counts from the two ledgers; None for a ledger that is None
    (unreadable), so a damaged file reads as unknown rather than zero."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(CHICAGO).date()
    week = now - datetime.timedelta(days=7)
    out = dict.fromkeys(("merged_today", "merged_week", "merged_total", "rows_closed"))
    # One malformed entry makes that whole ledger unreadable; the other one still counts.
    if not isinstance(merges, dict) or not all(
        isinstance(e, dict) for e in merges.values()
    ):
        merges = None
    if not isinstance(closures, dict) or not all(
        isinstance(e, dict) for e in closures.values()
    ):
        closures = None
    if merges is not None:
        stamps = [_when(e) for e in merges.values()]
        out["merged_today"] = sum(
            1 for t in stamps if t and t.astimezone(CHICAGO).date() == today
        )
        out["merged_week"] = sum(1 for t in stamps if t and t >= week)
        out["merged_total"] = len(merges)
    if closures is not None:
        out["rows_closed"] = sum(1 for r in closures.values() if r.get("closed"))
    return out


def counts(project, now=None) -> dict:
    return counts_from(
        slices.merge_records(project), slices.closure_records(project), now
    )


def _next_item(project) -> str:
    """One fixed-word pointer: the oldest active slice, or the state of the loop."""
    active = [s for s in slices.list_slices(project) if s["phase"] in slices.ACTIVE]
    if not active:
        return "pick the next roadmap row"
    s = min(active, key=lambda x: x["last_moved_at"])
    return f"{s['row']} {s['slice']} in {s['phase']}"


def render_status(project) -> str:
    c = counts(project)
    all_slices = slices.list_slices(project)
    phases = {}
    rows = {}
    for s in all_slices:
        phases[s["phase"]] = phases.get(s["phase"], 0) + 1
        rows.setdefault(s["row"], []).append(f"{s['slice']} {s['phase']}")
    running = runs.slot_usage(project)
    lines = [
        f"# Dev loop status, {clock()}",
        "",
        "## Merged",
        f"- Today: {c['merged_today']}",
        f"- This week: {c['merged_week']}",
        f"- Total: {c['merged_total']}",
        f"- Rows closed: {c['rows_closed']}",
        "",
        "## Slices by phase",
    ]
    lines += [f"- {p}: {n}" for p, n in sorted(phases.items())] or ["- none"]
    lines += ["", "## Progress by row"]
    lines += [
        f"- {row}: {', '.join(parts)}" for row, parts in sorted(rows.items())
    ] or ["- none"]
    lines += ["", "## Running"]
    cfg = projmod.load_project(project)
    lines += [
        f"- {projmod.pool_label(cfg, slot)}: {n}" for slot, n in sorted(running.items())
    ] or ["- nothing"]
    held = [s["key"] for s in all_slices if s["phase"] in ("HELD", "PARKED")]
    lines += ["", "## Held or parked"] + ([f"- {k}" for k in held] or ["- none"])
    problems = [
        e
        for e in store.read_json(store.state_dir(project) / "problems.json")
        .get("problems", {})
        .values()
        if e.get("open")
    ]
    lines += ["", "## Known problems"]
    lines += [f"- {e['key']} (x{e['count']})" for e in problems] or ["- none"]
    cand = [
        e["key"]
        for e in store.read_json(store.state_dir(project) / "lessons.json")
        .get("lessons", {})
        .values()
        if e.get("catalog_candidate")
    ]
    lines += ["", "## Lessons ready for the defect catalog"]
    lines += [f"- {k}" for k in cand] or ["- none"]
    # Per line, because scrub flattens whitespace.
    return "\n".join(scrub(line, 200) for line in lines) + "\n"


def write_status(project) -> None:
    text = render_status(project)
    store.write_text(store.state_dir(project) / "STATUS.md", text)
    copy = projmod.load_project(project).get("status_copy")
    if copy:
        Path(copy).parent.mkdir(parents=True, exist_ok=True)
        store.write_text(Path(copy), text)


# --- the tick line ------------------------------------------------------------

# One entry per event kind: its fixed wording and the tag the dashboard feed shows.
EventDesc = collections.namedtuple("EventDesc", "words tag")
EVENT_TAGS = (
    "DISPATCHED",
    "APPLIED",
    "MOVED",
    "MERGED",
    "CLOSED",
    "QUESTION",
    "DECIDED",
    "ANSWERED",
    "DROPPED",
    "PAUSED",
    "RESUMED",
    "TICK",
)
EVENTS = {
    "slice-moved": EventDesc(
        lambda e, cfg: f"moved {e.get('row')} {e.get('slice')} to {e.get('to')}",
        "MOVED",
    ),
    "merged": EventDesc(
        lambda e, cfg: f"merged {e.get('row')} {e.get('slice')}", "MERGED"
    ),
    "run-dispatched": EventDesc(
        lambda e, cfg: (
            f"started {projmod.role_label(cfg, e.get('run_kind'))} on {e.get('key')}"
        ),
        "DISPATCHED",
    ),
    "run-applied": EventDesc(
        lambda e, cfg: f"applied a run on {e.get('key')}", "APPLIED"
    ),
    "question-defaulted": EventDesc(
        lambda e, cfg: f"used the default for question {e.get('key')}", "DECIDED"
    ),
    "question-decided": EventDesc(
        lambda e, cfg: f"Tech lead decided question {e.get('key')}", "DECIDED"
    ),
    "question-answered": EventDesc(
        lambda e, cfg: f"you answered question {e.get('key')}", "ANSWERED"
    ),
    "question-waiting": EventDesc(
        lambda e, cfg: f"waiting on your answer to {e.get('key')}", "QUESTION"
    ),
    "row-closed": EventDesc(lambda e, cfg: f"closed row {e.get('row')}", "CLOSED"),
    "apply-mismatch": EventDesc(
        lambda e, cfg: f"dropped a stale result on {e.get('key')}", "DROPPED"
    ),
}
EVENT_KINDS = tuple(EVENTS)


def event_tag(kind):
    """The feed tag for an event kind, or None for a kind with no wording."""
    desc = EVENTS.get(kind)
    return desc.tag if desc else None


def event_words(ev, cfg):
    """One fixed-wording line for an event, or None for a kind with no wording.

    Event values come from `devloop-state event` too, so only plain IDs pass. The run
    kind is looked up in the role table, never printed.
    """
    desc = EVENTS.get(ev.get("kind"))
    if desc is None:
        return None
    safe = {k: plain(v) for k, v in ev.items() if k != "run_kind"}
    safe["run_kind"] = ev.get("run_kind")
    return desc.words(safe, cfg)


def summarize(project, since: str, limit=6) -> str:
    """Fixed words per event kind, from events after `since`; counts when there are many."""
    cfg = projmod.load_project(project)
    parts = []
    for ev in events(project):
        words = event_words(ev, cfg) if ev["at"] > since else None
        if words:
            parts.append(words)
    if len(parts) > limit:
        extra = len(parts) - limit
        parts = parts[:limit] + [f"{extra} more changes"]
    return "; ".join(parts) or "nothing changed"


def tick_line(project, outcome: str, summary: str = "", *, last_model_at=None) -> str:
    running = runs.slot_usage(project)
    cfg = projmod.load_project(project)
    pools = ", ".join(
        f"{projmod.pool_label(cfg, s)} {running.get(s, 0)}/{cfg['capacity'][s]}"
        for s in projmod.SLOTS
    )
    tail = f" Running: {pools}. Next: {_next_item(project)}."
    head = f"Dev loop {clock()}: "
    if outcome == "skipped":
        since = clock(last_model_at) if last_model_at else "the last tick"
        body = f"no change since {since}, {sum(running.values())} running"
        return scrub(head + body + ".", LINE_LIMIT)
    body = {
        "model": summary or "nothing to do",
        "paused": "paused, collecting finished work only",
        "capped": "waiting for the usage cap to clear",
        "failed": "the tick failed, see the cron log",
    }[outcome]
    return scrub(head + body + "." + tail, LINE_LIMIT)
