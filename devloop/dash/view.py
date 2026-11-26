"""The dashboard's read model: one dict built from state files, sealed by a schema.

Guarantee: this module reads state and never writes it, runs no command and passes no
free text through. Every value in the finished view is checked by `_seal` against
`VIEW_SCHEMA`, so a new field that was not declared (or an ID that looks like a secret)
cannot reach a page by accident. A damaged file becomes an `unreadable` item; the
healthy cards still render.
"""

import datetime
import hashlib
import json
import re

from .. import evidence, notes, runs, slices, snapshot, status, store
from .. import project as projmod
from ..scrub import plain, scrub
from . import memory

SCHEMA = 1
SHIPPED_LIMIT = 20
ACTIVITY_LIMIT = 20
CHIPS = slices.PHASES[:10]
NEEDS = ("question", "hold")
PROBLEMS = ("stuck", "problem", "unreadable")
RUN_STATES = ("executing", "awaiting-apply", "awaiting-release")
SHIPPED_KINDS = ("merge", "row-close")
MEMORY_STATES = ("ok", "tight", "low")

# The fixed wording table: every `word` value in a view must be one of these.
WORDS = frozenset(
    (
        *slices.PHASES,
        *slices.RISKS,
        *NEEDS,
        *PROBLEMS,
        *RUN_STATES,
        *SHIPPED_KINDS,
        *MEMORY_STATES,
        *evidence.CODES,
        *evidence.REASON_WORDS,
        *status.EVENT_KINDS,
    )
)

_CARD = {
    "key": "id",
    "row": "id",
    "slice": "id",
    "phase": "word",
    "phase_index": "int",
    "held": "bool",
    "risk": "word?",
    "pr": "int?",
    "url": "url?",
    "round": "int",
    "since": "at?",
    "owner": "label",
    "stuck": "bool",
    "checklist": {
        "available": "bool",
        "met": "int",
        "total": "int",
        "items": [
            {
                "code": "word",
                "role": "label?",
                "ok": "bool",
                "reason": "word?",
            }
        ],
    },
}
_MEMORY = {
    "total_mb": "int",
    "available_mb": "int",
    "reserved_mb": "int",
    "floor_mb": "int",
    "headroom_mb": "int",
    "state": "word",
    "workers": [{"role": "label", "slice": "id", "rss_mb": "int"}],
    "top": [{"name": "id", "rss_mb": "int"}],
    "hosts": [
        {
            "name": "id",
            "total_mb": "int",
            "available_mb": "int",
            "state": "word",
        }
    ],
}
VIEW_SCHEMA = {
    "schema": "int",
    "project": "id",
    "version": "version",
    "tick": {
        "last_pass_at": "at?",
        "last_model_at": "at?",
        "paused": "bool",
        "capped": "bool",
    },
    "counts": {
        "needs_you": "int",
        "problems": "int",
        "executing": "int",
        "free": "int",
        "merged_today": "int?",
        "merged_week": "int?",
        "rows_closed": "int?",
    },
    "needs_you": [
        {
            "kind": "word",
            "key": "id?",
            "owner": "label",
            "count": "int?",
            "since": "at?",
            "question": "line?",
            "options": ["line"],
            "recommended": "line?",
            "answer_at": "line?",
        }
    ],
    "problems": [
        {
            "kind": "word",
            "key": "id?",
            "owner": "label",
            "count": "int?",
            "since": "at?",
        }
    ],
    "rows": [
        {"row": "id", "oldest_at": "at?", "stuck": "bool", "cards": [_CARD]},
    ],
    "workers": [
        {
            "role": "label",
            "key": "id",
            "node": "id",
            "state": "word",
            "started_at": "at?",
        }
    ],
    "nodes": [{"name": "id", "ok": "bool", "at": "at?"}],
    "slots": [{"pool": "id", "label": "label", "cap": "int", "used": "int"}],
    "memory": ("null", _MEMORY),
    "shipped": [
        {
            "kind": "word",
            "row": "id",
            "slice": "id?",
            "pr": "int?",
            "url": "url?",
            "at": "at",
        }
    ],
    "activity": [{"at": "at", "kind": "word", "words": "line"}],
    "parked": [_CARD],
}

_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")
_VERSION = re.compile(r"[0-9a-f]{16}")


class ViewError(Exception):
    """The view did not match its schema. The message names a key, never a value."""


def _iso_ok(value) -> bool:
    try:
        datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _scalar(kind, value, labels, where):
    if kind == "id":
        return plain(value)
    if kind == "word":
        if value not in WORDS:
            raise ViewError(f"{where}: word not in the fixed table")
        return value
    if kind == "label":
        if value not in labels:
            raise ViewError(f"{where}: unknown label")
        return value
    if kind == "int":
        if type(value) is not int:
            raise ViewError(f"{where}: not an int")
        return value
    if kind == "bool":
        if type(value) is not bool:
            raise ViewError(f"{where}: not a bool")
        return value
    if kind == "at":
        if not _iso_ok(value):
            raise ViewError(f"{where}: not a timestamp")
        return value
    if kind == "url":
        if not isinstance(value, str) or _URL.fullmatch(value) is None:
            raise ViewError(f"{where}: not a pull request URL")
        return value
    if kind == "version":
        if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
            raise ViewError(f"{where}: bad version")
        return value
    if kind == "line":
        if not isinstance(value, str) or scrub(value, len(value) + 1) != value:
            raise ViewError(f"{where}: line is not plain")
        return value
    raise ViewError(f"{where}: unknown kind")


def _seal(value, spec, labels, where="view"):
    """Walk `value` against `spec`; return a rebuilt tree. Undeclared keys raise."""
    if isinstance(spec, tuple):  # ("null", inner)
        return None if value is None else _seal(value, spec[1], labels, where)
    if isinstance(spec, dict):
        if not isinstance(value, dict) or set(value) != set(spec):
            raise ViewError(f"{where}: keys do not match the schema")
        return {k: _seal(value[k], s, labels, f"{where}.{k}") for k, s in spec.items()}
    if isinstance(spec, list):
        if not isinstance(value, list):
            raise ViewError(f"{where}: not a list")
        return [_seal(v, spec[0], labels, f"{where}[]") for v in value]
    nullable = spec.endswith("?")
    if value is None:
        if nullable:
            return None
        raise ViewError(f"{where}: null not allowed")
    return _scalar(spec.rstrip("?"), value, labels, where)


def version_of(view) -> str:
    """16 hex of sha256 over the canonical JSON without the version key."""
    body = {k: v for k, v in view.items() if k != "version"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- helpers --------------------------------------------------------------------


def _labels(cfg) -> set:
    kinds = list(projmod.ROLE_DEFAULTS) + list(runs.KINDS)
    return (
        {projmod.role_label(cfg, k) for k in kinds}
        | {projmod.pool_label(cfg, p) for p in projmod.SLOTS}
        | {projmod.role_label(cfg, None)}
    )


def _iso(value):
    return value if isinstance(value, str) and _iso_ok(value) else None


def _when(value):
    try:
        t = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def _age(value, now) -> float:
    t = _when(value)
    return (now - t).total_seconds() if t else 0.0


def _int_or_none(value):
    return value if type(value) is int else None


def _pr_url(cfg, slug, pr):
    """A link only for a configured repo slug and an integer PR number."""
    known = {r["slug"] for r in cfg["repos"]}
    if slug in known and type(pr) is int and pr >= 0:
        return f"https://github.com/{slug}/pull/{pr}"
    return None


def _try(fn, bad, name, default):
    """Run one reader; a damaged file adds `name` to `bad` and returns `default`."""
    try:
        return fn()
    except (store.StoreError, notes.NotesError, OSError, ValueError, TypeError):
        bad.append(name)
        return default


# --- sections -------------------------------------------------------------------


class _Ctx:
    """What every section needs, built once per view."""

    def __init__(self, project, cfg, now, proc):
        self.project, self.cfg, self.now, self.proc = project, cfg, now, proc
        self.bad = []
        self.owner = projmod.role_label(cfg, "owner")
        self.tick = projmod.role_label(cfg, "tick")
        self.slices, bad_slices = slices.load_all(project)
        self.bad += bad_slices
        self.runs = self._runs()
        self.executing_by_key = {
            r["key"]: r["role"] for r in self.runs if r["state"] == "executing"
        }

    def _runs(self):
        out = []
        for rd, req in runs.iter_runs(self.project):
            try:
                state = runs.run_state(rd, req)
                if state is None:
                    continue
                out.append(
                    {
                        "role": projmod.role_label(self.cfg, req.get("kind")),
                        "key": req.get("key"),
                        "node": req.get("host") or "local",
                        "state": state,
                        "started_at": _iso(req.get("started_at")),
                        "pgid": req.get("payload_pgid"),
                    }
                )
            except (store.StoreError, KeyError, TypeError, ValueError, OSError):
                continue
        return out


def _owner_of(ctx, s, held):
    role = ctx.executing_by_key.get(s["key"])
    if role:
        return role
    if held or s["phase"] == "HELD":
        return ctx.owner
    return ctx.tick


CHECKLIST_ERRORS = (
    slices.SliceError,
    store.StoreError,
    projmod.ConfigError,
    KeyError,
    TypeError,
    ValueError,
    AttributeError,
    OSError,
)


def _checklist(ctx, s):
    empty = {"available": False, "met": 0, "total": 0, "items": []}
    if s["phase"] not in slices.ACTIVE:
        return empty
    try:
        items = slices.exit_items(ctx.project, s["key"])
    except CHECKLIST_ERRORS:
        return empty  # one slice's checklist must not blank the page
    roles = {}
    for name, spec in ctx.cfg["reviewers"].items():
        roles[name] = projmod.role_label(ctx.cfg, spec["kind"])
    items = sorted(items, key=lambda i: i.ok)  # missing first, stable otherwise
    return {
        "available": True,
        "met": sum(1 for i in items if i.ok),
        "total": len(items),
        "items": [
            {
                "code": i.code,
                "role": roles.get(i.reviewer),
                "ok": i.ok,
                "reason": i.reason,
            }
            for i in items
        ],
    }


def _card(ctx, s):
    held = s["phase"] in ("HELD", "PARKED")
    phase = (s["parked_from"] or "FRAME") if held else s["phase"]
    executing = s["key"] in ctx.executing_by_key
    stuck = (
        not held
        and not executing
        and s["phase"] in slices.ACTIVE
        and _age(s["last_moved_at"], ctx.now) > snapshot.STALE_SECS
    )
    return {
        "key": s["key"],
        "row": s["row"],
        "slice": s["slice"],
        "phase": phase,
        "phase_index": CHIPS.index(phase),
        "held": held,
        "risk": s["risk"],
        "pr": s["pr"],
        "url": None
        if s["pr"] is None
        else _pr_url(
            ctx.cfg,
            next((r["slug"] for r in ctx.cfg["repos"] if r["name"] == s["repo"]), None),
            s["pr"],
        ),
        "round": s["round"],
        "since": _iso(s["last_moved_at"]),
        "owner": _owner_of(ctx, s, held),
        "stuck": stuck,
        "checklist": _checklist(ctx, s),
    }


def _by_age(card):
    return (not card["stuck"], card["since"] or "")


def _rows(ctx):
    live = [s for s in ctx.slices if s["phase"] in slices.ACTIVE]
    by_row = {}
    for s in live:
        by_row.setdefault(s["row"], []).append(_card(ctx, s))
    rows = []
    for row, cards in by_row.items():
        cards.sort(key=_by_age)
        rows.append(
            {
                "row": row,
                "oldest_at": min(
                    (c["since"] for c in cards if c["since"]), default=None
                ),
                "stuck": any(c["stuck"] for c in cards),
                "cards": cards,
            }
        )
    rows.sort(key=lambda r: (not r["stuck"], r["oldest_at"] or ""))
    return rows


def _parked(ctx):
    cards = [_card(ctx, s) for s in ctx.slices if s["phase"] in ("HELD", "PARKED")]
    return sorted(cards, key=_by_age)


def _need(kind, owner, key=None, count=None, since=None, **extra):
    return {
        "kind": kind,
        "key": key,
        "owner": owner,
        "count": count,
        "since": since,
        **extra,
    }


def _plain_line(text, limit=300):
    """Loop-written text, scrubbed like every other public line; None when empty."""
    if not isinstance(text, str):
        return None
    out = scrub(text, limit)
    # The cut can end on a space or inside a pattern; settle on text that scrubs to itself,
    # the form the sealed schema accepts.
    for _ in range(4):
        again = scrub(out, limit)
        if again == out:
            return out or None
        out = again
    return None


def _owner_item(kind, owner, key=None, since=None, **extra):
    """An item for the owner: the question fields are always present, empty for a hold."""
    fields = {"question": None, "options": [], "recommended": None, "answer_at": None}
    fields.update(extra)
    return _need(kind, owner, key, since=since, **fields)


def _problem_item(kind, owner, key=None, count=None, since=None):
    """No loop text here: the page words each kind itself."""
    return {"kind": kind, "key": key, "owner": owner, "count": count, "since": since}


def _needs_you(ctx):
    """Items that only the Product owner can answer: open questions and holds."""
    out = []
    held_keys = {s["key"] for s in ctx.slices if s["phase"] == "HELD"}

    def questions():
        path = notes.questions_path(ctx.project)
        if not path.is_file():
            return {}
        return notes.load_questions(path)[1]

    for q in _try(questions, ctx.bad, "questions", {}).values():
        hold = q["key"].startswith(notes.HOLD_PREFIX)
        if hold and q["key"][len(notes.HOLD_PREFIX) :] in held_keys:
            continue  # shown once, on the hold item
        if q["wait"] and not q["answer"]:
            out.append(
                _owner_item(
                    "question",
                    ctx.owner,
                    q["key"],
                    since=_iso(q["asked_at"]),
                    question=_plain_line(q["question"]),
                    options=[
                        o for o in (_plain_line(o, 120) for o in q["options"]) if o
                    ],
                    recommended=_plain_line(q["recommended"], 120),
                    answer_at=_plain_line(answer_file(ctx), 80),
                )
            )
    for s in ctx.slices:
        owner_park = s["phase"] == "PARKED" and s["park_reason"] in slices.OWNER_PARKS
        if s["phase"] == "HELD" or owner_park or s["merge_unknown"]:
            out.append(
                _owner_item("hold", ctx.owner, s["key"], since=_iso(s["last_moved_at"]))
            )
    return out


def answer_file(ctx):
    """Where the owner answers: the questions file's name, never its folder."""
    try:
        return notes.questions_path(ctx.project).name
    except (OSError, ValueError, TypeError, store.StoreError, notes.NotesError):
        return None


def _problems(ctx):
    """Known problems and stuck work: the Tech lead's, not the owner's."""
    out = []
    for row in _rows_cache(ctx):
        for c in row["cards"]:
            if c["stuck"]:
                out.append(_problem_item("stuck", ctx.tick, c["key"], since=c["since"]))

    def problems():
        data = store.read_json(store.state_path(ctx.project) / "problems.json")
        return data.get("problems", {}).values()

    for e in _try(problems, ctx.bad, "problems", []):
        if isinstance(e, dict) and e.get("open"):
            out.append(
                _problem_item(
                    "problem",
                    ctx.tick,
                    e.get("key"),
                    count=_int_or_none(e.get("count")),
                    since=_iso(e.get("last_seen")),
                )
            )
    return out


def _rows_cache(ctx):
    if not hasattr(ctx, "_rows_value"):
        ctx._rows_value = _rows(ctx)
    return ctx._rows_value


def _unreadable(ctx):
    return [
        _problem_item("unreadable", ctx.tick, name) for name in dict.fromkeys(ctx.bad)
    ]


def _nodes(ctx):
    data = _try(
        lambda: store.read_json(store.state_path(ctx.project) / "node-status.json"),
        ctx.bad,
        "nodes",
        {},
    )
    return [
        {"name": name, "ok": v.get("ok") is True, "at": _iso(v.get("at"))}
        for name, v in sorted(data.items())
        if isinstance(v, dict)
    ]


def _slots(ctx):
    used = runs.slot_usage(ctx.project)
    return [
        {
            "pool": pool,
            "label": projmod.pool_label(ctx.cfg, pool),
            "cap": ctx.cfg["capacity"][pool],
            "used": used.get(pool, 0),
        }
        for pool in projmod.SLOTS
    ]


def _shipped(ctx, merges, closures):
    out = []
    for mid, rec in (merges or {}).items():
        if not isinstance(rec, dict) or not _iso(rec.get("at")):
            continue
        slug = mid.rsplit("#", 1)[0] if "#" in mid else None
        if type(rec.get("pr")) is not int:
            continue
        out.append(
            {
                "kind": "merge",
                "row": rec.get("row"),
                "slice": rec.get("slice"),
                "pr": rec["pr"],
                "url": _pr_url(ctx.cfg, slug, rec["pr"]),
                "at": rec["at"],
            }
        )
    for row, rec in (closures or {}).items():
        if isinstance(rec, dict) and rec.get("closed") and _iso(rec.get("at")):
            out.append(
                {
                    "kind": "row-close",
                    "row": row,
                    "slice": None,
                    "pr": None,
                    "url": None,
                    "at": rec["at"],
                }
            )
    out.sort(key=lambda e: _when(e["at"]), reverse=True)
    return out[:SHIPPED_LIMIT]


def _activity(ctx):
    out = []
    for ev in status.events(ctx.project)[-200:]:
        words = status.event_words(ev, ctx.cfg)
        if words and _iso(ev["at"]):
            out.append({"at": ev["at"], "kind": ev["kind"], "words": words})
    return out[-ACTIVITY_LIMIT:][::-1]


def _tick_facts(ctx):
    d = store.state_path(ctx.project)
    try:
        mtime = (d / "STATUS.md").stat().st_mtime
        last_pass = datetime.datetime.fromtimestamp(
            mtime, datetime.timezone.utc
        ).isoformat()
    except OSError:
        last_pass = None
    state = _try(
        lambda: store.read_json(d / "tick-state.json"), ctx.bad, "tick-state", {}
    )
    return {
        "last_pass_at": last_pass,
        "last_model_at": _iso(state.get("last_model_at")),
        "paused": (d / "PAUSE").exists(),
        "capped": store.cap_active(ctx.project, ctx.now),
    }


def _memory(ctx):
    workers = []
    for r in ctx.runs:
        if r["state"] != "executing":
            continue
        pgid = r["pgid"] if type(r["pgid"]) is int else None
        workers.append((r["role"], r["key"], pgid))
    floor_mb = int(ctx.cfg.get("min_free_gb", 2.5) * 1024)
    reserved = runs.reserved_mb(ctx.now)
    return memory.read_memory(ctx.proc, workers, reserved, floor_mb)


# --- the view ---------------------------------------------------------------------


def build_view(project, proc="/proc", now=None) -> dict:
    """The sealed view for one project. Raises ViewError when it breaks its schema."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cfg = projmod.load_project(project)
    ctx = _Ctx(project, cfg, now, proc)
    unread = object()  # a failed read, as opposed to a file holding JSON null
    raw_merges = _try(lambda: slices.merge_records(project), ctx.bad, "merges", unread)
    raw_closures = _try(
        lambda: slices.closure_records(project), ctx.bad, "closures", unread
    )
    merges, closures = status.checked_ledgers(
        None if raw_merges is unread else raw_merges,
        None if raw_closures is unread else raw_closures,
    )
    for name, raw, ok in (
        ("merges", raw_merges, merges),
        ("closures", raw_closures, closures),
    ):
        if raw is not unread and ok is None:  # read fine, but its content is damaged
            ctx.bad.append(name)
    needs = _needs_you(ctx)
    problems = _problems(ctx)
    counts = status.counts_from(merges, closures, now)
    slots = _slots(ctx)
    view = {
        "schema": SCHEMA,
        "project": project,
        "version": "0" * 16,
        "tick": _tick_facts(ctx),
        "counts": {
            "needs_you": 0,
            "problems": 0,
            "executing": sum(1 for r in ctx.runs if r["state"] == "executing"),
            "free": sum(max(0, p["cap"] - p["used"]) for p in slots),
            "merged_today": counts["merged_today"],
            "merged_week": counts["merged_week"],
            "rows_closed": counts["rows_closed"],
        },
        "needs_you": needs,
        "problems": problems,
        "rows": _rows_cache(ctx),
        "workers": [
            {k: r[k] for k in ("role", "key", "node", "state", "started_at")}
            for r in ctx.runs
        ],
        "nodes": _nodes(ctx),
        "slots": slots,
        "memory": _memory(ctx),
        "shipped": _shipped(ctx, merges, closures),
        "activity": _activity(ctx),
        "parked": _parked(ctx),
    }
    view["problems"] = problems + _unreadable(ctx)
    view["counts"]["needs_you"] = len(view["needs_you"])
    view["counts"]["problems"] = len(view["problems"])
    for w in view["workers"]:
        w["key"] = w["key"] if w["key"] else "?"
    sealed = _seal(view, VIEW_SCHEMA, _labels(cfg))
    sealed["version"] = version_of(sealed)
    return sealed
