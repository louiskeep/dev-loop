"""HTML for the top bar and the slice drawer sources.

Like `render.py` this only formats the sealed view. Every value goes through `html.escape`;
role icons come from a fixed set of inline SVG, never from data.
"""

import datetime

from . import render as r
from . import render_control, words

e = r.e

HARNESS_TEXT = {"claude": "Claude", "codex": "Codex", "script": "Script"}
# One path per icon id, drawn on a 16 by 16 box. Shapes only, no avatars.
ICON_PATHS = {
    "plan": "M3 2h10v12H3zM5 5h6M5 8h6M5 11h4",
    "build": "M2 13l6-6M9 3l4 4-2 2-4-4zM2 13l1 1 2-1",
    "review": "M1 8s3-5 7-5 7 5 7 5-3 5-7 5-7-5-7-5zM8 6a2 2 0 100 4 2 2 0 000-4z",
    "test": "M6 2h4M7 2v5l-4 7h10L9 7V2M5 11h6",
    "docs": "M4 2h6l3 3v9H4zM10 2v3h3M6 8h5M6 11h5",
    "duty": "M8 2a6 6 0 100 12A6 6 0 008 2zM8 5v3l2 2",
}


def role_icon(icon: str) -> str:
    path = ICON_PATHS[icon]
    return (
        f'<svg class="role-icon" data-icon="{e(icon)}" viewBox="0 0 16 16" '
        'aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" '
        f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" d="{path}"/></svg>'
    )


# --- top bar -------------------------------------------------------------------------


def _stat(label, value):
    return f'<span class="stat"><span class="k">{e(label)}</span> {value}</span>'


def _pools(v):
    out = [
        f'<span class="pill">{e(s["label"])} <b>{s["used"]}/{s["cap"]}</b></span>'
        for s in v["slots"]
    ]
    m = v["memory"]
    if m is None:
        out.append('<span class="pill">Memory <b>n/a</b></span>')
    else:
        icon, word, kind = r.MEMORY_STATE[m["state"]]
        out.append(
            f'<span class="pill">Memory <b>{r._gb(m["headroom_mb"])}</b> '
            f"{r._chip(kind, icon, word)}</span>"
        )
    return '<span class="dot" aria-hidden="true">·</span>'.join(out)


def _capacity_body(v):
    out = []
    for s in v["slots"]:
        frac = s["used"] / s["cap"] if s["cap"] else 0
        label = f"{s['label']} {s['used']} of {s['cap']}"
        out.append(
            f'<li class="slot"><span>{e(s["label"])} <strong>{s["used"]}/{s["cap"]}</strong></span>'
            f"{r._bar([('used', frac), ('free', 1.0)], label=label)}</li>"
        )
    return f'<h3>Capacity</h3><ul class="slots">{"".join(out)}</ul>'


def _memory_body(v):
    m = v["memory"]
    if m is None:
        return '<h3>Memory</h3><p class="empty">Memory is not readable here.</p>'
    icon, word, kind = r.MEMORY_STATE[m["state"]]
    head = (
        f"<p>{r._chip(kind, icon, word)} {r._gb(m['headroom_mb'])} headroom: "
        f"{r._gb(m['available_mb'])} available of {r._gb(m['total_mb'])}, "
        f"{r._gb(m['reserved_mb'])} reserved for recent starts, floor {r._gb(m['floor_mb'])}.</p>"
        f"{r._memory_bar(m)}"
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
    hosts = "".join(
        f"<li>{e(h['name'])} {r._gb(h['available_mb'])} free</li>" for h in m["hosts"]
    )
    return (
        f"<h3>Memory</h3>{head}"
        + (
            f'<h3>Workers</h3><table class="mini"><tbody>{workers}</tbody></table>'
            if workers
            else '<p class="empty">No worker is running.</p>'
        )
        + (
            f'<h3>Largest other processes</h3><table class="mini"><tbody>{top}</tbody></table>'
            if top
            else ""
        )
        + (f'<h3>Hosts</h3><ul class="list">{hosts}</ul>' if hosts else "")
    )


def sources_line(v) -> str:
    """One line on the health of everything the page reads."""
    srcs = v["sources"]
    bad = [s for s in srcs if not s["ok"]]
    if not bad:
        n = len(srcs)
        text = f"All {n} data source{'s' if n != 1 else ''} read OK."
        return f'<p class="srcline">{e(text)}</p>'
    names = ", ".join(f"{s['label']} ({s['why']})" for s in bad)
    n = len(bad)
    text = f"{n} data source{'s' if n != 1 else ''} could not be read: {names}."
    return f'<p class="srcline bad">{e(text)}</p>'


def top_bar(v, now, token="") -> str:
    k, t = v["kpi"], v["tick"]
    if t["capped"]:
        loop = r._chip("warn", "⏸", "Capped", "usage cap active")
    elif t["paused"]:
        loop = r._chip("warn", "⏸", "Paused", "the loop is paused")
    else:
        loop = r._chip("ok", "▶", "Running")
    n = k["needs_you"]
    need = r._chip("warn", "●", str(n)) if n else "<b>0</b>"
    merged = "<b>" + r._count(k["merged_today"]) + "</b>"
    bar = (
        f'<span class="project">{e(v["project"])}</span>{loop}'
        + _stat("Last tick", r._time(k["last_pass_at"], now))
        + _stat("Needs you", need)
        + _stat("Merged today", merged)
        + '<button type="button" class="chips" data-toggle="topdetail" '
        'aria-expanded="false" aria-controls="topdetail" '
        'title="Show capacity, memory and data sources">'
        f'{_pools(v)}<span class="caret" aria-hidden="true">▾</span></button>'
        f"{render_control.controls(v, token)}"
        '<button id="theme" type="button" class="theme">Theme</button>'
    )
    panel = (
        '<div id="topdetail" class="panel" hidden>'
        f"{_capacity_body(v)}{_memory_body(v)}<h3>Data sources</h3>{sources_line(v)}</div>"
    )
    return (
        '<section id="strip" data-section="strip" class="strip" aria-label="Summary">'
        f'<div class="topbar">{bar}</div>{panel}</section>'
    )


# --- slice drawer sources -----------------------------------------------------------


def _gate_items(c) -> str:
    cl = c["checklist"]
    if not cl["available"] or not cl["items"]:
        return '<p class="empty">Nothing is needed for this stage.</p>'
    items = []
    for i in cl["items"]:
        text = r.CODE_TEXT[i["code"]] + (f" ({i['role']})" if i["role"] else "")
        why = f", {r.REASON_TEXT[i['reason']]}" if i["reason"] else ""
        chip = r._chip("ok", "✓", "yes") if i["ok"] else r._chip("bad", "✗", "not yet")
        items.append(f"<li>{e(text)}: {chip}{e(why)}</li>")
    return f'<ul class="list">{"".join(items)}</ul>'


def _minutes(start, end):
    try:
        a = datetime.datetime.fromisoformat(start)
        b = datetime.datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((b - a).total_seconds() // 60))
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def history_line(x) -> str:
    """One agent session: who, what it did, how it ended and how long it took."""
    who = x["role"]
    if x["harness"] != "script":
        who += f" ({HARNESS_TEXT[x['harness']]})"
    if x["verdict"]:
        result = words.VERDICT[x["verdict"]]
    elif x["status"]:
        result = words.RESULT[x["status"]]
    else:
        result = "no result yet" if x["ended_at"] else "running"
    if x["summary"]:
        result += f" ({x['summary']})"
    parts = [f"{who} {words.ACT[x['icon']]}", result]
    took = _minutes(x["started_at"], x["ended_at"]) if x["ended_at"] else ""
    if took:
        parts.append(took)
    return ", ".join(parts)


def _history(d) -> str:
    items = "".join(f"<li>{e(history_line(x))}</li>" for x in d["runs"])
    return (
        f'<ul class="list history">{items}</ul>'
        if items
        else '<p class="empty">No agent has worked on this slice yet.</p>'
    )


def slice_heading(c) -> str:
    """The row's plain name and the slice, as one line for titles."""
    return f"Slice {c['slice']}"


def _blurb(text) -> str:
    return f'<p class="d-desc">{e(text)}</p>' if text else ""


def _drawer(d, cards, now) -> str:
    c = cards.get(d["key"])
    if c is None:
        return ""
    pr = ""
    if c["url"]:
        pr = (
            f'<a href="{e(c["url"])}" target="_blank" rel="noopener noreferrer">'
            f"PR #{c['pr']}</a>"
        )
    elif c["pr"] is not None:
        pr = f"PR #{c['pr']}"
    risk = words.RISK.get(c["risk"] or "", "risk not set")
    meta = " ".join(
        x
        for x in (
            r._chip("info", "▸", words.stage(c["phase"], c["round"])),
            f'<span class="muted">{e(risk)}</span>',
            pr,
        )
        if x
    )
    nxt = (
        f'<p class="d-next"><strong>Next:</strong> {e(c["next_action"])}</p>'
        if c["next_action"]
        else ""
    )
    return (
        f'<div id="ds-{e(d["key"])}" data-title="{e(slice_heading(c))}">'
        f'<h3 class="d-row">Row: {e(c["title"] or c["row"])}</h3>'
        f"{_blurb(c['desc'])}"
        f'<h3 class="d-slice">Slice: {e(c["slice"])}</h3>'
        f"{_blurb(c['slice_desc'])}"
        f'<p class="d-meta">{meta}</p>{nxt}'
        "<h3>Before it can move on</h3>"
        '<p class="muted">What this step needs before the slice moves to the next stage.</p>'
        f"{_gate_items(c)}<h3>Work history</h3>{_history(d)}</div>"
    )


def drawer_sources(v, now) -> str:
    cards = {}
    for row in v["rows"]:
        for c in row["cards"]:
            cards[c["key"]] = {**c, "title": row["title"], "desc": row["desc"]}
    body = "".join(_drawer(d, cards, now) for d in v["drawers"])
    return f'<section id="sources" data-section="sources" hidden>{body}</section>'
