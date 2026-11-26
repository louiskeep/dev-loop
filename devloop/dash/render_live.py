"""HTML for the live parts of the page: KPIs, attention, worker cards, feed, drawer sources.

Like `render.py` this only formats the sealed view. Every value goes through `html.escape`;
role icons come from a fixed set of inline SVG, never from data.
"""

from . import render as r
from . import render_control, transcript

e = r.e

HARNESS_TEXT = {"claude": "Claude", "codex": "Codex", "script": "Script"}
STATE_TEXT = {
    "starting": "starting",
    "outside": "activity not shown for this worker",
    "none": "no activity file",
    "unreadable": "activity file not readable",
}
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


def sparkline(counts: list) -> str:
    n = len(counts)
    top = max(max(counts, default=0), 1)
    pts = " ".join(
        f"{i * 60 / max(n - 1, 1):.1f},{15 - 14 * c / top:.1f}"
        for i, c in enumerate(counts)
    )
    return (
        '<svg class="spark" viewBox="0 0 60 16" role="img" '
        'aria-label="Tool calls per minute, last 15 minutes">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{pts}"/></svg>'
    )


def tag_chip(tag: str) -> str:
    cls = f"t-{tag.lower()}" if tag in transcript.TOOL_TAGS else "t-event"
    return f'<span class="tag {cls}">{e(tag)}</span>'


# --- KPI strip ---------------------------------------------------------------------


def _kpi(label, value, extra=""):
    return (
        f'<div class="kpi"><span class="k">{e(label)}</span>'
        f'<span class="v">{value}</span>{extra}</div>'
    )


def kpi_strip(v, now, token="") -> str:
    k, t = v["kpi"], v["tick"]
    if t["capped"]:
        loop = r._chip("warn", "⏸", "Capped", "usage cap active")
    elif t["paused"]:
        loop = r._chip("warn", "⏸", "Paused", "the loop is paused")
    else:
        loop = r._chip("ok", "▶", "Running")
    if k["free_mb"] is None:
        free = '<span class="muted">n/a</span>'
    else:
        icon, word, kind = r.MEMORY_STATE[k["memory_state"]]
        free = f"{r._gb(k['free_mb'])} {r._chip(kind, icon, word)}"
    tiles = (
        _kpi("Workers", f'{k["running"]}<span class="of">/{k["slots"]}</span>'),
        _kpi("Needs you", str(k["needs_you"])),
        _kpi("Merged today", r._count(k["merged_today"])),
        _kpi("Tools / min", str(k["tools_per_min"])),
        _kpi("Free memory", free),
        _kpi("Last tick", r._time(k["last_pass_at"], now)),
    )
    return (
        '<section id="strip" data-section="strip" class="strip" aria-label="Summary">'
        f'<div class="strip-head"><span class="project">{e(v["project"])}</span>{loop}'
        f"{render_control.controls(v, token)}</div>"
        f'<div class="kpis">{"".join(tiles)}</div></section>'
    )


# --- attention -----------------------------------------------------------------------


def attention(v, now) -> str:
    items = []
    for n in v["needs_you"]:
        icon, word, text, kind = r.NEED_TEXT[n["kind"]]
        key = f" <code>{e(n['key'])}</code>" if n["key"] else ""
        items.append(
            f"<li>{r._chip(kind, icon, word)}{key} {e(text)} "
            f'<span class="muted">{e(n["owner"])}</span> {r._time(n["since"], now)}'
            f"{r._question_detail(n, v['project'])}</li>"
        )
    for p in v["problems"]:
        if p["kind"] != "stuck":
            continue
        icon, word, text, kind = r.PROBLEM_TEXT["stuck"]
        items.append(
            f'<li>{r._chip(kind, icon, word)} <a href="#slice={e(p["key"])}">'
            f"<code>{e(p['key'])}</code></a> {e(text)} "
            f'<span class="muted">{e(p["owner"])}</span> {r._time(p["since"], now)}</li>'
        )
    body = (
        f'<ul class="list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">Nothing needs you right now.</p>'
    )
    return r._section("attention", "Needs you", body)


# --- worker grid ----------------------------------------------------------------------


def _doing(w) -> str:
    d = w["doing"]
    if d is None or w["state"] != "live":
        text = STATE_TEXT.get(w["state"], "no activity yet")
        return f'<div class="doing muted">{e(text)}</div>'
    target = f" <code>{e(d['target'])}</code>" if d["target"] else ""
    running = '<span class="run-dot" title="running"></span>' if d["running"] else ""
    return f'<div class="doing">{tag_chip(d["tag"])}{target} {running}</div>'


def _worker(w, now) -> str:
    key = (
        f'<a href="#slice={e(w["key"])}"><code>{e(w["key"])}</code></a>'
        if w["slice_active"]
        else f"<code>{e(w['key'])}</code>"
    )
    model = f' <span class="muted">{e(w["model"])}</span>' if w["model"] else ""
    stats = [f"{w['tools']} tools"]
    if w["errors"]:
        stats.append(f"{w['errors']} errors")
    if w["tokens_out"]:
        stats.append(f"{w['tokens_out']:,} tokens out")
    if w["partial"]:
        stats.append("partial")
    return (
        f'<article class="wcard" data-ref="{e(w["ref"])}" data-state="{e(w["state"])}">'
        f'<div class="w1">{role_icon(w["icon"])}<strong>{e(w["role"])}</strong>'
        f'<span class="badge">{e(HARNESS_TEXT[w["harness"]])}</span>{model}</div>'
        f'<div class="w2">{key} {r._time(w["started_at"], now)}</div>'
        f"{_doing(w)}"
        f'<div class="w3"><span class="muted">{e(", ".join(stats))}</span>'
        f"{sparkline(w['spark'])}</div></article>"
    )


def worker_grid(v, now) -> str:
    cards = "".join(_worker(w, now) for w in v["live"])
    grid = (
        f'<div class="wgrid">{cards}</div>'
        if cards
        else '<p class="empty">No workers running.</p>'
    )
    waiting = ""
    if v["waiting_runs"]:
        rows = "".join(
            f"<li>{r._chip(r.WORKER_STATE[w['state']][2], r.WORKER_STATE[w['state']][0], r.WORKER_STATE[w['state']][1])} "
            f"{e(w['role'])} <code>{e(w['key'])}</code> {r._time(w['started_at'], now)}</li>"
            for w in v["waiting_runs"]
        )
        waiting = f'<h3>Waiting</h3><ul class="list">{rows}</ul>'
    nodes = "".join(
        f"<li>{e(n['name'])} "
        f"{r._chip('ok', chr(0x2713), 'OK') if n['ok'] else r._chip('bad', chr(0x2717), 'Failing')} "
        f"{r._time(n['at'], now)}</li>"
        for n in v["nodes"]
    )
    node_html = f'<ul class="inline">{nodes}</ul>' if nodes else ""
    return r._section("workers", "Workers", grid + waiting + node_html)


# --- feed ------------------------------------------------------------------------------


def feed(v, now) -> str:
    items = []
    for f in v["feed"]:
        what = e(f["words"]) if f["words"] else ""
        if f["target"]:
            what += f" <code>{e(f['target'])}</code>"
        who = f' <span class="muted">{e(f["role"])}</span>' if f["role"] else ""
        key = f' <span class="muted">{e(f["key"])}</span>' if f["key"] else ""
        items.append(
            f'<li class="fi">{r._time(f["at"], now)}{tag_chip(f["tag"])}'
            f'<span class="fw">{what}{who}{key}</span></li>'
        )
    body = (
        f'<ol class="feed">{"".join(items)}</ol>'
        if items
        else '<p class="empty">No activity yet.</p>'
    )
    return r._section("feed", "Activity", body)


# --- drawer sources ---------------------------------------------------------------------


def _drawer(d, cards, now) -> str:
    runs = "".join(
        f"<li>{e(x['role'])}, {e(HARNESS_TEXT[x['harness']])} "
        f"{r._time(x['started_at'], now)}"
        + (f" {e(x['status'])}" if x["status"] else "")
        + (f" {e(x['verdict'])}" if x["verdict"] else "")
        + "</li>"
        for x in d["runs"]
    )
    # No per-call time here: twenty timestamps per drawer would swamp the fragment.
    calls = "".join(
        f"<li>{tag_chip(c['tag'])}"
        + (f" <code>{e(c['target'])}</code>" if c["target"] else "")
        + "</li>"
        for c in d["calls"]
    )
    files = "".join(
        f"<li><code>{e(f['name'])}</code> {f['edits']} edit{'s' if f['edits'] != 1 else ''}</li>"
        for f in d["files"]
    )
    card = cards.get(d["key"])
    checklist = r._checklist_items(card) if card else ""
    parts = [
        f'<h3>Runs</h3><ul class="list">{runs}</ul>' if runs else "",
        f'<h3>Recent tool calls</h3><ul class="list">{calls}</ul>' if calls else "",
        f'<h3>Files touched</h3><ul class="list">{files}</ul>' if files else "",
        f"<h3>Checklist</h3>{checklist}" if checklist else "",
    ]
    return (
        f'<div id="ds-{e(d["key"])}" data-title="{e(d["key"])}">{"".join(parts)}</div>'
    )


def drawer_sources(v, now) -> str:
    cards = {c["key"]: c for row in v["rows"] for c in row["cards"]}
    body = "".join(_drawer(d, cards, now) for d in v["drawers"])
    return f'<section id="sources" data-section="sources" hidden>{body}</section>'
