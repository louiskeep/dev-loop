"""The work table: stage sections, oldest first, collapsed Docs and Planning."""

import datetime
import re

from devloop.dash import render_tables

NOW = datetime.datetime(2026, 11, 27, 12, 0, tzinfo=datetime.timezone.utc)


def _iso(hours):
    return (NOW - datetime.timedelta(hours=hours)).isoformat()


def card(key, phase, hours=1, **kw):
    c = {
        "key": key,
        "slice": key,
        "phase": phase,
        "round": 0,
        "since": _iso(hours) if hours is not None else None,
        "stuck": False,
        "wait": "loop",
        "wait_for": None,
        "next_action": "",
        "pr": None,
        "url": None,
        "purpose": None,
    }
    c.update(kw)
    return c


def view(*cards):
    return {
        "live": [],
        "rows": [{"row": "R1", "title": None, "cards": list(cards)}],
    }


def sections(html):
    out = {}
    for m in re.finditer(
        r'<details class="work-group" id="[^"]+"( open)?><summary><h3>(.*?) <span '
        r'class="count">\((\d+)\)</span></h3></summary>(.*?)</details>',
        html,
        re.S,
    ):
        out[m.group(2)] = (bool(m.group(1)), int(m.group(3)), m.group(4))
    return out


def test_section_order_and_membership():
    cards = [
        card("d", "DOCUMENT"),
        card("f", "FRAME"),
        card("p", "PLAN"),
        card("b", "DEVELOP"),
        card("x", "REMEDIATE", round=2),
        card("s", "SELF_CHECK"),
        card("v", "VERIFY"),
        card("rv", "REVIEW"),
        card("g", "GATE"),
    ]
    html = render_tables.work(view(*cards), NOW)
    found = sections(html)
    assert list(found) == [
        "Ready to merge",
        "Testing and review",
        "Fixing",
        "Building",
        "Planning",
        "Docs",
    ]
    assert [found[k][1] for k in found] == [1, 3, 1, 1, 2, 1]
    assert 'data-card-key="rv"' in found["Testing and review"][2]
    assert "Fixing review findings (round 2)" in found["Fixing"][2]
    assert "Other" not in found
    assert 'data-open-kind="slice" data-open-key="g"' in found["Ready to merge"][2]


def test_empty_sections_are_left_out():
    found = sections(render_tables.work(view(card("b", "DEVELOP")), NOW))
    assert list(found) == ["Building"]


def test_oldest_first_and_moved_text():
    html = render_tables.work(
        view(
            card("new", "VERIFY", 1),
            card("old", "REVIEW", 30),
            card("none", "VERIFY", None),
        ),
        NOW,
    )
    body = sections(html)["Testing and review"][2]
    assert body.index('data-card-key="old"') < body.index('data-card-key="new"')
    assert body.index('data-card-key="new"') < body.index('data-card-key="none"')
    assert "moved <time" in body and ">1d</time> ago" in body


def test_docs_and_planning_start_collapsed():
    found = sections(
        render_tables.work(
            view(card("d", "DOCUMENT"), card("p", "PLAN"), card("b", "DEVELOP")), NOW
        )
    )
    assert found["Docs"][0] is False and found["Planning"][0] is False
    assert found["Building"][0] is True


def test_unknown_phase_goes_to_other_last():
    html = render_tables.work(
        view(card("w", "WEIRD"), card("pk", "PARKED"), card("b", "DEVELOP")), NOW
    )
    found = sections(html)
    assert list(found) == ["Building", "Other"]
    assert found["Other"][1] == 2 and found["Other"][0] is True
    assert 'data-card-key="w"' in found["Other"][2]


def test_who_from_next_action_role():
    c = card("a", "DEVELOP", next_action="slice runner (subagent): build the plan")
    body = sections(render_tables.work(view(c), NOW))["Building"][2]
    assert (
        "Slice runner (subagent)"
        in body.split('data-label="Who is on it now">')[1].split("</td>")[0]
    )
    for text, role in (
        ("Developer (subagent): fix", "Developer (subagent)"),
        ("QA: run tests", "QA"),
        ("Tech writer (subagent): docs", "Tech writer (subagent)"),
    ):
        got = render_tables._who(card("a", "DEVELOP", next_action=text), {}, NOW)
        assert role in got and "Waiting for the loop" not in got


def test_who_keeps_other_cases_before_the_role():
    ra = "slice runner (subagent): go"
    assert "Waiting on you" in render_tables._who(
        card("a", "PLAN", wait="owner", next_action=ra), {}, NOW
    )
    assert "Waiting for a x slot" in render_tables._who(
        card("a", "PLAN", wait="slot", wait_for="x", next_action=ra), {}, NOW
    )
    assert "Waiting for the loop" in render_tables._who(
        card("a", "PLAN", next_action="Merge PR 5: now"), {}, NOW
    )
