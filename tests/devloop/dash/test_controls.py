"""Loop control: question class and detail, answer rules, one write path, POST actions."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import notes, slices, store

STATE_BIN = str(Path(__file__).resolve().parents[3] / "bin" / "devloop-state")


def events(home):
    path = home / "p" / "events.jsonl"
    return (
        [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    )


def ask(proj, key="q1", **kw):
    args = dict(question="Pick", options=["A", "B"], recommended="A")
    args.update(kw)
    return notes.ask(proj, key, **args)


def cli(home, *args):
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    env.pop("DEVLOOP_HEADLESS", None)
    return subprocess.run(
        [sys.executable, STATE_BIN, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd="/",
    )


def posts_to(tmp_path, cfg_edit):
    log = tmp_path / "posts.log"
    script = tmp_path / "jnotify"
    script.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1]) + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    cfg_edit(lambda c: c.update(notify={"cmd": [str(script)]}))
    return lambda: [json.loads(x) for x in log.read_text().splitlines()]


def stored(proj, key="q1"):
    return notes.load_questions(notes.questions_path(proj))[1][key]


# --- Task 9: class, detail, markers, versions ------------------------------------


def test_ask_records_class_and_detail(proj, home):
    ask(proj, detail="It matters.  Because of x.", klass="tech")
    page = (home / "p" / "questions.md").read_text()
    assert "- Detail: It matters. Because of x." in page and "- Class: tech" in page
    q = stored(proj)
    assert (q["detail"], q["klass"], q["wait"]) == (
        "It matters. Because of x.",
        "tech",
        False,
    )
    with pytest.raises(notes.NotesError, match="class"):
        ask(proj, "q2", klass="maybe")


def test_tech_question_is_decided_now(proj, home, cfg_edit, tmp_path):
    posts = posts_to(tmp_path, cfg_edit)
    q = ask(proj, recommended="B", klass="tech")
    assert q["applied"] == "B" and q["answer"] is None
    last = events(home)[-1]
    assert (last["kind"], last["key"]) == ("question-decided", "q1")
    assert "Tech lead decided q1: B. Override on the dashboard." in posts()[-1]


def test_product_question_waits(proj, home):
    q = ask(proj, klass="product")
    assert q["applied"] is None and q["wait"] is True
    assert events(home)[-1]["kind"] == "question-waiting"


def test_class_defaults_to_product(proj):
    q = ask(proj)
    assert q["klass"] == "product" and q["wait"] is True and q["applied"] is None


def test_wait_flag_forces_product(proj, home):
    assert ask(proj, klass="tech", wait=True)["klass"] == "product"
    r = cli(
        home,
        "p",
        *("ask", "q2", "--question", "Pick", "--option", "A", "--option", "B"),
        *("--recommended", "A", "--class", "tech", "--wait"),
    )
    assert r.returncode == 0, r.stderr
    assert stored(proj, "q2")["klass"] == "product"
    r = cli(
        home,
        "p",
        *("ask", "q3", "--question", "Pick", "--option", "A", "--option", "B"),
        *("--recommended", "A", "--class", "tech", "--detail", "why"),
    )
    assert r.returncode == 0, r.stderr
    assert (stored(proj, "q3")["klass"], stored(proj, "q3")["detail"]) == (
        "tech",
        "why",
    )


OLD_PAGE = """<!-- devloop:questions -->

### old-tech
- Question: Old one
- Options: A | B
- Recommended: A
- Wait: no
- Asked: 2026-01-01T00:00:00+00:00
- Applied: A
- Answer: 

### old-product
- Question: Old two
- Options: A | B
- Recommended: A
- Wait: yes
- Asked: 2026-01-01T00:00:00+00:00
- Applied: 
- Answer: 
<!-- /devloop:questions -->
"""


def test_old_questions_page_class_from_wait(proj, home):
    (home / "p" / "questions.md").write_text(OLD_PAGE)
    assert stored(proj, "old-tech")["klass"] == "tech"
    assert stored(proj, "old-tech")["wait"] is False
    assert stored(proj, "old-product")["klass"] == "product"
    assert stored(proj, "old-product")["wait"] is True


def test_old_wait_no_record_survives_rewrite(proj, home):
    (home / "p" / "questions.md").write_text(OLD_PAGE)
    ask(proj, "fresh", klass="tech")
    page = (home / "p" / "questions.md").read_text()
    block = page[page.index("### old-tech") : page.index("### old-product")]
    assert "- Wait: no" in block and "- Applied: A" in block
    assert stored(proj, "old-tech")["klass"] == "tech"


def test_managed_markers_refused(proj, home, cfg_edit, tmp_path):
    for marker in (notes.BLOCK_START, notes.BLOCK_END):
        for field in ("question", "detail", "option"):
            kw = {
                "question": {"question": f"a {marker} b"},
                "detail": {"detail": f"a {marker} b"},
                "option": {"options": [f"x{marker}", "B"], "recommended": "B"},
            }[field]
            with pytest.raises(notes.NotesError, match="markers"):
                ask(proj, **kw)
    # An inline edit of the Answer line is refused at sync; the page stays readable.
    page = tmp_path / "owner-page.md"
    page.write_text("# Mine\nabove\n")
    cfg_edit(lambda c: c.update(questions={"path": str(page)}))
    ask(proj, klass="tech")
    page.write_text(page.read_text() + "below\n")
    bad = page.read_text().replace("- Answer:", f"- Answer: {notes.BLOCK_END}", 1)
    page.write_text(bad)
    assert notes.read_answers(proj) == []
    text = page.read_text()
    assert text.startswith("# Mine\nabove\n") and text.endswith("below\n")
    assert stored(proj)["answer"] == notes.BLOCK_END
    page.write_text(bad.replace(f"- Answer: {notes.BLOCK_END}", "- Answer: B"))
    assert notes.read_answers(proj) == ["q1"]
    assert stored(proj)["applied"] == "B"
    assert page.read_text().endswith("below\n")
    # Problems and lessons still take any text.
    notes.problem(proj, "k", impact=f"x {notes.BLOCK_START}", fix="f")
    notes.lesson(proj, "l", area="a", wrong=notes.BLOCK_END, instead="i")


def test_version_digest_fixtures(proj):
    q = ask(proj, klass="tech", detail="d")
    rec = {**q, "asked_at": "2026-01-01T00:00:00+00:00"}
    assert notes.question_version(rec) == "dbf3bb5294c5e775"
    assert notes.question_version(rec) == store.canonical_digest(rec)
    assert notes.question_version({**rec, "answer": "B"}) != notes.question_version(rec)


# --- Task 9b: answer rules, idempotent effects ---------------------------------------


def version(proj, key="q1"):
    return notes.question_version(stored(proj, key))


def dash_answer(proj, key, choice, ver=None, **kw):
    ver = version(proj, key) if ver is None else ver
    return notes.answer(proj, key, choice, expect_version=ver, via="dashboard", **kw)


def answered(home):
    return [e for e in events(home) if e["kind"] == "question-answered"]


def hold_slice(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    notes.ask(
        proj, k, question="Spend?", options=["yes", "no"], recommended="no", wait=True
    )
    slices.move(proj, k, "HELD", "R3 waits")
    return k


def owner_park(proj, mk_slice):
    k = mk_slice(proj, phase="REMEDIATE", round=2)
    slices.move(proj, k, "PARKED", "failed-twice")
    return k


def on_lock(monkeypatch, proj, nth, action):
    """Run `action` just before the nth acquisition of the questions lock."""
    real, count = store.locked, [0]
    lock = notes._lock_for(proj)

    def locked(path):
        if Path(path) == lock:
            count[0] += 1
            if count[0] == nth:
                action()
        return real(path)

    monkeypatch.setattr(store, "locked", locked)
    return real


def test_other_answer_stored_and_validated(proj, home, mk_slice):
    ask(proj)
    notes.answer(proj, "q1", "other: use the slower path")
    page = (home / "p" / "questions.md").read_text()
    assert "- Answer: other: use the slower path" in page
    assert stored(proj)["applied"] == "other: use the slower path"
    for bad in ("other: ", "other: a\nb", "other: " + "x" * 501, "other:  x"):
        ask(proj, "q2")
        with pytest.raises(notes.NotesError):
            notes.answer(proj, "q2", bad)
        assert stored(proj, "q2")["answer"] is None
    ask(proj, "q3")
    notes.answer(proj, "q3", "other: " + "x" * 500)
    k = hold_slice(proj, mk_slice)
    with pytest.raises(notes.NotesError):
        notes.answer(proj, f"hold:{k}", "other: release it")
    assert slices.load(proj, k)["phase"] == "HELD"


def test_answer_with_old_version_is_refused(proj, home):
    ask(proj)
    old = version(proj)
    ask(proj, question="Pick again")
    with pytest.raises(notes.AnswerStale, match="This question changed"):
        dash_answer(proj, "q1", "A", old)
    assert stored(proj)["answer"] is None and answered(home) == []


def test_removed_option_on_reask_is_stale(proj, home):
    ask(proj, options=["A", "B"])
    old = version(proj)
    ask(proj, options=["A", "C"])
    with pytest.raises(notes.AnswerStale):
        dash_answer(proj, "q1", "B", old)
    assert stored(proj)["answer"] is None


def test_unknown_key_is_gone(proj):
    with pytest.raises(notes.AnswerGone, match="This question is gone"):
        dash_answer(proj, "nope", "A", "0" * 16)


def test_reask_between_check_and_lock_is_refused(proj, home, monkeypatch):
    ask(proj)
    old = version(proj)
    on_lock(monkeypatch, proj, 1, lambda: ask(proj, question="Changed"))
    with pytest.raises(notes.AnswerStale):
        dash_answer(proj, "q1", "A", old)
    q = stored(proj)
    assert q["question"] == "Changed" and q["answer"] is None and q["decision"] is None
    assert answered(home) == []


def test_cli_answer_unchanged(proj, home):
    ask(proj, options=["A", "B", "C"])
    r = cli(home, "p", "answer", "q1", "B")
    assert r.returncode == 0 and "Answer saved." in r.stdout
    assert stored(proj)["applied"] == "B"
    r = cli(home, "p", "answer", "q1", "B")
    assert r.returncode == 0 and "Already answered." in r.stdout
    assert cli(home, "p", "answer", "q1", "C").returncode == 0  # a CLI change, as today
    assert stored(proj)["applied"] == "C"
    assert cli(home, "p", "answer", "q1", "Z").returncode == 3
    assert [e["via"] for e in answered(home)] == ["cli", "cli"]


def test_override_of_tech_decision_records_owner_answer(proj, home):
    ask(proj, klass="tech", recommended="A")
    assert stored(proj)["applied"] == "A"
    assert dash_answer(proj, "q1", "B") == notes.MSG_SAVED
    q = stored(proj)
    assert (q["answer"], q["applied"], q["recommended"]) == ("B", "B", "A")
    assert q["decision"]["status"] == "done" and q["decision"]["source"] == "dashboard"
    (ev,) = answered(home)
    assert ev["via"] == "dashboard" and ev["decision"] == q["decision"]["id"]
    assert ev["at"] == q["answered_at"]
    dash_answer(proj, "q1", "A")  # override the override
    assert stored(proj)["applied"] == "A" and len(answered(home)) == 2


def test_duplicate_same_answer_is_already_answered(proj, home):
    ask(proj)
    first = version(proj)
    dash_answer(proj, "q1", "A")
    with pytest.raises(notes.AlreadyAnswered, match="Already answered"):
        dash_answer(proj, "q1", "A")  # a fresh tab, same choice
    with pytest.raises(notes.AlreadyAnswered):
        dash_answer(proj, "q1", "A", first)  # the double click: the saved pair
    with pytest.raises(notes.AlreadyAnswered):
        dash_answer(proj, "q1", "B")  # a product answer is final from the page
    assert len(answered(home)) == 1 and stored(proj)["applied"] == "A"


def test_keep_held_then_approve(proj, home, mk_slice):
    k = hold_slice(proj, mk_slice)
    hk = f"hold:{k}"
    dash_answer(proj, hk, "keep-held")
    assert slices.load(proj, k)["phase"] == "HELD"
    dash_answer(proj, hk, "approve")
    assert slices.load(proj, k)["phase"] == "FRAME"
    with pytest.raises(notes.AlreadyAnswered):
        dash_answer(proj, hk, "keep-held")
    assert [e["via"] for e in answered(home)] == ["dashboard", "dashboard"]


def test_owner_park_keep_held_then_approve_resumes(proj, mk_slice):
    k = owner_park(proj, mk_slice)
    hk = f"hold:{k}"
    dash_answer(proj, hk, "keep-held")
    assert slices.load(proj, k)["owner_answered"] is False
    with pytest.raises(slices.SliceError):
        slices.move(proj, k, "REMEDIATE", "owner approved")
    dash_answer(proj, hk, "approve")
    assert slices.load(proj, k)["owner_answered"] is True
    slices.move(proj, k, "REMEDIATE", "owner approved")
    assert slices.load(proj, k)["phase"] == "REMEDIATE"


def test_page_sync_rechecks_under_lock(proj, home, mk_slice, monkeypatch):
    k = hold_slice(proj, mk_slice)
    hk = f"hold:{k}"
    page = home / "p" / "questions.md"
    text = page.read_text()
    cut = text.index(f"### {hk}")
    page.write_text(
        text[:cut] + text[cut:].replace("- Answer:", "- Answer: approve", 1)
    )

    def replace_question():
        page.write_text(page.read_text().replace("Release the hold", "Free the hold"))

    real_locked = on_lock(monkeypatch, proj, 2, replace_question)
    assert notes.read_answers(proj) == []
    assert slices.load(proj, k)["phase"] == "HELD" and answered(home) == []
    assert stored(proj, hk)["decision"] is None
    monkeypatch.setattr(store, "locked", real_locked)
    assert notes.read_answers(proj) == [hk]  # the next sync sees the new version
    assert slices.load(proj, k)["phase"] == "FRAME" and len(answered(home)) == 1


def test_failed_effect_is_finished_on_retry(proj, home, mk_slice, monkeypatch):
    k = hold_slice(proj, mk_slice)
    hk = f"hold:{k}"
    ver = version(proj, hk)
    real = slices.release_hold

    def boom(*a, **kw):
        raise OSError("disk went away")

    monkeypatch.setattr(slices, "release_hold", boom)
    with pytest.raises(notes.AnswerPending, match="saved, but applying it did not"):
        dash_answer(proj, hk, "approve", ver)
    q = stored(proj, hk)
    assert q["answer"] == "approve" and q["decision"]["status"] == "pending"
    assert slices.load(proj, k)["phase"] == "HELD" and answered(home) == []
    monkeypatch.setattr(slices, "release_hold", real)
    assert dash_answer(proj, hk, "approve", ver) == notes.MSG_FINISHED
    assert stored(proj, hk)["decision"]["status"] == "done"
    assert slices.load(proj, k)["phase"] == "FRAME" and len(answered(home)) == 1
    with pytest.raises(notes.AlreadyAnswered):
        dash_answer(proj, hk, "approve", ver)


@pytest.mark.parametrize("stop", ["before_append", "after_append"])
@pytest.mark.parametrize("recover", ["retry", "sync"])
def test_answer_event_once_across_append_failure(
    proj, home, mk_slice, monkeypatch, stop, recover
):
    k = hold_slice(proj, mk_slice)
    hk = f"hold:{k}"
    ver = version(proj, hk)
    real = store.append_event

    def flaky(project, kind, **kw):
        if kind != "question-answered":
            return real(project, kind, **kw)
        if stop == "after_append":
            real(project, kind, **kw)
        raise OSError("stop")

    monkeypatch.setattr(store, "append_event", flaky)
    with pytest.raises(notes.AnswerPending):
        dash_answer(proj, hk, "approve", ver)
    saved = stored(proj, hk)
    monkeypatch.setattr(store, "append_event", real)
    monkeypatch.setattr(store, "now_iso", lambda: "2099-01-01T00:00:00+00:00")
    if recover == "retry":
        assert dash_answer(proj, hk, "approve", ver) == notes.MSG_FINISHED
    else:
        assert notes.read_answers(proj) == [hk]
    (ev,) = answered(home)
    assert (
        ev["decision"] == saved["decision"]["id"] == stored(proj, hk)["decision"]["id"]
    )
    assert ev["at"] == saved["answered_at"] and ev["via"] == "dashboard"
    assert stored(proj, hk)["decision"]["status"] == "done"
    assert stored(proj, hk)["answered_at"] == saved["answered_at"]
    assert slices.load(proj, k)["phase"] == "FRAME"
