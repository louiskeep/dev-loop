"""The page's tables: needs you, work in progress, known problems, shipped, parked.

Formats the sealed view only; every value is escaped. Each table cell carries its column
name in `data-label` so the phone layout can stack a row as labelled lines.
"""

import datetime
import hashlib
import re

from .. import status
from . import render as r
from . import render_live, words

e = r.e


def _work_name(row, slice_name, title, key):
    """`Row name, slice S2`, or the key itself when the row is unknown."""
    if slice_name is None:
        return f"<code>{e(key)}</code>"
    return f'<strong>{e(title or row)}</strong><div class="muted">slice {e(slice_name)}</div>'


# --- needs you -----------------------------------------------------------------------

NEED_HINT = {
    "hold": "Release this held slice, or keep it held.",
    "stuck": "No progress for over 2 hours. It is waiting on your answer.",
}


def _decide(n):
    if n["kind"] != "stuck" and n["answer_key"] and n["version"]:
        kind, key = "q", n["answer_key"]
    else:
        kind, key = "slice", n["key"]
    return (
        f'<button type="button" class="act" data-open-kind="{kind}" '
        f'data-open-key="{e(key)}">Decide</button>'
    )


def needs_you(v, now) -> str:
    rows = []
    for n in v["needs_you"]:
        icon, word, kind = r.NEED_TEXT[n["kind"]]
        text = n["question"] or NEED_HINT.get(n["kind"], "Needs your answer.")
        rows.append(
            "<tr>"
            + r._cell("Type", r._chip(kind, icon, word))
            + r._cell("Work", _work_name(n["row"], n["slice"], n["title"], n["key"]))
            + r._cell("What we need from you", e(text), "clip")
            + r._cell("Recommended", e(n["recommended"] or ""), "clip")
            + r._cell("Waiting", r._span(n["since"], now))
            + r._cell("Action", _decide(n), "act-cell")
            + "</tr>"
        )
    body = (
        r._table(
            "tbl needs",
            (
                "Type",
                "Work",
                "What we need from you",
                "Recommended",
                "Waiting",
                "Action",
            ),
            rows,
        )
        if rows
        else '<p class="empty">Nothing needs you right now.</p>'
    )
    return r._section("attention", "Needs you", body, len(rows) or None)


# --- work in progress ----------------------------------------------------------------


def _model_words(w):
    """`Claude Sonnet`, `Codex` or nothing for a script, from the harness and model."""
    if w["harness"] == "script":
        return ""
    name = render_live.HARNESS_TEXT[w["harness"]]
    family = next(
        (f for f in ("opus", "sonnet", "haiku") if f in (w["model"] or "").lower()),
        None,
    )
    return f" ({name} {family.title()})" if family else f" ({name})"


# Slice runners and other roles are subagents of the live orchestrator, so no headless
# worker exists for them. The next step names the role before its first colon.
_ROLE = re.compile(
    r"^((?:slice runner|developer|qa|tech writer|tech lead|architect|planner|"
    r"senior reviewer|outside reviewer|reviewer|po)\b[^:]{0,30}):",
    re.IGNORECASE,
)


def _next_action_role(text):
    m = _ROLE.match((text or "").strip())
    if not m:
        return ""
    role = m.group(1).strip()
    return role[:1].upper() + role[1:]


# What a slice's next step is waiting for, first match wins. Keyword rules are listed in
# priority order; a leading slice runner or developer beats all of them because it owns
# its own tests and reviews.
_KEYWORDS = (
    (
        re.compile(r"\b(?:gate run|gate-selfhosted|engine gate)\b", re.IGNORECASE),
        "Engine gate",
    ),
    (re.compile(r"\bdennis\b", re.IGNORECASE), "Dennis review"),
    (re.compile(r"\bcodex\b", re.IGNORECASE), "Codex review"),
    (re.compile(r"\b(?:t1|t2|tests|suite)\b", re.IGNORECASE), "Tests on a node"),
    (re.compile(r"\bci\b", re.IGNORECASE), "GitHub CI"),
)
_WHEN_CLAUSE = re.compile(
    r"\b(?:merge when|when|waiting on)\b(.*)$", re.IGNORECASE | re.DOTALL
)
# A keyword followed by one of these (after an optional round tag) names a step that is
# finished, unless "pending" follows ("Codex GO pending" is still waiting on Codex).
_STEP_DONE = re.compile(
    r"\s*(?:r\d+\s+)?(?:go|green|passed|done)\b(?!\s+pending)", re.IGNORECASE
)
_OWN_ROLE = re.compile(r"^(?:slice runner|developer)\b", re.IGNORECASE)
_MERGE_WHEN = re.compile(r"\bmerge when\b", re.IGNORECASE)
_DOCUMENT_STUCK_HOURS = 2


def _muted(text):
    return f'<span class="muted">{e(text)}</span>'


def _hours_since(iso, now):
    try:
        then = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.UTC)
    return (now - then).total_seconds() / 3600


def _first_keyword(text):
    hits = [
        (m.start(), label)
        for pattern, label in _KEYWORDS
        for m in pattern.finditer(text)
    ]
    return min(hits)[1] if hits else None


def _pending_step(text):
    """The step the text says is still to come, or None when it names no step."""
    when = _WHEN_CLAUSE.search(text)
    if when:
        label = _first_keyword(when.group(1))
        if label:
            return label
    pending = [
        (m.start(), label)
        for pattern, label in _KEYWORDS
        for m in pattern.finditer(text)
        if not _STEP_DONE.match(text, m.end())
    ]
    return max(pending)[1] if pending else None


def waiting_on(c, worker, now):
    """One short answer to "what is this slice waiting for right now?" (an HTML fragment)."""
    if worker:
        return (
            f"{render_live.role_icon(worker['icon'])} {e(worker['role'])}"
            f"{e(_model_words(worker))} for {r._span(worker['started_at'], now)}"
        )
    if c["wait"] == "owner":
        return _muted("You")
    if c["wait"] == "slot":
        return _muted(f"A free {c['wait_for']} slot")
    if c["wait"] == "apply":
        return _muted("The loop to take the result")
    if c["phase"] == "DOCUMENT":
        out = _muted("Shipped-log entry")
        if _hours_since(c["since"], now) > _DOCUMENT_STUCK_HOURS:
            out += " " + r._chip_html("bad", "⚠", f"stuck {r._span(c['since'], now)}")
        return out
    text = (c["next_action"] or "").strip()
    if not _OWN_ROLE.match(text):
        label = _pending_step(text)
        if label:
            return _muted(label)
    role = _next_action_role(text)
    if role:
        return _muted(f"{role} working")
    if _MERGE_WHEN.search(text):
        return _muted("Checks, then merge")
    if c["wait"] == "stuck":
        return (
            _muted("Nobody")
            + " "
            + r._chip_html("bad", "⚠", f"stuck {r._span(c['since'], now)}")
        )
    return _muted("Next loop tick")


def _slice_cell(c):
    purpose = (
        f'<span class="muted">: {e(c["purpose"])}</span>' if r.has_purpose(c) else ""
    )
    return f"<strong>{e(c['slice'])}</strong>{purpose}"


def _pr(c):
    if c["url"]:
        return (
            f'<a href="{e(c["url"])}" target="_blank" rel="noopener noreferrer">'
            f"PR #{c['pr']}</a>"
        )
    return f"PR #{c['pr']}" if c["pr"] is not None else ""


def _stage(c, now):
    out = e(
        words.STAGE.get(c["phase"])
        and words.stage(c["phase"], c["round"])
        or c["phase"]
    )
    if c["since"]:
        out += f' <span class="muted">moved {r._span(c["since"], now)} ago</span>'
    if c["stuck"]:
        out += " " + r._chip_html("bad", "⚠", f"stuck {r._span(c['since'], now)}")
    return out


def _row_cell(row):
    title = row["title"]
    if title:
        return f'<strong>{e(title)}</strong><div class="muted"><code>{e(row["row"])}</code></div>'
    return f"<code>{e(row['row'])}</code>"


# Stage sections of the work table, in page order: (title, phases, open by default).
WORK_SECTIONS = (
    ("Ready to merge", ("GATE",), True),
    ("Testing and review", ("SELF_CHECK", "VERIFY", "REVIEW"), True),
    ("Fixing", ("REMEDIATE",), True),
    ("Building", ("DEVELOP",), True),
    ("Planning", ("FRAME", "PLAN"), False),
    ("Docs", ("DOCUMENT",), False),
)
WORK_HEADS = ("Row", "Slice", "Stage", "Waiting on", "Next step", "PR")


def _work_row(row, c, workers, now):
    return (
        f'<tr class="slice" tabindex="0" data-card-key="{e(c["key"])}" '
        f'data-open-kind="slice" data-open-key="{e(c["key"])}">'
        + r._cell("Row", _row_cell(row))
        + r._cell("Slice", _slice_cell(c))
        + r._cell("Stage", _stage(c, now))
        + r._cell("Waiting on", waiting_on(c, workers.get(c["key"]), now))
        + r._cell("Next step", e(c["next_action"] or ""), "clip")
        + r._cell("PR", _pr(c))
        + "</tr>"
    )


def _oldest_first(pairs):
    """Sort by last phase move, oldest first; a slice with no move time goes last."""
    return sorted(pairs, key=lambda p: (not p[1]["since"], p[1]["since"] or ""))


def work(v, now) -> str:
    workers = {}
    for w in v["live"]:
        workers.setdefault(w["key"], w)
    pairs = [(row, c) for row in v["rows"] for c in row["cards"]]
    count = len(pairs)
    if not pairs:
        body = '<p class="empty">No slices in progress.</p>'
        return r._section("progress", "Work in progress", body, None)
    listed = {p for _, phases, _ in WORK_SECTIONS for p in phases}
    groups = [(t, ph, op) for t, ph, op in WORK_SECTIONS]
    parts = []
    for title, phases, is_open in groups + [("Other", None, True)]:
        mine = [
            p
            for p in pairs
            if (p[1]["phase"] in phases if phases else p[1]["phase"] not in listed)
        ]
        if not mine:
            continue
        rows = [_work_row(row, c, workers, now) for row, c in _oldest_first(mine)]
        sid = "work-" + title.lower().replace(" ", "-")
        parts.append(
            f'<details class="work-group" id="{sid}"{" open" if is_open else ""}>'
            f'<summary><h3>{e(title)} <span class="count">({len(rows)})</span></h3>'
            f"</summary>{r._table('tbl work', WORK_HEADS, rows)}</details>"
        )
    return r._section("progress", "Work in progress", "".join(parts), count)


# --- known problems ------------------------------------------------------------------


def _seen(p, now):
    if not p["count"]:
        return ""
    times = f"{p['count']} time{'s' if p['count'] != 1 else ''}"
    if p["last_seen"]:
        return f"{times}, last {r._time(p['last_seen'], now)}"
    return times


def known_problems(v, now) -> str:
    rows = []
    for p in v["known_problems"]:
        what = e(p["impact"] or p["key"])
        if p["detail"]:
            what += f'<div class="muted">{e(p["detail"])}</div>'
        if p["open"]:
            status_cell = r._chip("bad", "✗", "Open")
        else:
            day = (p["closed_at"] or "")[:10]
            status_cell = r._chip("ok", "✓", f"Fixed on {day}")
        row_cls = "" if p["open"] else ' class="done"'
        rows.append(
            f"<tr{row_cls}>"
            + r._cell("Problem", what)
            + r._cell("Fix", e(p["fix"] or "No fix recorded yet."))
            + r._cell("Status", status_cell)
            + r._cell("Seen", _seen(p, now))
            + "</tr>"
        )
    n_open = sum(1 for p in v["known_problems"] if p["open"])
    body = (
        r._table("tbl problems", ("Problem", "Fix", "Status", "Seen"), rows)
        if rows
        else '<p class="empty">No known problems.</p>'
    )
    return r._details("problems", "Known problems", n_open, body)


# --- recently shipped ----------------------------------------------------------------


def _day(iso):
    try:
        t = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.UTC)
    return t.astimezone(status.CHICAGO).date().isoformat()


def _loop_entry(s, titles):
    name = titles.get(s["row"]) or s["row"]
    if s["kind"] == "merge":
        pr = f" (PR #{s['pr']})" if s["pr"] is not None else ""
        return f"Merged {name} {s['slice'] or ''}{pr}".replace("  ", " ")
    return f"Closed row {name}"


def _shipped_items(v):
    """Doc entries plus loop merges newer than the newest doc entry, newest first."""
    titles = {c["row"]: row["title"] for row in v["rows"] for c in row["cards"]}
    newest = v["recent"][0]["date"] if v["recent"] else ""
    items = [(m["date"], m["title"], m["bullets"], "doc") for m in v["recent"]]
    for s in v["shipped"]:
        day = _day(s["at"])
        if day > newest:
            items.append((day, _loop_entry(s, titles), [], "loop"))
    items.sort(key=lambda i: i[0], reverse=True)
    return items[:10]


def shipped(v, now) -> str:
    out = []
    for date, title, bullets, src in _shipped_items(v):
        head = (
            f'<span class="date">{e(date)}</span> <span class="ttl">{e(title)}</span>'
        )
        if bullets:
            ident = hashlib.sha256(f"{date} {title}".encode()).hexdigest()[:8]
            items = "".join(f"<li>{e(b)}</li>" for b in bullets)
            out.append(
                f'<li><details id="d-ship-{ident}"><summary>{head}</summary>'
                f'<ul class="bullets">{items}</ul></details></li>'
            )
        else:
            out.append(f'<li class="plain">{head}</li>')
    body = (
        f'<ul class="ships">{"".join(out)}</ul>'
        if out
        else '<p class="empty">Nothing has shipped yet.</p>'
    )
    return r._section("shipped", "Recently shipped", body)


# --- waiting on other work -----------------------------------------------------------


def _waiting_for(c):
    if c["released"]:
        return "Released by you, resumes on the next tick"
    text = e(c["waiting_text"] or "Not stated")
    if c["waiting_key"]:
        return (
            f'<button type="button" class="link" data-open-kind="slice" '
            f'data-open-key="{e(c["waiting_key"])}">{text}</button>'
        )
    return text


def parked(v, now) -> str:
    """Slices waiting on something other than the owner. Owner holds live in Needs you."""
    rows = []
    for c in v["parked"]:
        if c["hold"]:
            continue
        rows.append(
            "<tr>"
            + r._cell("Row", e(c["row_title"] or c["row"]))
            + r._cell("Slice", f"<strong>{e(c['slice'])}</strong>")
            + r._cell("Waiting for", _waiting_for(c))
            + r._cell("Since", r._span(c["since"], now))
            + "</tr>"
        )
    body = (
        r._table("tbl parked", ("Row", "Slice", "Waiting for", "Since"), rows)
        if rows
        else '<p class="empty">Nothing is waiting on other work.</p>'
    )
    return r._details("parked", "Waiting on other work", len(rows), body)
