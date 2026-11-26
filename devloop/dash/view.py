"""The dashboard's read model: one dict built from state files, sealed by a schema.

Guarantee: this module reads state and never writes it, runs no command and passes no
free text through. Every value in the finished view is checked by `_seal` against
`VIEW_SCHEMA`, so a new field that was not declared (or an ID that looks like a secret)
cannot reach a page by accident. A damaged file becomes an `unreadable` item; the
healthy cards still render.
"""

import datetime
import re

from .. import evidence, notes, runs, slices, snapshot, status, store, tick
from .. import project as projmod
from ..scrub import plain, redact_secrets, scrub
from . import live, memory, roadmap, transcript, words

SCHEMA = 2
OWNER_TEXT_LIMIT = 1000
SHIPPED_LIMIT = 20
DECIDED_LIMIT = 20
ACTIVITY_LIMIT = 20
CHIPS = slices.PHASES[:10]
NEEDS = ("question", "hold", "stuck")
PROBLEMS = ("stuck", "problem", "unreadable")
RUN_STATES = ("executing", "awaiting-apply", "awaiting-release")
SHIPPED_KINDS = ("merge", "row-close")
MEMORY_STATES = ("ok", "tight", "low")
CLASSES = ("tech", "product")
TRANSCRIPT_STATES = ("live", "starting", "outside", "none", "unreadable")

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
        *CLASSES,
        *TRANSCRIPT_STATES,
        *runs.STATUSES,
        *slices.VERDICTS,
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
    "hold": "bool",
    "row_title": "owner_text?",
    "released": "bool",
    "waiting_text": "line?",
    "waiting_key": "id?",
    "risk": "word?",
    "pr": "int?",
    "url": "url?",
    "round": "int",
    "since": "at?",
    "owner": "label",
    "stuck": "bool",
    "purpose": "line?",
    "slice_desc": "line?",
    "next_action": "line?",
    "wait": "wait?",
    "wait_for": "label?",
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
            "row": "id?",
            "slice": "id?",
            "title": "owner_text?",
            "owner": "label",
            "count": "int?",
            "since": "at?",
            "answer_key": "qkey?",
            "question": "owner_text?",
            "detail": "owner_text?",
            "options": ["owner_text"],
            "option_ok": ["bool"],
            "recommended": "owner_text?",
            "klass": "word",
            "version": "version?",
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
    "decided": [
        {
            "key": "qkey",
            "question": "owner_text?",
            "detail": "owner_text?",
            "options": ["owner_text"],
            "option_ok": ["bool"],
            "recommended": "owner_text?",
            "applied": "owner_text?",
            "answer": "owner_text?",
            "at": "at?",
            "version": "version",
        }
    ],
    "controls": {"paused": "bool", "tick_running": "bool"},
    "known_problems": [
        {
            "key": "id",
            "impact": "line?",
            "detail": "line?",
            "fix": "line?",
            "open": "bool",
            "closed_at": "at?",
            "count": "int?",
            "last_seen": "at?",
        }
    ],
    "rows": [
        {
            "row": "id",
            "title": "owner_text?",
            "desc": "line?",
            "done": "int",
            "total": "int",
            "oldest_at": "at?",
            "stuck": "bool",
            "cards": [_CARD],
        },
    ],
    "kpi": {
        "running": "int",
        "slots": "int",
        "needs_you": "int",
        "merged_today": "int?",
        "tools_per_min": "int",
        "free_mb": "int?",
        "memory_state": "word?",
        "last_pass_at": "at?",
    },
    "live": [
        {
            "ref": "id",
            "role": "label",
            "icon": "icon",
            "key": "id",
            "slice_active": "bool",
            "phase": "word?",
            "harness": "harness",
            "model": "model?",
            "started_at": "at?",
            "state": "word",
            "doing": (
                "null",
                {"tag": "tag", "target": "target?", "at": "at?", "running": "bool"},
            ),
            "tools": "int",
            "partial": "bool",
            "errors": "int",
            "tokens_out": "int?",
            "context": "int?",
            "spark": ["int"],
        }
    ],
    "waiting_runs": [
        {"role": "label", "key": "id", "state": "word", "started_at": "at?"}
    ],
    "feed": [
        {
            "at": "at",
            "tag": "tag",
            "target": "target?",
            "role": "label?",
            "key": "id?",
            "words": "line?",
        }
    ],
    "drawers": [
        {
            "key": "id",
            "runs": [
                {
                    "ref": "id",
                    "role": "label",
                    "icon": "icon",
                    "harness": "harness",
                    "started_at": "at?",
                    "ended_at": "at?",
                    "status": "word?",
                    "verdict": "word?",
                    "summary": "line?",
                }
            ],
            "calls": [{"at": "at?", "tag": "tag", "target": "target?"}],
            "files": [{"name": "target", "edits": "int"}],
        }
    ],
    "sources": [{"label": "line", "ok": "bool", "why": "line?", "at": "at?"}],
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
    "recent": [{"date": "line", "title": "owner_text", "bullets": ["owner_text"]}],
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
    if kind == "tag":
        if value not in transcript.TOOL_TAGS and value not in status.EVENT_TAGS:
            raise ViewError(f"{where}: tag not in the fixed table")
        return value
    if kind == "target":
        if transcript.safe_target(value) != value:
            raise ViewError(f"{where}: target is not a plain name")
        return value
    if kind == "model":
        if transcript.safe_model(value) != value:
            raise ViewError(f"{where}: model is not a plain name")
        return value
    if kind == "wait":
        if value not in words.WAIT:
            raise ViewError(f"{where}: wait not in the fixed table")
        return value
    if kind == "icon":
        if value not in live.ICON_IDS:
            raise ViewError(f"{where}: unknown icon")
        return value
    if kind == "harness":
        if value not in runs.HARNESSES:
            raise ViewError(f"{where}: unknown harness")
        return value
    if kind == "owner_text":
        # The owner reads their own questions as written; only the page markers and
        # line breaks are refused, because they would break the questions file.
        if (
            not isinstance(value, str)
            or "\n" in value
            or len(value) > OWNER_TEXT_LIMIT
            or notes.BLOCK_START in value
            or notes.BLOCK_END in value
        ):
            raise ViewError(f"{where}: owner text is not one plain line")
        return value
    if kind == "qkey":
        try:
            return notes._key(value)
        except notes.NotesError:
            raise ViewError(f"{where}: bad question key") from None
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
    return store.canonical_digest({k: v for k, v in view.items() if k != "version"})


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
        self.events = status.events(project)[-200:]
        self.slices, bad_slices = slices.load_all(project)
        self.bad += bad_slices
        self.history: dict = {}
        self.runs = self._runs()
        self.executing_by_key = {
            r["key"]: r["role"] for r in self.runs if r["state"] == "executing"
        }
        self.apply_keys = {
            r["key"] for r in self.runs if r["state"] == "awaiting-apply"
        }
        self.slot_used = _try(
            lambda: runs.slot_usage(project), self.bad, "run-records", {}
        )
        self.titles = _try(lambda: roadmap.titles(cfg), self.bad, "roadmap-titles", {})
        self.descs = _try(
            lambda: roadmap.descriptions(cfg), self.bad, "roadmap-titles", {}
        )

    def title_of(self, row):
        """The plain name of a roadmap row, or None; one shared owner-text rule."""
        return _owner_text(
            self.titles.get(row) or words.DUTY_ROWS.get(row), roadmap.TITLE_LIMIT + 4
        )

    def desc_of(self, row):
        """What a roadmap row is and why, scrubbed like every loop-written line."""
        text = self.descs.get(row) or words.DUTY_ROWS.get(row)
        return live.view_line(text, roadmap.DESC_LIMIT + 3)

    def slice_desc(self, s):
        """The slice plan's goal and why; else the slice's own purpose line."""
        try:
            text = roadmap.plan_summary(
                slices._plan_file(s),
                roadmap.SLICE_DESC_LIMIT,
                (s["row"], s["slice"]),
            )
        except OSError:  # a plan that cannot be read just falls back to the purpose
            text = None
        found = live.view_line(text, roadmap.SLICE_DESC_LIMIT + 3)
        if found:
            return found
        purpose = _plain_line(s["purpose"], roadmap.SLICE_DESC_LIMIT)
        # A purpose that only repeats the row and slice names says nothing more.
        same = (
            purpose
            and purpose.replace(" ", "").lower() == (s["row"] + s["slice"]).lower()
        )
        return None if same else purpose

    iso = staticmethod(_iso)
    when = staticmethod(_when)

    def _runs(self):
        """One walk of `runs/`: slot holders, plus each active slice's recent history.

        The history is chosen before the slot filter, so a finished run stays in its
        slice's drawer after it is applied and holds no slot.
        """
        out = []
        active = {s["key"] for s in self.slices if s["phase"] in slices.ACTIVE}
        for rd, req in runs.iter_runs(self.project):
            try:
                if req.get("kind") not in runs.KINDS:
                    continue
                state = runs.run_state(rd, req)
                rec = {
                    "role": projmod.role_label(self.cfg, req.get("kind")),
                    "key": req.get("key"),
                    "node": req.get("host") or "local",
                    "state": state,
                    "started_at": _iso(req.get("started_at")),
                    "pgid": req.get("payload_pgid"),
                    "rd": rd,
                    "req": req,
                    "ref": live.ref_of(rd),
                }
                if rec["key"] in active:
                    self.history.setdefault(rec["key"], []).append(rec)
                if state is not None:
                    out.append(rec)
            except (store.StoreError, KeyError, TypeError, ValueError, OSError):
                continue
        for key, recs in self.history.items():
            recs.sort(key=lambda r: r["started_at"] or "", reverse=True)
            self.history[key] = recs[: live.DRAWER_RUNS]
        return out


def _last_activity(ctx, s):
    """The later of the slice's last move and its newest run's start: a slice whose
    test ran for an hour and just finished has not been idle for that hour."""
    times = [s["last_moved_at"]] + [
        r["started_at"] for r in ctx.history.get(s["key"], [])
    ]
    parsed = [(_when(t), t) for t in times if t and _when(t)]
    return max(parsed)[1] if parsed else s["last_moved_at"]


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


def _wait(ctx, s, stuck):
    """Who a slice with no running worker waits on: a fixed word and, for a slot, its pool."""
    on = s["waiting_on"] or ""
    pool = on[: -len(" slot")] if on.endswith(" slot") else None
    if s["key"] in ctx.executing_by_key:
        return None, None
    if is_qa_wait(s):
        return "slot", projmod.pool_label(ctx.cfg, "tester")
    if _is_hold(s) or on.startswith("question "):
        return "owner", None
    if pool in projmod.SLOTS:
        return "slot", projmod.pool_label(ctx.cfg, pool)
    if s["key"] in ctx.apply_keys:
        return "apply", None
    full = _full_pool_for(ctx, s)
    if full:
        return "slot", projmod.pool_label(ctx.cfg, full)
    return ("stuck" if stuck else "loop"), None


# The pool a slice needs next in each phase. A slice whose pool is full is queued for
# that slot, not stuck, even when the tick did not write `waiting_on`.
PHASE_POOL = {
    "FRAME": "planner",
    "PLAN": "planner",
    "DEVELOP": "builder",
    "REMEDIATE": "builder",
    "SELF_CHECK": "reviewer",
    "VERIFY": "tester",
    "REVIEW": "reviewer",
    "DOCUMENT": "builder",
}


def _full_pool_for(ctx, s):
    phase = (s["parked_from"] or "FRAME") if s["phase"] == "PARKED" else s["phase"]
    pool = PHASE_POOL.get(phase)
    if pool is None:
        return None
    cap = (ctx.cfg.get("capacity") or {}).get(pool)
    if cap is None or ctx.slot_used.get(pool, 0) < cap:
        return None
    return pool


def is_qa_wait(s) -> bool:
    """A parked slice that only waits for a tester (QA) slot: still work in progress."""
    return s["phase"] == "PARKED" and "tester slot" in (s["waiting_on"] or "")


def _waiting_key(ctx, s, text):
    """The open slice a waiting-on text names, as `ROW--SLICE` or `ROW SLICE`."""
    best = None
    for o in ctx.slices:
        if o["key"] == s["key"] or not (o["phase"] in slices.ACTIVE or is_qa_wait(o)):
            continue
        spaced = o["key"].replace("--", " ")
        if (o["key"] in text or spaced in text) and (
            best is None or len(o["key"]) > len(best)
        ):
            best = o["key"]
    return best


def _card(ctx, s):
    held = s["phase"] in ("HELD", "PARKED") and not is_qa_wait(s)
    parked = s["phase"] in ("HELD", "PARKED")
    phase = (s["parked_from"] or "FRAME") if parked else s["phase"]
    waiting = (s["waiting_on"] or "") if parked else ""
    executing = s["key"] in ctx.executing_by_key
    stuck = (
        not held
        and not executing
        and s["phase"] in slices.ACTIVE
        and _age(_last_activity(ctx, s), ctx.now) > snapshot.STALE_SECS
    )
    wait, wait_for = _wait(ctx, s, stuck)
    if wait == "slot":
        stuck = False  # queued for a full slot: waiting its turn, not stuck
    return {
        "key": s["key"],
        "row": s["row"],
        "slice": s["slice"],
        "phase": phase,
        "phase_index": CHIPS.index(phase),
        "held": held,
        "hold": _is_hold(s),
        "row_title": ctx.title_of(s["row"]),
        "released": s["phase"] == "PARKED"
        and s["park_reason"] in slices.OWNER_PARKS
        and bool(s.get("owner_answered")),
        "waiting_text": _plain_line(waiting or s["park_reason"], 160)
        if parked
        else None,
        "waiting_key": _waiting_key(ctx, s, waiting) if waiting else None,
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
        "purpose": _plain_line(s["purpose"], 160),
        "slice_desc": ctx.slice_desc(s),
        "next_action": _plain_line(s["next_action"], 220),
        "wait": wait,
        "wait_for": wait_for,
        "checklist": _checklist(ctx, s),
    }


def _by_age(card):
    return (not card["stuck"], card["since"] or "")


def _rows(ctx):
    live = [s for s in ctx.slices if s["phase"] in slices.ACTIVE or is_qa_wait(s)]
    total, done = {}, {}
    for s in ctx.slices:
        total[s["row"]] = total.get(s["row"], 0) + 1
        done[s["row"]] = done.get(s["row"], 0) + (s["phase"] == "DONE")
    by_row = {}
    for s in live:
        by_row.setdefault(s["row"], []).append(_card(ctx, s))
    rows = []
    for row, cards in by_row.items():
        cards.sort(key=_by_age)
        rows.append(
            {
                "row": row,
                "title": ctx.title_of(row),
                "desc": ctx.desc_of(row),
                "done": done[row],
                "total": total[row],
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
    cards = [
        _card(ctx, s)
        for s in ctx.slices
        if s["phase"] in ("HELD", "PARKED") and not is_qa_wait(s)
    ]
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
    return live.view_line(text, limit)


def _owner_item(kind, owner, key=None, since=None, **extra):
    """An item for the owner: the question fields are always present, empty for a hold."""
    fields = {
        "row": None,
        "slice": None,
        "title": None,
        "answer_key": None,
        "question": None,
        "detail": None,
        "options": [],
        "option_ok": [],
        "recommended": None,
        "klass": "product",
        "version": None,
        "answer_at": None,
    }
    fields.update(extra)
    return _need(kind, owner, key, since=since, **fields)


def _problem_item(kind, owner, key=None, count=None, since=None):
    """No loop text here: the page words each kind itself."""
    return {"kind": kind, "key": key, "owner": owner, "count": count, "since": since}


def _owner_text(text, limit=OWNER_TEXT_LIMIT):
    """A question field as the owner wrote it: one line, cut at the limit, or None."""
    if not isinstance(text, str):
        return None
    out = " ".join(text.split())[:limit].strip()
    if notes.BLOCK_START in out or notes.BLOCK_END in out:
        return None
    # Full text, except spans shaped like a secret, so a pasted key never reaches a page.
    return redact_secrets(out) or None


def _options(raw):
    """Shown options, each with whether the shown text is the real value to submit."""
    shown, ok = [], []
    for o in raw:
        text = _owner_text(o)
        if text:
            shown.append(text)
            ok.append(text == " ".join(o.split())[:OWNER_TEXT_LIMIT].strip())
    return shown, ok


def _question_fields(ctx, q):
    options, option_ok = _options(q["options"])
    return {
        "answer_key": q["key"],
        "question": _owner_text(q["question"]),
        "detail": _owner_text(q["detail"]),
        "klass": q["klass"],
        "version": notes.question_version(q),
        "options": options,
        "option_ok": option_ok,
        "recommended": _owner_text(q["recommended"]),
        "answer_at": _plain_line(answer_file(ctx), 80),
    }


def _is_hold(s):
    """A slice waiting on the owner's release: held, or parked for an owner reason.

    An owner-parked slice the owner already released only waits for the tick to
    resume it, so it no longer needs the owner.
    """
    return s["phase"] == "HELD" or (
        s["phase"] == "PARKED"
        and s["park_reason"] in slices.OWNER_PARKS
        and not s.get("owner_answered")
    )


def _plain_key(key) -> bool:
    try:
        notes._key(key)
    except notes.NotesError:
        return False
    return True


def _question_records(ctx):
    """The questions page, read once per view."""
    if not hasattr(ctx, "_questions_value"):

        def questions():
            path = notes.questions_path(ctx.project)
            found = notes.load_questions(path)[1] if path.is_file() else {}
            # A hand-edited heading that is not a plain key could not be sealed or answered.
            return {k: q for k, q in found.items() if _plain_key(k)}

        ctx._questions_value = _try(questions, ctx.bad, "questions", {})
    return ctx._questions_value


def _decided(ctx):
    """Tech questions the Tech lead decided, newest first. The owner can override each."""
    out = []
    for q in _question_records(ctx).values():
        if q["klass"] != "tech" or not q["applied"]:
            continue
        options, option_ok = _options(q["options"])
        out.append(
            {
                "key": q["key"],
                "question": _owner_text(q["question"]),
                "detail": _owner_text(q["detail"]),
                "options": options,
                "option_ok": option_ok,
                "recommended": _owner_text(q["recommended"]),
                "applied": _owner_text(q["applied"]),
                "answer": _owner_text(q["answer"]),
                "at": _iso(q["answered_at"] or q["asked_at"]),
                "version": notes.question_version(q),
            }
        )
    out.sort(key=lambda d: d["at"] or "", reverse=True)
    return out[:DECIDED_LIMIT]


def _work(ctx, s) -> dict:
    """The slice an owner item is about: row, slice name and the row's plain name."""
    return {"row": s["row"], "slice": s["slice"], "title": ctx.title_of(s["row"])}


def _needs_you(ctx):
    """Items that only the Product owner can answer: open questions, holds and stuck work.

    A held slice and its `hold:KEY` question are one item: the hold carries the
    question's key, text and options. Stuck work counts only when the slice waits on a
    question; work waiting on the loop belongs to the work table, not to the owner.
    """
    out = []
    held_keys = {s["key"] for s in ctx.slices if _is_hold(s)}
    by_key = {s["key"]: s for s in ctx.slices}

    records = _question_records(ctx)
    for q in records.values():
        hold = q["key"].startswith(notes.HOLD_PREFIX)
        if hold and q["key"][len(notes.HOLD_PREFIX) :] in held_keys:
            continue  # shown once, on the hold item
        if q["wait"] and not q["answer"]:
            about = by_key.get(q["key"][len(notes.HOLD_PREFIX) :]) if hold else None
            out.append(
                _owner_item(
                    "question",
                    ctx.owner,
                    q["key"],
                    since=_iso(q["asked_at"]),
                    **(_work(ctx, about) if about else {}),
                    **_question_fields(ctx, q),
                )
            )
    for s in ctx.slices:
        if _is_hold(s) or s["merge_unknown"]:
            # A hold's question record carries the real answer key and options; without
            # one (a merge with an unknown result) there is nothing to answer by key.
            rec = records.get(notes.HOLD_PREFIX + s["key"])
            extra = _question_fields(ctx, rec) if rec else {}
            out.append(
                _owner_item(
                    "hold",
                    ctx.owner,
                    s["key"],
                    since=_iso(s["last_moved_at"]),
                    **_work(ctx, s),
                    **extra,
                )
            )
    asked = {i["key"] for i in out}
    for row in _rows_cache(ctx):
        for c in row["cards"]:
            s = by_key[c["key"]]
            on = s["waiting_on"] or ""
            if c["stuck"] and on.startswith("question ") and on[9:] not in asked:
                out.append(
                    _owner_item(
                        "stuck",
                        ctx.owner,
                        c["key"],
                        since=c["since"],
                        **_work(ctx, s),
                    )
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


FIXED_SHOWN_DAYS = 7
KNOWN_PROBLEMS_LIMIT = 40


def _known_problems(ctx):
    """The problem log as the owner reads it: what goes wrong, the fix, and its status.

    Open problems first (newest sighting first), then fixed ones from the last seven
    days. Older fixed problems are left out.
    """

    def entries():
        data = store.read_json(store.state_path(ctx.project) / "problems.json")
        return list(data.get("problems", {}).values())

    cutoff = ctx.now - datetime.timedelta(days=FIXED_SHOWN_DAYS)
    out = []
    for e in _try(entries, ctx.bad, "problems", []):
        if not isinstance(e, dict) or not isinstance(e.get("key"), str):
            continue
        if not _plain_key(e["key"]):
            continue
        closed = _iso(e.get("closed_at"))
        if not e.get("open") and not (closed and _when(closed) >= cutoff):
            continue
        out.append(
            {
                "key": e["key"],
                "impact": _plain_line(e.get("impact"), 200),
                "detail": _plain_line(e.get("detail"), 200),
                "fix": _plain_line(e.get("fix"), 200),
                "open": bool(e.get("open")),
                "closed_at": closed,
                "count": _int_or_none(e.get("count")),
                "last_seen": _iso(e.get("last_seen")),
            }
        )
    out.sort(key=lambda p: p["last_seen"] or "", reverse=True)
    out.sort(key=lambda p: not p["open"])
    return out[:KNOWN_PROBLEMS_LIMIT]


def _rows_cache(ctx):
    if not hasattr(ctx, "_rows_value"):
        ctx._rows_value = _rows(ctx)
    return ctx._rows_value


def _unreadable(ctx):
    return [
        _problem_item("unreadable", ctx.tick, name) for name in dict.fromkeys(ctx.bad)
    ]


# Names `_try` records for a file it could not read; each needs a label in `words.FILES`.
SOURCE_FILES = (
    "merges",
    "closures",
    "questions",
    "problems",
    "tick-state",
    "nodes",
    "roadmap-titles",
    "shipped-doc",
)


def _sources(ctx):
    """Health of what the page reads: the loop's snapshot commands and the dashboard's
    own files. Names come from the `words` registry, never from file text."""
    data = _try(
        lambda: store.read_json(store.state_path(ctx.project) / "node-status.json"),
        ctx.bad,
        "nodes",
        {},
    )
    out = [
        {
            "label": words.command_label(name),
            "ok": v.get("ok") is True,
            "why": None if v.get("ok") is True else "command failed",
            "at": _iso(v.get("at")),
        }
        for name, v in sorted(data.items())
        if isinstance(v, dict)
    ]
    # Any other name is a damaged slice file: one line for all of them.
    labels = dict.fromkeys(words.FILES.get(n, words.SLICE_FILES) for n in ctx.bad)
    out += [{"label": x, "ok": False, "why": "damaged", "at": None} for x in labels]
    return out


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


def _recent(ctx):
    """The owner's recently shipped record, newest first, as plain text only."""
    out = []
    for sec in _try(lambda: roadmap.shipped(ctx.cfg), ctx.bad, "shipped-doc", []):
        title = _owner_text(sec["title"], 200)
        if title:
            bullets = [
                b
                for b in (_owner_text(b, roadmap.BULLET_LIMIT) for b in sec["bullets"])
                if b
            ]
            out.append({"date": sec["date"], "title": title, "bullets": bullets})
    return out


def _activity(ctx):
    out = []
    for ev in ctx.events:
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
    reserved = runs.reserved_mb(ctx.cfg, ctx.now)
    return memory.read_memory(ctx.proc, workers, reserved, floor_mb)


# --- the view ---------------------------------------------------------------------


def build_view(project, proc="/proc", now=None, cache=None) -> dict:
    """The sealed view for one project. Raises ViewError when it breaks its schema."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cache = cache or transcript.Cache()
    cfg = projmod.load_project(project)
    ctx = _Ctx(project, cfg, now, proc)
    merges = _try(lambda: slices.merge_records(project), ctx.bad, "merges", None)
    closures = _try(lambda: slices.closure_records(project), ctx.bad, "closures", None)
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
        "decided": _decided(ctx),
        "rows": _rows_cache(ctx),
        "slots": slots,
        "memory": _memory(ctx),
        "shipped": _shipped(ctx, merges, closures),
        "recent": _recent(ctx),
        "known_problems": _known_problems(ctx),
        "activity": _activity(ctx),
        "parked": _parked(ctx),
    }
    view["controls"] = {
        "paused": view["tick"]["paused"],
        "tick_running": tick.running(project, proc=proc),
    }
    view["problems"] = problems + _unreadable(ctx)
    view["counts"]["needs_you"] = len(view["needs_you"])
    view["counts"]["problems"] = len(view["problems"])
    drawer_keys = [c["key"] for row in view["rows"] for c in row["cards"]]
    states = live.read_states(ctx, cache, drawer_keys)
    now_s = int(now.timestamp())
    view["live"] = live.live_cards(ctx, states, now_s)
    view["waiting_runs"] = live.waiting_runs(ctx)
    for w in view["waiting_runs"]:
        w["key"] = w["key"] or "?"
    view["feed"] = live.feed(ctx, cache)
    view["drawers"] = live.drawers(ctx, states, drawer_keys)
    view["kpi"] = live.kpi(ctx, view, states, now_s)
    view["sources"] = _sources(ctx)  # last: every reader above may add a damaged name
    sealed = _seal(view, VIEW_SCHEMA, _labels(cfg))
    sealed["version"] = version_of(sealed)
    return sealed
