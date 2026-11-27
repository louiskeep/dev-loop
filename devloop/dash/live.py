"""The live part of the view: worker cards, the activity feed, KPIs and slice drawers.

Facts come from `transcript.Cache` (tool kind tags, allowlisted names, counts) and from
the run directories. Nothing here passes free text on; `view._seal` checks every value
again, so a field added here without a schema entry cannot reach a page.
"""

import datetime
import hashlib

from .. import runs, slices, status, store
from ..scrub import scrub
from . import transcript

MAX_LIVE = 16  # executing runs given a card, newest first
MAX_DRAWER_FILES = 8  # finished-run transcripts parsed per build
DRAWER_RUNS = 8  # runs listed per slice drawer
DRAWER_SLICES = 60  # active slices given a drawer source
SUMMARY_LIMIT = 120
DRAWER_CALLS = 20
DRAWER_FILES = 12
HEADER_BYTES = 8192  # result.md is read for its header only, never whole

# Role icons are a fixed set; the page draws each one as inline SVG.
ICONS = {
    "planner": "plan",
    "builder": "build",
    "codex-builder": "build",
    "dennis": "review",
    "codex-review": "review",
    "codex-fix": "plan",
    "test": "test",
    "docs": "docs",
    "duty": "duty",
}
ICON_IDS = ("plan", "build", "review", "test", "docs", "duty")


def ref_of(rd) -> str:
    """An opaque card id: run directory names carry the kind and a timestamp."""
    return "w" + hashlib.sha256(rd.name.encode()).hexdigest()[:6]


def _started(run):
    return run["started_at"] or ""


def _executing(ctx):
    live = [r for r in ctx.runs if r["state"] == "executing"]
    return sorted(live, key=_started, reverse=True)


def _drawer_keys(ctx, keys):
    return list(keys)[:DRAWER_SLICES]


def read_states(ctx, cache, keys) -> dict:
    """One cache read per selected run, keyed by run ref. Evicts files nobody needs."""
    chosen = [(r, True) for r in _executing(ctx)[:MAX_LIVE]]
    seen = {r["ref"] for r, _ in chosen}
    finished = []
    for key in _drawer_keys(ctx, keys):
        for r in ctx.history.get(key, []):
            if r["ref"] not in seen:
                finished.append(r)
    finished.sort(key=_started, reverse=True)
    for r in finished[:MAX_DRAWER_FILES]:
        if r["ref"] not in seen:
            seen.add(r["ref"])
            chosen.append((r, r["state"] == "executing"))
    states, keep = {}, set()
    for run, executing in chosen:
        spec = runs.KINDS.get(run["req"].get("kind"))
        if spec is None:
            continue
        path = transcript.transcript_path(run["rd"], run["req"])
        if path is not None:
            keep.add(path)
        states[run["ref"]] = cache.read(
            path, spec.transcript, executing, (run["role"], run["key"] or "?")
        )
    cache.collect(keep)
    return states


def _model(run, state):
    argv = run["req"].get("argv")
    if isinstance(argv, list) and "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            return transcript.safe_model(argv[i + 1])
    return transcript.safe_model(state.model) if state else None


def live_cards(ctx, states, now_s) -> list:
    active = {s["key"]: s["phase"] for s in ctx.slices if s["phase"] in slices.ACTIVE}
    cards = []
    for run in _executing(ctx)[:MAX_LIVE]:
        st = states.get(run["ref"])
        kind = runs.KINDS[run["req"]["kind"]]
        doing = st.doing() if st else None
        cards.append(
            {
                "ref": run["ref"],
                "role": run["role"],
                "icon": ICONS[run["req"]["kind"]],
                "key": run["key"] or "?",
                "slice_active": run["key"] in active,
                "phase": active.get(run["key"]),
                "harness": kind.harness,
                "model": _model(run, st),
                "started_at": run["started_at"],
                "state": st.state if st else "none",
                "doing": None
                if doing is None
                else {
                    "tag": doing[0].tag,
                    "target": doing[0].target,
                    "at": doing[0].at,
                    "running": doing[1],
                },
                "tools": st.tools if st else 0,
                "partial": bool(st and st.partial),
                "errors": st.errors if st else 0,
                "tokens_out": (st.token_total or None) if st else None,
                "context": st.context if st else None,
                "spark": st.spark(now_s) if st else [0] * transcript.SPARK_MINUTES,
            }
        )
    return cards


def waiting_runs(ctx) -> list:
    return [
        {k: r[k] for k in ("role", "key", "state", "started_at")}
        for r in ctx.runs
        if r["state"] != "executing"
    ]


def feed(ctx, cache) -> list:
    """Tool calls and events merged, newest first. Both are already safe facts."""
    items = []
    for at, tag, target, role, key in cache.feed_log:
        items.append(
            {
                "at": at,
                "tag": tag,
                "target": target,
                "role": role,
                "key": key,
                "words": None,
            }
        )
    for ev in ctx.events:
        tag = status.event_tag(ev.get("kind"))
        words = status.event_words(ev, ctx.cfg)
        if tag and words and ctx.iso(ev.get("at")):
            key = ev.get("key")
            items.append(
                {
                    "at": ev["at"],
                    "tag": tag,
                    "target": None,
                    "role": None,
                    "key": key if isinstance(key, str) else None,
                    "words": words,
                }
            )
    items.sort(key=lambda i: ctx.when(i["at"]), reverse=True)
    return items[: transcript.FEED_LIMIT]


def kpi(ctx, view, states, now_s) -> dict:
    cards = [states[r["ref"]] for r in _executing(ctx)[:MAX_LIVE] if r["ref"] in states]
    mem = view["memory"]
    return {
        "running": view["counts"]["executing"],
        "slots": sum(p["cap"] for p in view["slots"]),
        "needs_you": view["counts"]["needs_you"],
        "merged_today": view["counts"]["merged_today"],
        "tools_per_min": sum(st.rate(now_s) for st in cards),
        "free_mb": mem["headroom_mb"] if mem else None,
        "memory_state": mem["state"] if mem else None,
        "last_pass_at": view["tick"]["last_pass_at"],
    }


def _result_header(rd) -> dict:
    """A run's result header, or {} when the file is missing or damaged."""
    try:
        return store.read_header(store.out_dir(rd) / "result.md", limit=HEADER_BYTES)
    except (OSError, ValueError):
        return {}


def _ended_at(rd):
    """When the worker stopped, from its exit file; None while it runs or if unreadable."""
    try:
        at = store.read_json(rd / "exit").get("ended_at")
    except (OSError, store.StoreError):
        return None
    try:
        datetime.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return at


def _summary(hdr, icon):
    """The result's own one-line summary; a test run falls back to its tests line."""
    text = hdr.get("summary") or (hdr.get("tests") if icon == "test" else None)
    return view_line(text, SUMMARY_LIMIT)


def view_line(text, limit):
    """Loop-written text cut to a line that scrubs to itself (the sealed `line` form)."""
    if not isinstance(text, str):
        return None
    out = scrub(text, limit)
    for _ in range(4):
        again = scrub(out, limit)
        if again == out:
            return out or None
        out = again
    return None


def drawers(ctx, states, keys) -> list:
    out = []
    for key in _drawer_keys(ctx, keys):
        history = ctx.history.get(key, [])
        run_rows, calls, files = [], [], {}
        for r in history:
            spec = runs.KINDS[r["req"]["kind"]]
            hdr = _result_header(r["rd"])
            icon = ICONS[r["req"]["kind"]]
            run_rows.append(
                {
                    "ref": r["ref"],
                    "role": r["role"],
                    "icon": icon,
                    "harness": spec.harness,
                    "started_at": r["started_at"],
                    "ended_at": _ended_at(r["rd"]),
                    "summary": _summary(hdr, icon),
                    "status": hdr.get("status")
                    if hdr.get("status") in runs.STATUSES
                    else None,
                    "verdict": hdr.get("verdict")
                    if hdr.get("verdict") in slices.VERDICTS
                    else None,
                }
            )
            st = states.get(r["ref"])
            for c in list(st.calls) if st else []:
                calls.append({"at": c.at, "tag": c.tag, "target": c.target})
                if (
                    spec.harness == "claude"
                    and c.tag in ("EDIT", "WRITE")
                    and c.target
                    and c.target != "?"
                ):
                    files[c.target] = files.get(c.target, 0) + 1
        calls.sort(key=lambda c: c["at"] or "", reverse=True)
        out.append(
            {
                "key": key,
                "runs": run_rows,
                "calls": calls[:DRAWER_CALLS],
                "files": [
                    {"name": n, "edits": c}
                    for n, c in sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))
                ][:DRAWER_FILES],
            }
        )
    return out
