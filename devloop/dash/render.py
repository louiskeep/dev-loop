"""The one renderer: view dict in, HTML out.

`render_page` is the whole document; `render_fragment` is the part the page polls for.
Every value is escaped, every state is a chip with an icon and a word, and nothing here
reads state: it only formats what `view.build_view` already sealed.
"""

import datetime
import html

# Words people read for each fixed code in the view. A code with no entry is a bug, and a
# test walks every code the evidence module can emit.
PHASE_TEXT = {
    "FRAME": "Frame",
    "PLAN": "Plan",
    "DEVELOP": "Develop",
    "SELF_CHECK": "Self check",
    "VERIFY": "Verify",
    "REVIEW": "Review",
    "REMEDIATE": "Remediate",
    "GATE": "Gate",
    "DOCUMENT": "Document",
    "DONE": "Done",
}
CODE_TEXT = {
    "risk": "Risk set",
    "rulebooks": "Rulebooks listed",
    "question-open": "Owner question open",
    "plan-file": "Plan file exists",
    "plan-outstanding": "No plan review pending",
    "plan-rules": "Plan names its rules",
    "plan-review": "Plan review",
    "branch": "Branch set",
    "head": "Head commit set",
    "builder-run": "Developer run applied",
    "selfcheck": "Self check passed",
    "t1": "T1 tests passed",
    "t2": "T2 tests passed",
    "full": "Full suite passed",
    "no-outstanding": "No test or review pending",
    "no-row-in-document": "No row mate in documentation",
    "plan-unchanged": "Approved plan unchanged",
    "code-review": "Code review",
    "docs": "Docs written",
}
REASON_TEXT = {
    "no-run": "no run recorded",
    "unreadable": "run record unreadable",
    "other-slice": "run belongs to another slice",
    "other-head": "run saw a different head",
    "ledger": "run does not match its ledger",
    "not-applied": "run not applied yet",
    "wrong-kind": "wrong kind of run",
    "verdict-differs": "result differs from the verdict",
    "mismatch": "run does not match the request",
    "selection": "tested a selection only",
    "order": "reviewed out of order",
    "invalid": "evidence not valid",
}
NEED_TEXT = {
    "question": ("?", "Question", "waiting for your answer", "warn"),
    "hold": ("⏸", "Held", "needs your decision", "warn"),
}
PROBLEM_TEXT = {
    "stuck": ("⚠", "Stuck", "no progress for over 2 hours", "bad"),
    "problem": ("✗", "Problem", "open known problem", "bad"),
    "unreadable": ("✗", "Unreadable", "state file could not be read", "bad"),
}
WORKER_STATE = {
    "executing": ("▶", "Executing", "info"),
    "awaiting-apply": ("⏳", "Awaiting apply", "warn"),
    "awaiting-release": ("⏳", "Awaiting release", "warn"),
}
MEMORY_STATE = {
    "ok": ("✓", "OK", "ok"),
    "tight": ("⚠", "Tight", "warn"),
    "low": ("✗", "Low", "bad"),
}
EVENT_ICON = "·"
SECTION_IDS = (
    "strip",
    "attention",
    "workers",
    "feed",
    "progress",
    "problems",
    "capacity",
    "memory",
    "shipped",
    "parked",
    "sources",
)

e = html.escape


def _chip(kind, icon, word, title=None):
    tip = f' title="{e(title)}"' if title else ""
    return (
        f'<span class="chip st-{kind}"{tip}><span class="ico" aria-hidden="true">'
        f"{e(icon)}</span>{e(word)}</span>"
    )


def _rel(iso, now):
    try:
        then = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    secs = int((now - then).total_seconds())
    if secs < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return ""


def _time(iso, now):
    if not iso:
        return '<span class="muted">n/a</span>'
    return f'<time datetime="{e(iso)}">{e(_rel(iso, now))}</time>'


def _gb(mb):
    return f"{mb / 1024:.1f} GB"


def _bar(parts, marker=None, label=""):
    """A segmented bar as inline SVG, so no inline style is needed under the CSP.

    `parts` is [(css class, fraction)]; `marker` is a 0..1 position for the floor line.
    """
    x, rects = 0.0, []
    for cls, frac in parts:
        w = max(0.0, min(frac, 1.0 - x))
        if w > 0:
            rects.append(
                f'<rect class="{cls}" x="{x * 100:.2f}" width="{w * 100:.2f}" height="10"/>'
            )
        x += w
    line = ""
    if marker is not None:
        m = max(0.0, min(marker, 1.0)) * 100
        line = f'<line class="floor" x1="{m:.2f}" x2="{m:.2f}" y1="0" y2="10"/>'
    return (
        f'<svg class="bar" viewBox="0 0 100 10" preserveAspectRatio="none" role="img" '
        f'aria-label="{e(label)}">{"".join(rects)}{line}</svg>'
    )


# --- sections ---------------------------------------------------------------------


def _memory_bar(m):
    total = max(m["total_mb"], 1)
    used = max(m["total_mb"] - m["available_mb"], 0) / total
    reserved = m["reserved_mb"] / total
    label = (
        f"Memory: {_gb(m['total_mb'] - m['available_mb'])} used, "
        f"{_gb(m['reserved_mb'])} reserved, {_gb(m['headroom_mb'])} free, "
        f"floor {_gb(m['floor_mb'])}"
    )
    return _bar(
        [("used", used), ("reserved", reserved), ("free", 1.0)],
        marker=1.0 - m["floor_mb"] / total,
        label=label,
    )


def _count(value):
    if value is None:
        return '<span class="muted" title="ledger unreadable">n/a</span>'
    return str(value)


def _question_detail(n, project):
    parts = []
    if n["question"]:
        parts.append(f'<div class="q">{e(n["question"])}</div>')
    if n["options"]:
        opts = ", ".join(e(o) for o in n["options"])
        parts.append(f'<div class="muted">Options: {opts}</div>')
    if n["recommended"]:
        parts.append(f'<div class="muted">Recommended: {e(n["recommended"])}</div>')
    if n["answer_key"]:
        cmd = f"devloop-state {project} answer {n['answer_key']} <option>"
        where = (
            f", or on the questions page ({e(n['answer_at'])})"
            if n["answer_at"]
            else ""
        )
        parts.append(f'<div class="muted">Answer: <code>{e(cmd)}</code>{where}.</div>')
    elif n["answer_at"]:
        parts.append(f'<div class="muted">Answer in {e(n["answer_at"])}</div>')
    return "".join(parts)


def _problems(v, now):
    items = []
    for n in v["problems"]:
        icon, word, text, kind = PROBLEM_TEXT[n["kind"]]
        extra = (
            f" (seen {n['count']} time{'s' if n['count'] != 1 else ''})"
            if n["count"]
            else ""
        )
        key = f" <code>{e(n['key'])}</code>" if n["key"] else ""
        items.append(
            f"<li>{_chip(kind, icon, word)}{key} {e(text)}{e(extra)} "
            f'<span class="muted">{e(n["owner"])}</span> {_time(n["since"], now)}</li>'
        )
    body = (
        f'<ul class="list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">No known problems.</p>'
    )
    return _details("problems", "Known problems", len(items), body)


def _phase_strip(c):
    cur = c["phase_index"]
    out = []
    for i, name in enumerate(PHASE_TEXT):
        label = PHASE_TEXT[name]
        if i == cur:
            out.append(f'<li class="ph cur" aria-current="step">{e(label)}</li>')
        else:
            state = "done" if i < cur else "todo"
            out.append(
                f'<li class="ph {state}"><span class="sr">{e(label)}</span></li>'
            )
    return f'<ol class="phases" aria-label="Phase">{"".join(out)}</ol>'


def _checklist_items(c):
    cl = c["checklist"]
    if not cl["available"]:
        return ""
    items = []
    for i in cl["items"]:
        icon, kind = ("✓", "ok") if i["ok"] else ("✗", "bad")
        text = CODE_TEXT[i["code"]] + (f" ({i['role']})" if i["role"] else "")
        why = f", {REASON_TEXT[i['reason']]}" if i["reason"] else ""
        word = "met" if i["ok"] else "missing"
        items.append(f"<li>{_chip(kind, icon, word)} {e(text)}{e(why)}</li>")
    return f'<ul class="list">{"".join(items)}</ul>'


def _checklist(c):
    cl = c["checklist"]
    if not cl["available"]:
        return '<span class="muted">checklist n/a</span>'
    return (
        f'<details id="cl-{e(c["key"])}" class="cl"><summary>'
        f"{cl['met']}/{cl['total']} met</summary>{_checklist_items(c)}</details>"
    )


def _card(c, now):
    chips = [_chip("idle", "□", c["risk"])] if c["risk"] else []
    if c["stuck"]:
        chips.append(_chip("bad", "⚠", "Stuck"))
    if c["held"]:
        chips.append(_chip("warn", "⏸", "Held"))
    if c["round"]:
        chips.append(_chip("idle", "↻", f"Round {c['round']}"))
    pr = ""
    if c["url"]:
        pr = (
            f' <a href="{e(c["url"])}" target="_blank" rel="noopener noreferrer">'
            f"PR #{c['pr']}</a>"
        )
    elif c["pr"] is not None:
        pr = f' <span class="muted">PR #{c["pr"]}</span>'
    attn = "1" if c["stuck"] or c["held"] else "0"
    text = f"{c['key']} {c['owner']} {c['phase']}".lower()
    return (
        f'<article class="card" data-card data-card-key="{e(c["key"])}" '
        f'data-risk="{e(c["risk"] or "")}" '
        f'data-attn="{attn}" data-text="{e(text)}">'
        f'<div class="l1"><strong>{e(c["slice"])}</strong> {" ".join(chips)}{pr}</div>'
        f'<div class="l2">{_phase_strip(c)}<span class="muted">{e(c["owner"])}</span> '
        f"{_time(c['since'], now)} {_checklist(c)}</div></article>"
    )


def _progress(v, now):
    rows = []
    for r in v["rows"]:
        cards = "".join(_card(c, now) for c in r["cards"])
        flag = _chip("bad", "⚠", "Stuck") if r["stuck"] else ""
        rows.append(
            f'<div class="row"><h3>{e(r["row"])} {flag}'
            f'<span class="muted">{len(r["cards"])} slices</span></h3>{cards}</div>'
        )
    body = "".join(rows) or '<p class="empty">No slices in progress.</p>'
    return _section("progress", "In progress", body)


def _capacity(v):
    out = []
    for s in v["slots"]:
        frac = s["used"] / s["cap"] if s["cap"] else 0
        label = f"{s['label']} {s['used']} of {s['cap']}"
        out.append(
            f'<li class="slot"><span>{e(s["label"])} <strong>{s["used"]}/{s["cap"]}</strong></span>'
            f"{_bar([('used', frac), ('free', 1.0)], label=label)}</li>"
        )
    return _section("capacity", "Capacity", f'<ul class="slots">{"".join(out)}</ul>')


def _memory_panel(v):
    m = v["memory"]
    if m is None:
        return _section(
            "memory", "Memory", '<p class="empty">Memory is not readable here.</p>'
        )
    icon, word, kind = MEMORY_STATE[m["state"]]
    head = (
        f"<p>{_chip(kind, icon, word)} {_gb(m['headroom_mb'])} headroom: "
        f"{_gb(m['available_mb'])} available of {_gb(m['total_mb'])}, "
        f"{_gb(m['reserved_mb'])} reserved for recent starts, floor {_gb(m['floor_mb'])}.</p>"
        f"{_memory_bar(m)}"
    )
    workers = "".join(
        f"<tr><td>{e(w['role'])}</td><td><code>{e(w['slice'])}</code></td>"
        f'<td class="num">{w["rss_mb"]} MB</td></tr>'
        for w in m["workers"]
    )
    top = "".join(
        f'<tr><td><code>{e(t["name"])}</code></td><td class="num">{t["rss_mb"]} MB</td></tr>'
        for t in m["top"]
    )
    wt = (
        f"<h3>Workers</h3><table><tbody>{workers}</tbody></table>"
        if workers
        else '<p class="empty">No worker is running.</p>'
    )
    tt = (
        f"<h3>Largest other processes</h3><table><tbody>{top}</tbody></table>"
        if top
        else ""
    )
    hosts = "".join(
        f"<li>{e(h['name'])} {_gb(h['available_mb'])} free</li>" for h in m["hosts"]
    )
    ht = f'<h3>Hosts</h3><ul class="list">{hosts}</ul>' if hosts else ""
    return _section("memory", "Memory", head + wt + tt + ht)


def _shipped(v, now):
    items = []
    for s in v["shipped"]:
        if s["kind"] == "merge":
            pr = (
                f'<a href="{e(s["url"])}" target="_blank" rel="noopener noreferrer">PR #{s["pr"]}</a>'
                if s["url"]
                else f"PR #{s['pr']}"
            )
            what = f"{e(s['row'])} {e(s['slice'] or '')} {pr}"
            chip = _chip("ok", "✓", "Merged")
        else:
            what = f"Row {e(s['row'])}"
            chip = _chip("ok", "✓", "Row closed")
        items.append(f"<li>{chip} {what} {_time(s['at'], now)}</li>")
    body = (
        f'<ul class="list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">Nothing has shipped yet.</p>'
    )
    return _section("shipped", "Recently shipped", body)


def _parked(v, now):
    items = []
    for c in v["parked"]:
        items.append(
            f"<li><strong>{e(c['key'])}</strong> {_chip('warn', chr(0x23F8), 'Held')} "
            f'was in {e(PHASE_TEXT[c["phase"]])}, <span class="muted">{e(c["owner"])}</span> '
            f"{_time(c['since'], now)}</li>"
        )
    body = (
        f'<ul class="list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">Nothing is parked or held.</p>'
    )
    return _details("parked", "Parked and held", len(v["parked"]), body)


def _section(sid, title, body):
    return (
        f'<section id="{sid}" data-section="{sid}"><h2>{e(title)}</h2>{body}</section>'
    )


def _details(sid, title, n, body):
    return (
        f'<section id="{sid}" data-section="{sid}"><details id="d-{sid}">'
        f'<summary><h2>{e(title)} <span class="muted">{n}</span></h2></summary>{body}'
        "</details></section>"
    )


# --- documents --------------------------------------------------------------------


def render_fragment(view, now=None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    parts = (
        render_live.kpi_strip(view, now),
        render_live.attention(view, now),
        render_live.worker_grid(view, now),
        render_live.feed(view, now),
        _progress(view, now),
        _problems(view, now),
        _capacity(view),
        _memory_panel(view),
        _shipped(view, now),
        _parked(view, now),
        render_live.drawer_sources(view, now),
    )
    return (
        f'<div id="app-body" data-version="{e(view["version"])}">{"".join(parts)}</div>'
    )


def render_page(view, now=None) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Dev loop: {e(view['project'])}</title>"
        '<link rel="stylesheet" href="/static/dash.css"></head><body>'
        '<header class="top"><h1>Dev loop</h1>'
        '<form class="filters" role="search" onsubmit="return false">'
        '<label>Search <input id="f-q" type="search" placeholder="slice, owner, phase"></label>'
        '<label>Risk <select id="f-risk"><option value="">All</option>'
        "<option>R0</option><option>R1</option><option>R2</option><option>R3</option></select></label>"
        '<label class="chk"><input id="f-attn" type="checkbox"> Needs attention only</label>'
        '</form><button id="theme" type="button">Theme</button></header>'
        '<div id="stale" class="stale" role="status" hidden>'
        "Updates stopped. Showing the last good data.</div>"
        f'<main id="app">{render_fragment(view, now)}</main>'
        '<aside id="drawer" class="drawer" hidden aria-label="Details">'
        '<div class="drawer-bar"><h2 id="drawer-title"></h2>'
        '<button id="drawer-close" type="button" aria-label="Close details">Close</button>'
        '</div><div id="drawer-body"></div></aside>'
        '<script src="/static/dash.js" defer></script></body></html>'
    )


# render_live formats with this module's helpers and this module composes its output, so
# the import sits last: every helper above exists by the time render_live loads.
from . import render_live  # noqa: E402
