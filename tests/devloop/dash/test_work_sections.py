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


def _wo(phase="DEVELOP", worker=None, hours=1, **kw):
    return render_tables.waiting_on(card("a", phase, hours, **kw), worker, NOW)


def test_waiting_on_column_header_and_cell():
    c = card("a", "DEVELOP", next_action="slice runner (subagent): build the plan")
    html = render_tables.work(view(c), NOW)
    assert "Who is on it now" not in html
    body = sections(html)["Building"][2]
    assert (
        "Slice runner (subagent) working"
        in body.split('data-label="Waiting on">')[1].split("</td>")[0]
    )


def test_waiting_on_live_worker_first():
    w = {
        "icon": "build",
        "role": "Developer",
        "harness": "script",
        "model": "",
        "started_at": _iso(1),
    }
    got = _wo(worker=w, wait="owner")
    assert "Developer" in got and "You" not in got


def test_waiting_on_owner_slot_apply():
    assert ">You<" in _wo(wait="owner")
    assert "A free x slot" in _wo(wait="slot", wait_for="x")
    assert "The loop to take the result" in _wo(wait="apply")


def test_waiting_on_document():
    assert "Shipped-log entry" in _wo("DOCUMENT", hours=1)
    assert "stuck" not in _wo("DOCUMENT", hours=1)
    assert "stuck" in _wo("DOCUMENT", hours=3)


def test_waiting_on_keywords():
    cases = {
        "Merge when gate run 5 is green": "Engine gate",
        "wait for gate-selfhosted": "Engine gate",
        "Dennis to review": "Dennis review",
        "Codex GO pending": "Codex review",
        "t1 on mac": "Tests on a node",
        "t2 running": "Tests on a node",
        "full suite": "Tests on a node",
        "run the tests": "Tests on a node",
        "CI is red": "GitHub CI",
        "QA: run it": "QA working",
        "Merge when green": "Checks, then merge",
    }
    for text, want in cases.items():
        assert want in _wo(next_action=text), text
    assert "Next loop tick" in _wo(next_action="Merge PR 5: now")
    assert "Next loop tick" in _wo(next_action="decide it")  # "ci" only as a whole word


def test_waiting_on_names_the_pending_step():
    cases = {
        "Codex r3 GO + t1 GO (838 passed) on abc1234; merge when CI green": "GitHub CI",
        "Dennis GO (2 MEDIUM: note added); full suite running, started 18:38 CST": (
            "Tests on a node"
        ),
        "gate green on abc1234; main merged (def5678), engine gate 45917033510 "
        "rerun; merge when green": "Engine gate",
        "t1 GO + Codex GO; merge when engine gate is green": "Engine gate",
        "Codex delta GO on 91df318a6; waiting on targeted t1, then engine gate, CI, "
        "merge": "Tests on a node",
    }
    for text, want in cases.items():
        assert want in _wo(next_action=text), text


def test_waiting_on_precedence():
    ra = "Slice runner: build, then Codex and t1"
    assert "Slice runner working" in _wo(next_action=ra)
    assert "Developer working" in _wo(next_action="Developer: fix, engine gate later")
    assert "Engine gate" in _wo(
        next_action="Codex GO on x; merge when engine gate 123 is green"
    )
    assert "Dennis review" in _wo(next_action="codex then dennis")


def test_waiting_on_cases_beat_keywords_in_order():
    ra = "Codex review soon"
    assert ">You<" in _wo("PLAN", wait="owner", next_action=ra)
    assert "Shipped-log entry" in _wo("DOCUMENT", next_action=ra)
    assert "Codex review" in _wo(wait="loop", next_action=ra)


def test_waiting_on_stuck_and_default():
    got = _wo(wait="stuck")
    assert "Nobody" in got and "stuck" in got and "&lt;time" not in got
    assert "<time" in got
    assert "Next loop tick" in _wo()
