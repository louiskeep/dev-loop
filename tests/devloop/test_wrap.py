import shutil
import time

import pytest

from devloop import runs, store, wrap


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, fakebin, home, proj):
    monkeypatch.setenv("DEVLOOP_POLL_SECS", "0.1")
    bins = tmp_path / "bins"
    bins.mkdir()
    shutil.copy(fakebin / "devloop-tick", bins / "devloop-tick")
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(bins))
    monkeypatch.setenv("FAKE_TICK_MARK", str(tmp_path / "tick-mark"))


def mk(proj, tmp_path, argv, *, kind="builder", reader="claude", **over):
    rd = store.run_dir(proj, "r1")
    rd.mkdir(parents=True)
    req = {
        "id": "r1",
        "kind": kind,
        "row": "R",
        "slice": "S",
        "key": "R--S",
        "reader": reader,
        "argv": argv,
        "cwd": str(tmp_path),
        "deadline_s": 60,
        "idle_s": 60,
        "out": str(store.out_dir(rd) / "codex.out"),
        "last_msg": str(store.out_dir(rd) / "final.md"),
        "head": "abc1234",
        "infra_codes": [],
        "purpose": "code",
    }
    req.update(over)
    store.write_json(rd / "request.json", req)
    (rd / "brief.md").write_text("the brief")
    return rd


def result(rd):
    return store.read_header(store.out_dir(rd) / "result.md")


def test_wrapper_writes_exit_on_kill(proj, tmp_path):
    rd = mk(proj, tmp_path, ["sleep", "60"], deadline_s=1)
    start = time.time()
    wrap.run(proj, "r1")
    assert time.time() - start < 10
    ex = store.read_json(rd / "exit")
    assert ex["reason"] == "deadline" and ex["code"] != 0
    assert runs.parse_result(rd, "builder").status == "FAILED"
    req = runs.read_request(proj, "r1")
    assert req["payload_pgid"] and not runs.payload_alive(req)


def test_idle_payload_killed_as_hung(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_IDLE_SECS", "1")
    rd = mk(proj, tmp_path, ["sleep", "60"])
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "hung"


def test_codex_out_growth_is_not_idle(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_IDLE_SECS", "1")
    rd = store.run_dir(proj, "r1")
    script = f"for i in 1 2 3 4 5 6; do echo x >> {rd}/out/codex.out; sleep 0.4; done"
    rd = mk(proj, tmp_path, ["sh", "-c", script], kind="duty", reader="exit")
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "exit"
    assert result(rd)["status"] == "DONE"


def codex(
    proj, tmp_path, monkeypatch, last=None, no_last=False, exit_=0, kind="codex-review"
):
    if last is not None:
        monkeypatch.setenv("FAKE_CODEX_LAST", last)
    if no_last:
        monkeypatch.setenv("FAKE_NO_LAST", "1")
    monkeypatch.setenv("FAKE_EXIT", str(exit_))
    rd = store.run_dir(proj, "r1")
    argv = {
        "codex-review": [
            "codex-review",
            str(tmp_path),
            "brief",
            str(store.out_dir(rd) / "codex.out"),
        ],
        "codex-fix": ["codex-fix", "r1", "brief", "x"],
    }[kind]
    rd = mk(
        proj,
        tmp_path,
        argv,
        kind=kind,
        reader="verdict" if kind == "codex-review" else "selfverdict",
    )
    (tmp_path / "x").write_text("plan")
    wrap.run(proj, "r1")
    return rd


def test_codex_zero_exit_without_verdict_is_failed(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, no_last=True)
    assert result(rd)["status"] == "FAILED"
    assert runs.parse_result(rd, "codex-review").status == "FAILED"


def test_codex_final_message_gives_the_verdict(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, last="notes\nVERDICT: GO")
    assert (result(rd)["status"], result(rd)["verdict"]) == ("DONE", "GO")


@pytest.mark.parametrize(
    "line,verdict",
    [
        ("`VERDICT: GO`", "GO"),
        ("**VERDICT: GO**", "GO"),
        ("_VERDICT: NO-GO_", "NO-GO"),
        ("VERDICT: GO.", "GO"),
        ("**VERDICT: GO-WITH-FIXES**.", "GO-WITH-FIXES"),
        ("  `VERDICT: GO-WITH-FIXES`.  ", "GO-WITH-FIXES"),
    ],
)
def test_markdown_around_the_verdict_line_is_stripped(
    proj, tmp_path, monkeypatch, line, verdict
):
    rd = codex(proj, tmp_path, monkeypatch, last=f"notes\n{line}")
    assert (result(rd)["status"], result(rd)["verdict"]) == ("DONE", verdict)


@pytest.mark.parametrize(
    "line", ["VERDICT: GO..", "VERDICT: GOOD", "the VERDICT: GO", "VERDICT: GO, mostly"]
)
def test_stripping_never_turns_a_non_verdict_into_a_verdict(
    proj, tmp_path, monkeypatch, line
):
    rd = codex(proj, tmp_path, monkeypatch, last=line)
    assert result(rd)["status"] == "FAILED"


def test_a_clean_exit_with_no_verdict_has_its_own_reason(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, no_last=True)
    assert store.read_json(rd / "exit")["reason"] == "no-verdict"


def test_a_failed_exit_is_not_no_verdict(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, last="VERDICT: GO", exit_=3)
    assert store.read_json(rd / "exit")["reason"] == "exit"


def test_go_with_fixes_is_not_go(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, last="VERDICT: GO-WITH-FIXES")
    assert result(rd)["verdict"] == "GO-WITH-FIXES"


def test_nonzero_exit_voids_the_verdict(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, last="VERDICT: GO", exit_=3)
    assert result(rd)["status"] == "FAILED"


def test_codex_usage_cap_text_sets_cap_until(proj, tmp_path, monkeypatch, home):
    monkeypatch.setenv(
        "FAKE_OUT_TEXT",
        "ERROR: you have hit your usage limit. Try again at 2999-01-01T00:00:00Z",
    )
    rd = codex(proj, tmp_path, monkeypatch, no_last=True, exit_=1)
    assert store.read_json(rd / "exit")["reason"] == "capped"
    assert (home / "p" / "cap-until").read_text().startswith("2999-01-01")
    assert result(rd)["status"] == "FAILED"


def test_codex_failure_without_a_cap_message_is_not_a_cap(
    proj, tmp_path, monkeypatch, home
):
    monkeypatch.setenv("FAKE_OUT_TEXT", "the sandbox failed to start")
    rd = codex(proj, tmp_path, monkeypatch, no_last=True, exit_=1)
    assert store.read_json(rd / "exit")["reason"] == "exit"
    assert not (home / "p" / "cap-until").exists()


def test_codex_review_text_about_limits_with_exit_zero_is_not_a_cap(
    proj, tmp_path, monkeypatch, home
):
    monkeypatch.setenv("FAKE_OUT_TEXT", "the rate limit handling resets after a minute")
    rd = codex(proj, tmp_path, monkeypatch, last="VERDICT: GO")
    assert store.read_json(rd / "exit")["reason"] == "exit"
    assert not (home / "p" / "cap-until").exists()


def test_repair_reads_the_self_verdict(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FIX_CACHE", str(tmp_path / "cache"))
    rd = codex(proj, tmp_path, monkeypatch, kind="codex-fix")
    assert (result(rd)["status"], result(rd)["verdict"]) == ("DONE", "GO")
    rd2 = store.run_dir(proj, "r1")
    assert rd2 == rd and (tmp_path / "cache" / "r1" / "x").is_file()


def test_codex_final_message_env_is_set_for_codex_kinds(proj, tmp_path, monkeypatch):
    rd = codex(proj, tmp_path, monkeypatch, last="VERDICT: NO-GO")
    assert (store.out_dir(rd) / "final.md").read_text().strip() == "VERDICT: NO-GO"


def claude_argv(*extra):
    return ["claude", "-p", *extra]


def test_dennis_verdict_read_from_stream_json_result(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "findings...\nVERDICT: NO-GO")
    rd = mk(proj, tmp_path, claude_argv(), kind="dennis", reader="claude-verdict")
    wrap.run(proj, "r1")
    assert (result(rd)["status"], result(rd)["verdict"]) == ("DONE", "NO-GO")


def test_transcript_go_cannot_supply_verdict(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_STREAM_EXTRA", "VERDICT: GO")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "I ran out of time.")
    rd = mk(proj, tmp_path, claude_argv(), kind="dennis", reader="claude-verdict")
    wrap.run(proj, "r1")
    assert result(rd)["status"] == "FAILED"


def test_verdict_must_be_the_last_line_of_the_final_message(
    proj, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "VERDICT: GO\nbut I did not finish")
    rd = mk(proj, tmp_path, claude_argv(), kind="dennis", reader="claude-verdict")
    wrap.run(proj, "r1")
    assert result(rd)["status"] == "FAILED"


def test_error_result_cannot_supply_a_verdict(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "VERDICT: GO")
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "1")
    rd = mk(proj, tmp_path, claude_argv(), kind="dennis", reader="claude-verdict")
    wrap.run(proj, "r1")
    assert result(rd)["status"] == "FAILED"


def test_worker_cap_sets_cap_until_not_failure(proj, tmp_path, monkeypatch, home):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "usage-cap")
    rd = mk(proj, tmp_path, claude_argv())
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "capped"
    assert (home / "p" / "cap-until").is_file()


def test_timeout_exit_is_not_a_cap(proj, tmp_path, monkeypatch, home):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "usage-cap")
    rd = mk(proj, tmp_path, ["sh", "-c", "echo usage limit; exit 124"])
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "exit"
    assert not (home / "p" / "cap-until").exists()


@pytest.mark.parametrize(
    "code,reason,status,verdict",
    [
        (0, "exit", "DONE", "GO"),
        (1, "exit", "DONE", "NO-GO"),
        (255, "infra", "FAILED", ""),
    ],
)
def test_test_exit_codes_mapped(
    proj, tmp_path, monkeypatch, code, reason, status, verdict
):
    monkeypatch.setenv("FAKE_VM_EXIT", str(code))
    monkeypatch.setenv("FAKE_VM_FAILED", "tests/a.py::t1 tests/b.py::t2")
    rd = mk(
        proj,
        tmp_path,
        ["vm-run", "x"],
        kind="test",
        reader="testexit",
        infra_codes=[124, 255, 75],
        level="t1",
    )
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == reason
    assert (result(rd)["status"], result(rd)["verdict"]) == (status, verdict)
    if verdict == "NO-GO":
        body = (store.out_dir(rd) / "result.md").read_text()
        assert "tests/a.py::t1" in body and "tests/b.py::t2" in body


def test_wrapper_wakes_tick(proj, tmp_path):
    mk(proj, tmp_path, ["true"], kind="duty", reader="exit")
    wrap.run(proj, "r1")
    mark = tmp_path / "tick-mark"
    for _ in range(50):
        if mark.exists():
            break
        time.sleep(0.1)
    assert mark.exists()


def test_spawn_failure_still_writes_exit_and_result(proj, tmp_path):
    rd = mk(proj, tmp_path, ["no-such-binary-xyz"], kind="duty", reader="exit")
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "spawn"
    assert result(rd)["status"] == "FAILED"


def test_payload_env_marks_headless(proj, tmp_path):
    rd = store.run_dir(proj, "r1")
    out = tmp_path / "env.txt"
    mk(
        proj,
        tmp_path,
        ["sh", "-c", f'echo "$DEVLOOP_HEADLESS $DEVLOOP_RUN $DEVLOOP_RUN_DIR" > {out}'],
        kind="duty",
        reader="exit",
    )
    wrap.run(proj, "r1")
    assert out.read_text().split() == ["1", "r1", str(store.out_dir(rd))]


@pytest.mark.parametrize("limit", ["idle", "deadline"])
def test_a_test_that_hangs_or_times_out_is_infra(proj, tmp_path, monkeypatch, limit):
    # A stuck node returns no test result, so it must not count toward
    # failed-twice and park the slice.
    extra = {"deadline_s": 1} if limit == "deadline" else {}
    if limit == "idle":
        monkeypatch.setenv("DEVLOOP_IDLE_SECS", "1")
    rd = mk(
        proj,
        tmp_path,
        ["sleep", "60"],
        kind="test",
        reader="testexit",
        infra_codes=[124, 255, 75],
        level="t1",
        **extra,
    )
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["reason"] == "infra"
    assert result(rd)["status"] == "FAILED"
