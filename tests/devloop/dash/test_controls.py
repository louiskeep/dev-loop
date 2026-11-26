"""Loop control: question class and detail, answer rules, one write path, POST actions."""

import fcntl
import html.parser as html_parser
import http.client
import json
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from devloop import notes, slices, state_cli, store, tick, wrap
from devloop.dash import lifecycle, server
from dash_fixture import call

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


# --- Task 10: one write path for pause, resume and tick starts --------------------------


def test_cli_and_dashboard_pause_share_one_path(proj, home, monkeypatch):
    calls = []
    with monkeypatch.context() as m:
        m.setattr(
            store,
            "set_paused",
            lambda project, on, via: calls.append((project, on, via)),
        )
        assert state_cli.main(["p", "pause"]) == 0
        assert state_cli.main(["p", "resume"]) == 0
    assert calls == [("p", True, "cli"), ("p", False, "cli")]
    store.set_paused(proj, True, "dashboard")
    assert (home / "p" / "PAUSE").exists()
    store.set_paused(proj, False, "dashboard")
    assert not (home / "p" / "PAUSE").exists()
    kinds = [
        (e["kind"], e["via"]) for e in events(home) if e["kind"].startswith("loop-")
    ]
    assert kinds == [("loop-paused", "dashboard"), ("loop-resumed", "dashboard")]


def fake_proc(tmp_path, lines):
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "locks").write_text("".join(f"{ln}\n" for ln in lines))
    return proc


def lock_line(path, kind="FLOCK"):
    st = os.stat(path)
    ident = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    return f"1: {kind}  ADVISORY  WRITE 4242 {ident} 0 EOF"


def test_tick_running_is_passive(proj, home, tmp_path):
    lock = home / "p" / "tick.lock"
    assert tick.running(proj) is False and not lock.exists()
    lock.write_text("")
    before = lock.stat()
    assert tick.running(proj, proc=fake_proc(tmp_path, [])) is False
    held = [lock_line(lock)]
    assert tick.running(proj, proc=fake_proc(tmp_path, held)) is True
    # A POSIX lock, or a lock on another file, is not the tick's flock.
    other = [lock_line(lock, "POSIX"), lock_line(home / "p" / "project.json")]
    assert tick.running(proj, proc=fake_proc(tmp_path, other)) is False
    assert lock.stat().st_mtime_ns == before.st_mtime_ns
    # Against the real kernel table: the tick's own flock is seen, and probing takes none.
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert tick.running(proj) is True
    finally:
        os.close(fd)
    assert tick.running(proj) is False
    fd = os.open(lock, os.O_RDWR)
    try:
        for _ in range(3):  # a probe never holds the lock, so a tick still starts
            tick.running(proj)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def tick_launcher(tmp_path, monkeypatch, body):
    bins = tmp_path / "bins"
    bins.mkdir(exist_ok=True)
    script = bins / "devloop-tick"
    script.write_text(f"#!{sys.executable}\n{body}")
    script.chmod(0o755)
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(bins))


def wait_for_file(path, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip().endswith("done"):
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError("the launcher never ran")


def test_spawn_detached_leaves_no_child_fds(proj, home, tmp_path, monkeypatch):
    out = tmp_path / "child.txt"
    tick_launcher(
        tmp_path,
        monkeypatch,
        "import os, sys\n"
        "fds = ','.join(sorted(os.listdir('/proc/self/fd')))\n"
        f"open({str(out)!r}, 'w').write(f\"{{fds}} {{os.getsid(0) == os.getpid()}} "
        "{os.environ.get('DEVLOOP_HEADLESS')} {sys.argv[1]} done\")\n",
    )
    r, w = os.pipe()
    os.set_inheritable(r, True)
    os.set_inheritable(w, True)
    try:
        started = []
        tick.spawn_detached(proj, "dashboard", on_started=lambda: started.append(1))
        fds, leader, headless, project, _ = wait_for_file(out).split()
    finally:
        os.close(r)
        os.close(w)
    assert started == [1]
    assert str(r) not in fds.split(",") and str(w) not in fds.split(",")
    assert (leader, headless, project) == ("True", "1", "p")
    assert (home / "p" / "events.jsonl").read_text().count("tick-requested") == 1
    assert events(home)[-1]["via"] == "dashboard"


def test_spawn_detached_raises_when_it_cannot_launch(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(tmp_path / "nowhere"))
    started = []
    with pytest.raises(OSError):
        tick.spawn_detached(proj, "dashboard", on_started=lambda: started.append(1))
    assert started == []


def test_wrapper_wakes_tick_through_spawn_detached(
    proj, tmp_path, monkeypatch, fakebin
):
    calls = []
    monkeypatch.setattr(tick, "spawn_detached", lambda *a, **kw: calls.append(a))
    rd = store.run_dir(proj, "r1")
    rd.mkdir(parents=True)
    req = {
        "id": "r1",
        "kind": "duty",
        "row": "R",
        "slice": "S",
        "key": "R--S",
        "reader": "exit",
        "argv": ["true"],
        "cwd": str(tmp_path),
        "deadline_s": 60,
        "idle_s": 60,
    }
    store.write_json(rd / "request.json", req)
    (rd / "brief.md").write_text("b")
    store.out_dir(rd).mkdir()
    wrap.finish(proj, rd, req, 0, "exit")
    assert calls == [(proj, "wrapper")]
    # A launch failure never breaks the wrapper: the next cron tick collects the run.
    monkeypatch.setattr(
        tick, "spawn_detached", lambda *a, **kw: (_ for _ in ()).throw(OSError("x"))
    )
    wrap.finish(proj, rd, req, 0, "exit")


def test_server_env_is_not_headless(proj, home, monkeypatch):
    seen = {}

    class Done(Exception):
        pass

    def popen(argv, **kw):
        seen.update(kw)
        raise Done

    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    monkeypatch.setattr(lifecycle.subprocess, "Popen", popen)
    args = type("A", (), {"port": None, "host": None})()
    with pytest.raises(Done):
        lifecycle._detach(proj, args)
    assert "DEVLOOP_HEADLESS" not in seen["env"]


# --- Task 11: POST actions ---------------------------------------------------------------

ANSWER_KEY = "q-owner"  # the dash fixture's open product question: options a, b


def conn_for(srv):
    return http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)


def post(
    srv,
    path,
    fields=None,
    *,
    raw=None,
    token="",
    origin="",
    host=None,
    headers=None,
    length=None,
    drop=(),
):
    """POST with a good token and origin unless told otherwise. `drop` removes headers."""
    port = srv.server_address[1]
    body = raw if raw is not None else urllib.parse.urlencode(fields or {}).encode()
    if isinstance(body, str):
        body = body.encode()
    if fields is not None and token != "":
        fields = {**fields, "t": token}
    elif fields is not None and "t" not in fields:
        fields = {**fields, "t": srv.token}
    if raw is None and fields is not None:
        body = urllib.parse.urlencode(fields).encode()
    h = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin or f"http://127.0.0.1:{port}",
        "Content-Length": str(len(body) if length is None else length),
    }
    h.update(headers or {})
    for name in drop:
        h.pop(name, None)
    conn = conn_for(srv)
    try:
        conn.putrequest("POST", path, skip_host=host is not None)
        if host is not None:
            conn.putheader("Host", host)
        for k, v in h.items():
            conn.putheader(k, v)
        conn.endheaders(body)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def answer_fields(proj, key=ANSWER_KEY, choice="a"):
    return {"key": key, "version": version(proj, key), "choice": choice}


class Calls:
    """Replace every state function a POST may reach and count the calls."""

    def __init__(self, monkeypatch):
        self.calls = []
        for mod, name in (
            (notes, "answer"),
            (store, "set_paused"),
            (tick, "spawn_detached"),
        ):
            monkeypatch.setattr(
                mod, name, lambda *a, _n=name, **kw: self.calls.append(_n) or "ok"
            )


def valid_fields(proj, path):
    return {
        "/act/answer": lambda: answer_fields(proj),
        "/act/pause": dict,
        "/act/resume": dict,
        "/act/tick": dict,
    }[path]()


def post_routes():
    return [path for (method, path) in server.ROUTES if method == "POST"]


def test_every_post_route_refuses_bad_token_or_origin(proj, dash, srv, monkeypatch):
    paths = post_routes()
    assert sorted(paths) == ["/act/answer", "/act/pause", "/act/resume", "/act/tick"]
    calls = Calls(monkeypatch)
    port = srv.server_address[1]
    for path in paths:
        f = valid_fields(proj, path)
        s, _, b = post(srv, path, {**f, "t": "wrong"})
        assert (s, b) == (403, b"This page is out of date. Reload."), path
        s, _, b = post(srv, path, {**f, "t": ""})
        assert s == 403, path
        for origin in (
            f"http://evil.example:{port}",
            f"http://127.0.0.1:{port + 1}",
            f"https://127.0.0.1:{port}",
            "null",
        ):
            s, _, b = post(srv, path, f, origin=origin)
            assert (s, b) == (403, b"Refused: not sent from this page."), (path, origin)
        s, _, b = post(srv, path, f, drop=("Origin",))
        assert s == 403 and b"not sent from this page" in b
        # A Referer from this page stands in for a missing Origin; a foreign one does not.
        ok = post(
            srv,
            path,
            f,
            drop=("Origin",),
            headers={"Referer": f"http://127.0.0.1:{port}/x"},
        )
        assert ok[0] != 403, path
        assert (
            post(
                srv,
                path,
                f,
                drop=("Origin",),
                headers={"Referer": "http://evil.example/"},
            )[0]
            == 403
        )
    assert calls.calls.count("answer") == 1  # only the Referer case reached a handler


def test_post_body_contract(proj, dash, srv, monkeypatch, home):
    calls = Calls(monkeypatch)
    good = answer_fields(proj)
    enc = urllib.parse.urlencode
    t = srv.token
    cases = {
        "negative length": dict(fields=good, length=-5),
        "non-numeric length": dict(fields=good, headers={"Content-Length": "abc"}),
        "missing length": dict(fields=good, drop=("Content-Length",)),
        "too long": dict(raw=b"x=" + b"a" * 9000),
        "bare percent": dict(raw=enc({**good, "t": t}) + "&z=%"),
        "truncated percent": dict(raw=enc({**good, "t": t}) + "&z=%4"),
        "bad percent": dict(raw=enc({**good, "t": t}) + "&z=%zz"),
        "invalid raw utf8": dict(raw=b"t=" + t.encode() + b"&key=\xff"),
        "invalid escaped utf8": dict(
            raw=enc({**good, "t": t, "choice": "x"}).replace("choice=x", "other=caf%E9")
        ),
        "duplicate choice": dict(raw=enc({**good, "t": t}) + "&choice=b"),
        "both modes": dict(raw=enc({**good, "t": t}) + "&other="),
        "neither mode": dict(fields={k: v for k, v in good.items() if k != "choice"}),
        "unknown field": dict(fields={**good, "extra": "1"}),
        "missing version": dict(
            fields={k: v for k, v in good.items() if k != "version"}
        ),
        "no equals": dict(raw="garbage"),
    }
    for name, kw in cases.items():
        s, _, b = post(srv, "/act/answer", **kw)
        assert (s, b) == (400, b"Bad request."), name
    assert calls.calls == []
    # Pause takes only the token.
    assert post(srv, "/act/pause", {"extra": "1"})[0] == 400
    assert calls.calls == []


def test_post_body_stores_utf8_text_once(proj, dash, srv):
    f = {"key": ANSWER_KEY, "version": version(proj, ANSWER_KEY), "other": "café"}
    body = urllib.parse.urlencode({**f, "t": srv.token}, encoding="utf-8").encode()
    assert b"caf%C3%A9" in body
    s, _, _ = post(srv, "/act/answer", raw=body)
    assert s == 303
    assert stored(proj, ANSWER_KEY)["answer"] == "other: café"
    assert len(answered(pathlib.Path(os.environ["DEVLOOP_HOME"]))) == 1


def test_double_post_records_one_answer(proj, dash, srv, home):
    f = answer_fields(proj)
    first = post(srv, "/act/answer", f, headers={"Accept": "application/json"})
    assert first[0] == 200 and json.loads(first[2])["ok"] is True
    second = post(srv, "/act/answer", f, headers={"Accept": "application/json"})
    assert second[0] == 409 and json.loads(second[2]) == {
        "ok": False,
        "message": "Already answered.",
    }
    assert len(answered(home)) == 1
    assert stored(proj, ANSWER_KEY)["applied"] == "a"


def test_stale_post_with_removed_option_returns_changed(proj, dash, srv, home):
    f = answer_fields(proj, choice="b")
    ask(proj, ANSWER_KEY, options=["a", "c"], recommended="a", question="Pick a thing")
    s, _, b = post(srv, "/act/answer", f)
    assert (s, b) == (409, b"This question changed. Reload to see the new version.")
    assert stored(proj, ANSWER_KEY)["answer"] is None and answered(home) == []


def test_unknown_question_key_is_refused(proj, dash, srv):
    s, _, b = post(
        srv, "/act/answer", {"key": "nope", "version": "0" * 16, "choice": "a"}
    )
    assert (s, b) == (409, b"This question is gone. Reload.")
    s, _, b = post(
        srv, "/act/answer", {"key": "../x y", "version": "0" * 16, "choice": "a"}
    )
    assert s == 400
    f = answer_fields(proj, choice="not an option")
    assert post(srv, "/act/answer", f)[0] == 400


def test_stale_token_after_restart_is_refused(proj, dash, srv):
    old = srv.token
    page = call(srv, "/")
    assert page[1]["Cache-Control"] == "no-store" and "ETag" not in page[1]
    assert "ETag" not in call(srv, "/fragment")[1]
    assert call(srv, "/api/state")[1]["ETag"]
    other = server.make_server(proj, "127.0.0.1", 0, proc="/nonexistent-proc")
    t = threading.Thread(target=other.serve_forever, daemon=True)
    t.start()
    try:
        assert other.token != old
        s, _, b = post(other, "/act/pause", {"t": old})
        assert (s, b) == (403, b"This page is out of date. Reload.")
        assert post(other, "/act/pause", {})[0] == 303
    finally:
        other.shutdown()
        other.server_close()
        store.set_paused(proj, False, "cli")


def test_pause_and_resume_post_and_events(proj, dash, srv, home):
    assert post(srv, "/act/pause", {})[0] == 303
    assert (home / "p" / "PAUSE").exists()
    assert post(srv, "/act/resume", {})[0] == 303
    assert not (home / "p" / "PAUSE").exists()
    got = [(e["kind"], e["via"]) for e in events(home) if e["kind"].startswith("loop-")]
    assert got == [("loop-paused", "dashboard"), ("loop-resumed", "dashboard")]


def test_each_write_appends_its_event(proj, dash, srv, home, tmp_path, monkeypatch):
    tick_launcher(tmp_path, monkeypatch, "pass\n")
    post(srv, "/act/answer", answer_fields(proj))
    post(srv, "/act/tick", {})
    wait_for_events = [
        e for e in events(home) if e["kind"] in ("question-answered", "tick-requested")
    ]
    assert [(e["kind"], e["via"]) for e in wait_for_events] == [
        ("question-answered", "dashboard"),
        ("tick-requested", "dashboard"),
    ]


def counting_launcher(tmp_path, monkeypatch):
    mark = tmp_path / "spawned.txt"
    tick_launcher(tmp_path, monkeypatch, f"open({str(mark)!r}, 'a').write('x')\n")
    return mark


def spawned(mark):
    time.sleep(0.5)
    return len(mark.read_text()) if mark.exists() else 0


def test_tick_debounce(proj, dash, srv, tmp_path, monkeypatch):
    mark = counting_launcher(tmp_path, monkeypatch)
    first = post(srv, "/act/tick", {})
    assert (first[0], first[2]) == (200, b"Tick started.") or first[0] == 303
    second = post(srv, "/act/tick", {})
    assert (second[0], second[2]) == (409, b"A tick was just started.")
    assert spawned(mark) == 1
    srv.last_tick -= 11  # the window has passed
    assert post(srv, "/act/tick", {})[0] in (200, 303)
    assert spawned(mark) == 2


def test_tick_running_refuses(proj, dash, srv, tmp_path, monkeypatch):
    mark = counting_launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(tick, "running", lambda *a, **kw: True)
    s, _, b = post(srv, "/act/tick", {})
    assert (s, b) == (409, b"A tick is running now.") and spawned(mark) == 0


def test_spawn_failure_reports_and_allows_retry(proj, dash, srv, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(tmp_path / "nowhere"))
    s, _, b = post(srv, "/act/tick", {})
    assert (s, b) == (500, b"Could not start a tick.") and srv.last_tick is None
    mark = counting_launcher(tmp_path, monkeypatch)
    assert post(srv, "/act/tick", {})[0] in (200, 303)
    assert spawned(mark) == 1


def test_tick_event_failure_keeps_debounce(proj, dash, srv, tmp_path, monkeypatch):
    mark = counting_launcher(tmp_path, monkeypatch)
    real = store.append_event

    def flaky(project, kind, **kw):
        if kind == "tick-requested":
            raise OSError("disk")
        return real(project, kind, **kw)

    monkeypatch.setattr(store, "append_event", flaky)
    s, _, b = post(srv, "/act/tick", {}, headers={"Accept": "application/json"})
    assert s == 200 and "could not be recorded" in json.loads(b)["message"]
    s, _, b = post(srv, "/act/tick", {})
    assert (s, b) == (409, b"A tick was just started.")
    assert spawned(mark) == 1


def test_overlapping_tick_posts_spawn_once(proj, dash, srv, tmp_path, monkeypatch):
    mark = counting_launcher(tmp_path, monkeypatch)
    real = tick.spawn_detached

    def slow(*a, **kw):
        time.sleep(0.4)  # a second post arrives while the first is still starting
        return real(*a, **kw)

    monkeypatch.setattr(tick, "spawn_detached", slow)
    results = []
    gate = threading.Barrier(2)

    def go():
        gate.wait()
        results.append(post(srv, "/act/tick", {})[0])

    threads = [threading.Thread(target=go) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(results) in ([303, 409], [200, 409])
    assert spawned(mark) == 1


def test_get_routes_write_nothing(proj, dash, srv, home, monkeypatch):
    def listing():
        return sorted(
            (str(p.relative_to(home)), p.stat().st_mtime_ns) for p in home.rglob("*")
        )

    gets = [path for (method, path) in server.ROUTES if method == "GET"]
    assert {"/", "/fragment", "/api/state"} <= set(gets)
    for lock_present in (False, True):
        lock = home / "p" / "tick.lock"
        if lock_present:
            lock.write_text("")
        else:
            lock.unlink(missing_ok=True)
        before = listing()
        srv.cache._value = None
        for path in gets:
            for method in ("GET", "HEAD"):
                assert call(srv, path, method)[0] == 200, path
        assert listing() == before


def test_post_success_json_and_redirect(proj, dash, srv):
    s, h, b = post(srv, "/act/pause", {})
    assert s == 303 and h["Location"] == "/" and b == b""
    s, h, b = post(srv, "/act/resume", {}, headers={"Accept": "application/json"})
    assert s == 200 and h["Content-Type"].startswith("application/json")
    assert json.loads(b) == {"ok": True, "message": "Loop resumed."}
    for name in ("Content-Security-Policy", "X-Content-Type-Options", "Cache-Control"):
        assert name in h


def test_post_log_has_no_values(proj, dash, srv):
    f = answer_fields(proj, choice="other: hunter2 secret")
    post(srv, "/act/answer", f)
    post(srv, "/act/pause", {})
    post(srv, "/act/resume", {"t": "wrong-token-value"})
    log = srv.log_text.getvalue()
    assert "POST /act/answer" in log and "answer ok" in log and "pause ok" in log
    for needle in ("hunter2", "wrong-token-value", ANSWER_KEY, f["version"], srv.token):
        assert needle not in log


def test_a_failing_handler_is_a_fixed_500(proj, dash, srv, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("secret detail /etc/shadow")

    monkeypatch.setattr(store, "set_paused", boom)
    s, _, b = post(srv, "/act/pause", {})
    assert (s, b) == (500, b"Something went wrong. Reload to see what was saved.")
    log = srv.log_text.getvalue()
    assert "RuntimeError" in log and "secret" not in log and "/etc" not in log


def test_post_to_a_get_path_and_unknown_paths(srv):
    assert post(srv, "/api/state", {})[0] == 405
    assert post(srv, "/nope", {})[0] == 404
    assert call(srv, "/act/pause")[0] == 405  # GET of a POST-only path


def test_short_body_at_eof_is_refused(proj, dash, srv, monkeypatch):
    import socket

    calls = Calls(monkeypatch)
    port = srv.server_address[1]
    body = urllib.parse.urlencode({"t": srv.token}).encode()
    request = (
        f"POST /act/pause HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Origin: http://127.0.0.1:{port}\r\nContent-Length: 500\r\n\r\n"
    ).encode() + body
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        reply = b""
        while chunk := s.recv(4096):
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 400") and reply.endswith(b"Bad request.")
    assert calls.calls == []


# --- Task 12: decisions drawer, decided list, controls ------------------------------------


class _Forms(html_parser.HTMLParser):
    """The forms inside one source div, with their hidden fields and button text."""

    def __init__(self, source_id):
        super().__init__()
        self.source_id, self.depth, self.forms, self.other = source_id, 0, [], {}
        self.copy = None
        self._form = None
        self._btn = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and (a.get("id") == self.source_id or self.depth):
            self.depth += 1
        if not self.depth:
            return
        if tag == "form":
            self._form = {"action": a["action"], "fields": {}, "label": "", "attrs": a}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None and a.get("type") == "hidden":
            self._form["fields"][a["name"]] = a["value"]
        elif tag == "textarea" and self._form is not None:
            self._form["fields"]["other"] = None
            self._form["textarea_id"] = a.get("id")
        elif tag == "button" and self._form is not None:
            self._btn = True
        elif tag == "button" and "data-copy" in a:
            self.copy = a["data-copy"]

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "button":
            self._btn = False
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self._btn and self._form is not None:
            self._form["label"] += data


def source_forms(page, key):
    p = _Forms(f"qs-{key}")
    p.feed(page)
    return p


def render_page(proj, token="tok", path="/x/questions.md"):
    from devloop.dash import render, view

    v = view.build_view(proj, proc="/nonexistent-proc")
    return v, render.render_page(v, token=token, copy_path=path)


def submit(srv, form, **override):
    fields = {k: v for k, v in form["fields"].items() if v is not None}
    fields.update(override)
    return post(srv, form["action"], fields)


def test_decisions_drawer_has_actions(proj, dash):
    v, page = render_page(proj, "tok123", "/x/questions.md")
    src = source_forms(page, ANSWER_KEY)
    labels = [f["label"] for f in src.forms]
    assert labels[:3] == ["Go with recommended: a", "b", "Save"]
    for f in src.forms:
        assert f["action"] == "/act/answer"
        assert f["fields"]["t"] == "tok123" and f["fields"]["key"] == ANSWER_KEY
        assert f["fields"]["version"] == version(proj, ANSWER_KEY)
        assert f["attrs"]["data-key"] == ANSWER_KEY
        assert f["attrs"]["data-version"] == version(proj, ANSWER_KEY)
    assert src.forms[2]["textarea_id"] == f"ta-{ANSWER_KEY}-{version(proj, ANSWER_KEY)}"
    assert src.copy == (
        f"Help me decide dev loop question {ANSWER_KEY} in project p. The question, "
        "its options and the background are in /x/questions.md. Read it, explain the "
        "trade-off, and recommend an option."
    )
    assert "Decide" in page and f'data-open-key="{ANSWER_KEY}"' in page


def test_hold_drawer_offers_only_two_choices(proj, dash, mk_slice):
    notes.ensure_hold_question(proj, dash["held"])
    _, page = render_page(proj)
    src = source_forms(page, f"hold:{dash['held']}")
    assert [f["label"] for f in src.forms] == [
        "Go with recommended: keep-held",
        "approve",
    ]
    assert all(f["fields"].get("other", 1) == 1 for f in src.forms)  # no free text
    assert (
        "textarea"
        not in page[page.index(f'id="qs-hold:{dash["held"]}"') :].split('id="qs-')[1]
    )


def test_hold_answer_uses_hold_key(proj, dash, srv, mk_slice):
    k = hold_slice(proj, mk_slice)
    srv.cache._value = None
    page = call(srv, "/")[2].decode()
    src = source_forms(page, f"hold:{k}")
    approve = next(f for f in src.forms if f["label"] == "approve")
    assert approve["fields"]["key"] == f"hold:{k}"
    assert submit(srv, approve)[0] == 303
    assert slices.load(proj, k)["phase"] != "HELD"


def test_option_with_changed_text_is_not_offered(proj, dash):
    secret = "ghp_DEMOTOKEN1234567"
    ask(proj, "q-secret", options=["fine", secret], recommended="fine")
    _, page = render_page(proj)
    src = source_forms(page, "q-secret")
    assert [f["label"] for f in src.forms] == ["Go with recommended: fine", "Save"]
    assert secret not in page


def test_decided_for_you_list(proj, dash):
    ask(
        proj,
        "t1",
        klass="tech",
        question="Use tool X?",
        options=["x", "y"],
        recommended="x",
    )
    v, page = render_page(proj)
    assert [d["key"] for d in v["decided"]] == ["t1"]
    assert v["decided"][0]["recommended"] == "x" and v["decided"][0]["applied"] == "x"
    assert "Decided for you" in page and "Tech lead chose x" in page
    assert 'data-open-kind="q" data-open-key="t1"' in page
    assert source_forms(page, "t1").forms  # every entry has a source


def test_override_form_round_trips(proj, dash, srv):
    ask(proj, "t1", klass="tech", options=["A", "B"], recommended="A")
    srv.cache._value = None
    page = call(srv, "/")[2].decode()
    to_b = next(f for f in source_forms(page, "t1").forms if f["label"] == "B")
    assert submit(srv, to_b)[0] == 303
    assert stored(proj, "t1")["applied"] == "B"
    srv.cache._value = None
    page = call(srv, "/")[2].decode()
    src = source_forms(page, "t1")
    assert "You chose: B" in page
    rec = next(f for f in src.forms if f["label"].startswith("Go with recommended"))
    assert rec["label"] == "Go with recommended: A"
    assert submit(srv, rec)[0] == 303
    q = stored(proj, "t1")
    assert (q["answer"], q["applied"]) == ("A", "A")


def test_controls_render_no_loop_buttons(proj, dash, monkeypatch):
    # Owner 2026-11-26: no Pause, Resume or tick button on the page, in any loop state.
    def assert_none(page):
        for action in ("/act/pause", "/act/resume", "/act/tick"):
            assert f'action="{action}"' not in page
        assert "Run a tick now" not in page and "A tick is running" not in page

    assert_none(render_page(proj, "tok")[1])
    store.set_paused(proj, True, "cli")
    assert_none(render_page(proj, "tok")[1])
    monkeypatch.setattr(tick, "running", lambda *a, **kw: True)
    assert_none(render_page(proj, "tok")[1])


def test_hold_form_key_survives_a_hex_looking_slice_name(proj, dash, srv):
    s = slices.new_slice(proj, "ROWdeadbeef1", "S1", repo="main", risk="R3")
    s["rulebooks"] = ["a.md"]
    slices._save(proj, s)
    k = s["key"]
    notes.ask(
        proj, k, question="Spend?", options=["y", "n"], recommended="n", wait=True
    )
    slices.move(proj, k, "HELD", "R3 waits")
    srv.cache._value = None
    page = call(srv, "/")[2].decode()
    approve = next(
        f for f in source_forms(page, f"hold:{k}").forms if f["label"] == "approve"
    )
    assert approve["fields"]["key"] == f"hold:{k}"
    assert submit(srv, approve)[0] == 303
    assert slices.load(proj, k)["phase"] == "FRAME"
