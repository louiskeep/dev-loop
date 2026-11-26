"""The one renderer: view dict in, HTML out.

`render_page` is the whole document; `render_fragment` is the part the page polls for.
Every value is escaped, every state is a chip with an icon and a word, and nothing here
reads state: it only formats what `view.build_view` already sealed.
"""

import datetime
import html

from . import words


# Words people read for each fixed code in the view. A code with no entry is a bug, and a
# test walks every code the evidence module can emit. Stage words live in `words.STAGE`.
# Each gate item as a statement; the page adds "yes" or "not yet".
CODE_TEXT = {
    "risk": "Risk level is set",
    "rulebooks": "Rulebooks are listed",
    "question-open": "No owner question is waiting",
    "plan-file": "Plan file exists",
    "plan-outstanding": "No plan review is pending",
    "plan-rules": "Plan names its rules",
    "plan-review": "Plan is reviewed",
    "branch": "Branch is set",
    "head": "Head commit is set",
    "builder-run": "Developer work is applied",
    "selfcheck": "Self check passed",
    "t1": "Tests pass on the latest code",
    "t2": "Wider tests pass on the latest code",
    "full": "Full test suite passes",
    "no-outstanding": "No test or review is still running",
    "no-row-in-document": "No other slice of the row is being documented",
    "plan-unchanged": "Approved plan has not changed",
    "code-review": "Code is reviewed",
    "docs": "Docs are written",
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
    "question": ("\u25c6", "Question", "warn"),
    "hold": ("⏸", "Hold", "warn"),
    "stuck": ("⚠", "Stuck", "bad"),
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
    "progress",
    "problems",
    "shipped",
    "decided",
    "parked",
    "sources",
    "qsources",
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


def has_purpose(card) -> bool:
    """A purpose worth showing: the loop often writes only the row and slice names."""
    p = card["purpose"]
    return (
        bool(p) and p.replace(" ", "").lower() != (card["row"] + card["slice"]).lower()
    )


def _span(iso, now):
    """A length of time such as `12m`, kept current by the page script (`data-span`)."""
    if not iso:
        return '<span class="muted">n/a</span>'
    text = _rel(iso, now)
    text = "0m" if text == "just now" else text.removesuffix(" ago")
    return f'<time datetime="{e(iso)}" data-span>{e(text)}</time>'


def _chip_html(kind, icon, inner):
    """A chip whose text is already escaped HTML (a word plus a `<time>`)."""
    return (
        f'<span class="chip st-{kind}"><span class="ico" aria-hidden="true">'
        f"{e(icon)}</span>{inner}</span>"
    )


def _section(sid, title, body, count=None):
    n = f' <span class="count">{count}</span>' if count is not None else ""
    return f'<section id="{sid}" data-section="{sid}"><h2>{e(title)}{n}</h2>{body}</section>'


def _details(sid, title, n, body):
    return (
        f'<section id="{sid}" data-section="{sid}"><details id="d-{sid}">'
        f'<summary><h2>{e(title)} <span class="count">{n}</span></h2></summary>{body}'
        "</details></section>"
    )


def _table(cls, heads, rows):
    """A table whose cells carry their column name, so the phone layout can label them."""
    th = "".join(f"<th>{e(h)}</th>" for h in heads)
    return (
        f'<table class="{cls}"><thead><tr>{th}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _cell(label, inner, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f'<td data-label="{e(label)}"{c}>{inner}</td>'


# --- loop steps strip ------------------------------------------------------------

# One line per step, in order, for the strip at the top of the page.
LOOP_STEPS = (
    ("FRAME", "Pick the row and slice, write what and why"),
    ("PLAN", "Write the plan and its acceptance tests; reviewed before build"),
    ("DEVELOP", "A builder writes the code and its tests"),
    ("SELF_CHECK", "The builder runs its own lint and tests"),
    ("VERIFY", "QA runs the change's own tests on a test node"),
    ("REVIEW", "Codex and Dennis review the diff"),
    ("REMEDIATE", "Fix review findings, then back to review"),
    ("GATE", "Green CI and gate tests on the exact head, then merge"),
    ("DOCUMENT", "Docs and changelog catch up with what shipped"),
)


def loop_steps() -> str:
    items = "".join(
        f'<li><span class="step-name">{e(words.STAGE[k])}</span>'
        f'<span class="step-desc">{e(d)}</span></li>'
        for k, d in LOOP_STEPS
    )
    return (
        '<section class="loop-steps" aria-label="Dev loop steps">'
        f"<ol>{items}</ol></section>"
    )


# --- documents --------------------------------------------------------------------


def render_fragment(view, now=None, token="", copy_path="") -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    parts = (
        loop_steps(),
        render_live.top_bar(view, now, token),
        render_tables.needs_you(view, now),
        render_tables.work(view, now),
        render_tables.known_problems(view, now),
        render_tables.shipped(view, now),
        render_control.decided_list(view, now),
        render_tables.parked(view, now),
        render_live.drawer_sources(view, now),
        render_control.question_sources(view, token, copy_path),
    )
    return (
        f'<div id="app-body" data-version="{e(view["version"])}">{"".join(parts)}</div>'
    )


def render_page(view, now=None, token="", copy_path="") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Dev loop: {e(view['project'])}</title>"
        '<link rel="stylesheet" href="/static/dash.css"></head><body>'
        '<div id="stale" class="stale" role="status" hidden>'
        "Updates stopped. Showing the last good data.</div>"
        f'<main id="app">{render_fragment(view, now, token, copy_path)}</main>'
        '<aside id="drawer" class="drawer" hidden aria-label="Details">'
        '<div class="drawer-bar"><h2 id="drawer-title"></h2>'
        '<button id="drawer-close" type="button" aria-label="Close details">Close</button>'
        '</div><div id="drawer-body"></div></aside>'
        '<script src="/static/dash.js" defer></script></body></html>'
    )


# The other renderers format with this module's helpers and this module composes their
# output, so the import sits last: every helper above exists by the time they load.
from . import render_control, render_live, render_tables  # noqa: E402
