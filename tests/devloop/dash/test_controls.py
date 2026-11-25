"""Loop control: question class and detail, answer rules, one write path, POST actions."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import notes, store

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
