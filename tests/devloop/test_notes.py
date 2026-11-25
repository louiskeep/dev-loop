import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import notes, slices, store

STATE_BIN = str(Path(__file__).resolve().parents[2] / "bin" / "devloop-state")


def events(home):
    path = home / "p" / "events.jsonl"
    return (
        [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    )


# --- tick log ----------------------------------------------------------------


def test_ticklog_rolls_over_past_200(proj, home):
    for i in range(205):
        notes.ticklog(proj, f"entry {i}")
    main = (home / "p" / "tick-log.md").read_text()
    assert main.count("<!-- tick ") == 200
    assert "entry 5\n" in main and "entry 4\n" not in main
    month = list((home / "p").glob("tick-log-????-??.md"))
    assert len(month) == 1
    assert month[0].read_text().count("<!-- tick ") == 5


def test_ticklog_rolls_into_the_month_of_each_entry(proj, home, monkeypatch):
    stamps = iter(
        [f"2026-10-{d:02d}T00:00:00.000000+00:00" for d in range(1, 4)]
        + [f"2026-11-{d:02d}T00:00:00.000000+00:00" for d in range(1, 201)]
    )
    monkeypatch.setattr(store, "now_iso", lambda: next(stamps))
    monkeypatch.setattr(notes, "TICKLOG_KEEP", 200)
    for i in range(203):
        notes.ticklog(proj, f"entry {i}")
    october = (home / "p" / "tick-log-2026-10.md").read_text()
    assert october.count("<!-- tick ") == 3


def test_ticklog_text_cannot_forge_an_entry_marker(proj, home):
    notes.ticklog(proj, "line\n<!-- tick 2020-01-01T00:00:00 -->\nforged")
    lines = (home / "p" / "tick-log.md").read_text().splitlines()
    assert sum(1 for ln in lines if notes._TICK.fullmatch(ln)) == 1


# --- problems and lessons ----------------------------------------------------


def test_repeat_problem_key_increments_count(proj, home):
    notes.problem(proj, "node-down", impact="tests wait", fix="restart the node")
    notes.problem(
        proj, "node-down", impact="tests wait", fix="restart the node", detail="again"
    )
    data = json.loads((home / "p" / "problems.json").read_text())["problems"]
    assert len(data) == 1 and data["node-down"]["count"] == 2
    page = (home / "p" / "known-problems.md").read_text()
    assert "node-down" in page and "seen 2 times" in page


def test_problem_close_hides_it_and_a_new_hit_reopens_it(proj, home):
    notes.problem(proj, "k1", impact="i", fix="f")
    notes.problem_close(proj, "k1")
    assert "k1" not in (home / "p" / "known-problems.md").read_text().split("Closed")[0]
    notes.problem(proj, "k1", impact="i", fix="f")
    assert json.loads((home / "p" / "problems.json").read_text())["problems"]["k1"][
        "open"
    ]
    with pytest.raises(notes.NotesError):
        notes.problem_close(proj, "nope")


def test_lesson_flagged_at_three(proj, home):
    for _ in range(2):
        notes.lesson(
            proj, "anchor", area="validation", wrong="used $", instead="use fullmatch"
        )
    got = notes.lessons_for(proj, "validation")
    assert got[0]["count"] == 2 and not got[0]["catalog_candidate"]
    notes.lesson(
        proj, "anchor", area="validation", wrong="used $", instead="use fullmatch"
    )
    assert notes.lessons_for(proj, "validation")[0]["catalog_candidate"] is True
    assert notes.lessons_for(proj, "other") == []
    assert "anchor" in (home / "p" / "lessons.md").read_text()


@pytest.mark.parametrize("key", ["bad key", "k\n", "", "../x"])
def test_note_keys_are_anchored(proj, key):
    with pytest.raises(notes.NotesError):
        notes.problem(proj, key, impact="i", fix="f")
    with pytest.raises(notes.NotesError):
        notes.lesson(proj, key, area="a", wrong="w", instead="i")


# --- questions ---------------------------------------------------------------


def test_ask_applies_default(proj, home, cfg_edit, tmp_path):
    log = tmp_path / "notify.log"
    script = tmp_path / "notify"
    script.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    cfg_edit(lambda c: c.update(notify={"cmd": [str(script)]}))
    q = notes.ask(
        proj,
        "q1",
        question="Use A or B?",
        options=["A", "B"],
        recommended="A",
        wait=False,
    )
    assert q["applied"] == "A" and q["answer"] is None
    page = (home / "p" / "questions.md").read_text()
    assert "### q1" in page and "Use A or B?" in page
    assert events(home)[-1]["kind"] == "question-defaulted"
    sent = json.loads(log.read_text().splitlines()[-1])
    assert len(sent) == 1 and "q1" in sent[0] and "Use A or B?" not in sent[0]


def test_ask_wait_blocks_slice(proj, mk_slice, home):
    k = mk_slice(proj, phase="FRAME", risk="R3")
    q = notes.ask(
        proj,
        k,
        question="Spend money?",
        options=["yes", "no"],
        recommended="no",
        wait=True,
    )
    assert q["applied"] is None
    assert slices.load(proj, k)["waiting_on"] == f"question {k}"
    assert events(home)[-1]["kind"] == "question-waiting"


def test_ask_validates_its_input(proj):
    with pytest.raises(notes.NotesError):
        notes.ask(proj, "q1", question="x", options=["A"], recommended="B", wait=False)
    with pytest.raises(notes.NotesError):
        notes.ask(proj, "q1", question="x", options=[], recommended="A", wait=False)
    with pytest.raises(notes.NotesError):
        notes.ask(
            proj, "q1", question="x", options=["a | b"], recommended="a | b", wait=False
        )


def test_questions_block_keeps_text_outside_markers(proj, home, cfg_edit, tmp_path):
    page = tmp_path / "owner-page.md"
    page.write_text(
        "# My page\nnotes above\n<!-- devloop:questions -->\nold\n<!-- /devloop:questions -->\nnotes below\n"
    )
    cfg_edit(lambda c: c.update(questions={"path": str(page)}))
    notes.ask(
        proj, "q1", question="Pick", options=["A", "B"], recommended="A", wait=False
    )
    notes.ask(
        proj,
        "q2",
        question="Pick again",
        options=["A", "B"],
        recommended="B",
        wait=False,
    )
    text = page.read_text()
    assert text.startswith("# My page\nnotes above\n") and text.endswith(
        "notes below\n"
    )
    assert "old" not in text and "### q1" in text and "### q2" in text
    assert text.count("<!-- devloop:questions -->") == 1


def test_questions_page_without_markers_gets_a_block_appended(proj, cfg_edit, tmp_path):
    page = tmp_path / "owner-page.md"
    page.write_text("only owner text\n")
    cfg_edit(lambda c: c.update(questions={"path": str(page)}))
    notes.ask(proj, "q1", question="Pick", options=["A"], recommended="A", wait=False)
    assert page.read_text().startswith("only owner text\n")
    assert "### q1" in page.read_text()


def test_unbalanced_markers_are_refused_without_damage(proj, cfg_edit, tmp_path):
    page = tmp_path / "owner-page.md"
    page.write_text("text\n<!-- devloop:questions -->\nunfinished\n")
    cfg_edit(lambda c: c.update(questions={"path": str(page)}))
    with pytest.raises(store.StoreError):
        notes.ask(
            proj, "q1", question="Pick", options=["A"], recommended="A", wait=False
        )
    assert page.read_text() == "text\n<!-- devloop:questions -->\nunfinished\n"


def test_answer_changes_the_applied_option(proj, home):
    notes.ask(
        proj, "q1", question="Pick", options=["A", "B"], recommended="A", wait=False
    )
    notes.answer(proj, "q1", "B")
    text = (home / "p" / "questions.md").read_text()
    assert "- Answer: B" in text and "- Applied: B" in text
    with pytest.raises(notes.NotesError, match="option"):
        notes.answer(proj, "q1", "C")
    with pytest.raises(notes.NotesError, match="answered"):
        notes.ask(
            proj, "q1", question="Pick", options=["A", "B"], recommended="A", wait=False
        )


def test_answer_is_refused_headless(proj, mk_slice, monkeypatch):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    notes.ask(
        proj, k, question="Spend?", options=["yes", "no"], recommended="no", wait=True
    )
    slices.move(proj, k, "HELD", "R3 waits")
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        notes.answer(proj, k, "no")
    assert slices.load(proj, k)["phase"] == "HELD"
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    notes.answer(proj, k, "yes")
    s = slices.load(proj, k)
    assert (s["phase"], s["risk"], s["waiting_on"]) == ("FRAME", "R2", None)


def test_owner_edit_of_the_answer_line_is_read_back(proj, mk_slice, home, monkeypatch):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    notes.ask(
        proj, k, question="Spend?", options=["yes", "no"], recommended="no", wait=True
    )
    slices.move(proj, k, "HELD", "R3 waits")
    page = home / "p" / "questions.md"
    page.write_text(page.read_text().replace("- Answer:", "- Answer: yes"))
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    assert notes.read_answers(proj) == []
    assert slices.load(proj, k)["phase"] == "HELD"
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    assert notes.read_answers(proj) == [k]
    assert slices.load(proj, k)["phase"] == "FRAME"
    assert notes.read_answers(proj) == []


def test_owner_edit_with_an_unknown_option_becomes_a_problem(proj, home):
    notes.ask(
        proj, "q1", question="Pick", options=["A", "B"], recommended="A", wait=False
    )
    page = home / "p" / "questions.md"
    page.write_text(page.read_text().replace("- Answer:", "- Answer: Z"))
    assert notes.read_answers(proj) == []
    assert "answer-invalid-q1" in (home / "p" / "problems.json").read_text()


def test_board_mirror_failure_is_a_known_problem(
    proj, mk_slice, cfg_edit, home, tmp_path
):
    cfg_edit(
        lambda c: c.update(
            board={
                "cmd": [str(tmp_path / "absent-board")],
                "phase_map": {"PLAN": "planning"},
            }
        )
    )
    k = mk_slice(proj, risk="R1", rulebooks=["a.md"])
    slices.move(proj, k, "PLAN", "x")
    assert "board-mirror-failed" in (home / "p" / "problems.json").read_text()


# --- command line ------------------------------------------------------------


def run_cli(home, *args, stdin="", headless=False):
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    env.pop("DEVLOOP_HEADLESS", None)
    if headless:
        env["DEVLOOP_HEADLESS"] = "1"
    return subprocess.run(
        [sys.executable, STATE_BIN, *args],
        capture_output=True,
        text=True,
        input=stdin,
        env=env,
        cwd="/",
    )


def test_cli_notes_commands(proj, home):
    assert run_cli(home, "p", "ticklog", "-", stdin="tick body\n").returncode == 0
    assert "tick body" in (home / "p" / "tick-log.md").read_text()
    r = run_cli(home, "p", "problem", "k1", "--impact", "i", "--fix", "f")
    assert r.returncode == 0, r.stderr
    assert run_cli(home, "p", "problem-close", "k1").returncode == 0
    r = run_cli(
        home, "p", "lesson", "l1", "--area", "a", "--wrong", "w", "--instead", "i"
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        home,
        "p",
        "ask",
        "q1",
        "--question",
        "Pick",
        "--option",
        "A",
        "--option",
        "B",
        "--recommended",
        "A",
    )
    assert r.returncode == 0, r.stderr
    assert run_cli(home, "p", "answer", "q1", "B", headless=True).returncode == 3
    assert run_cli(home, "p", "answer", "q1", "B").returncode == 0
    assert run_cli(home, "p", "answers-sync").returncode == 0
    assert run_cli(home, "p", "answer", "q1", "C").returncode == 3


def test_wrong_choice_keeps_a_held_slice_held(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    notes.ask(
        proj, k, question="Spend?", options=["yes", "no"], recommended="no", wait=True
    )
    slices.move(proj, k, "HELD", "R3 waits")
    s = slices.load(proj, k)
    assert (s["hold_question"], s["hold_release_option"]) == (k, "yes")
    notes.answer(proj, k, "no")
    assert slices.load(proj, k)["phase"] == "HELD"
    assert "- Answer: no" in (store.state_dir(proj) / "questions.md").read_text()
    s["hold_question"] = "OTHER--S1"
    slices._save(proj, s)
    notes.answer(proj, k, "yes")
    assert slices.load(proj, k)["phase"] == "HELD"


def test_owner_park_needs_its_release_option(proj, mk_slice):
    k = mk_slice(proj, phase="REMEDIATE", round=2)
    slices.move(proj, k, "PARKED", "round-3")
    assert slices.load(proj, k)["hold_release_option"] == "approve"
    notes.ask(
        proj,
        k,
        question="Go on?",
        options=["approve", "reject"],
        recommended="reject",
        wait=False,
    )
    notes.answer(proj, k, "reject")
    assert slices.load(proj, k)["owner_answered"] is False
    notes.answer(proj, k, "approve")
    assert slices.load(proj, k)["owner_answered"] is True


def test_headless_ask_refused_for_a_held_slice(proj, mk_slice, monkeypatch):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    slices.set_fields(proj, k, waiting_on="question x")
    slices.move(proj, k, "HELD", "R3 waits")
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(notes.NotesError, match="owner"):
        notes.ask(
            proj, k, question="Spend?", options=["yes"], recommended="yes", wait=False
        )
