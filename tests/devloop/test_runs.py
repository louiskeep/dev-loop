import itertools
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from devloop import notes, runs, slices, store, wrap

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "plan.md").write_text("Rules consulted: x\n")
    (path / "f.txt").write_text("one\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "c1")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, fakebin, home):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(64 * 1024 * 1024))
    bins = tmp_path / "bins"
    bins.mkdir()
    wrap = bins / "devloop-wrap"
    wrap.write_text("#!/bin/sh\nsleep 30\n")
    wrap.chmod(0o755)
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(bins))
    monkeypatch.setenv("FAKE_FIX_CACHE", str(tmp_path / "fixcache"))
    yield
    for req in home.glob("*/runs/*/request.json"):
        pid = json.loads(req.read_text()).get("pid")
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def config(proj, cfg_edit, tmp_path):
    def edit(c):
        c["rules_dir"] = str(tmp_path / "rules")
        c["reviewers"]["codex"]["argv"] = {
            "codex-review": ["codex-review", "{worktree}", "{brief}", "{out}"],
            "codex-fix": ["codex-fix", "{run_id}", "{brief}", "{plan}"],
        }
        c["reviewers"]["codex"]["repair_out"] = (
            str(tmp_path / "fixcache") + "/{run_id}/{plan_name}"
        )
        c["tests"] = {
            "t1": {"argv": ["vm-run", "{worktree}"], "infra_codes": [124, 255, 75]},
            "full": {"argv": ["vm-run", "full"], "infra_codes": [124, 255, 75]},
        }
        c["brief_extra"] = "Standing: keep commits short."
        c["nodes"] = {"directory": "NODEDIR-MARKER"}

    cfg_edit(edit)


@pytest.fixture
def brief(tmp_path):
    p = tmp_path / "brief.md"
    p.write_text("BODY-MARKER do the task")
    return p


@pytest.fixture
def ready(proj, mk_slice, tmp_path):
    n = itertools.count()

    def make(phase="DEVELOP", risk="R1", worktree=None, **fields):
        if worktree is None:
            worktree = tmp_path / f"wt{next(n)}"
            head = make_repo(worktree)
        else:
            head = git(worktree, "rev-parse", "HEAD")
        fields.setdefault("plan_path", "plan.md")
        key = mk_slice(
            proj,
            phase=phase,
            risk=risk,
            rulebooks=["a.md"],
            worktree=str(worktree),
            branch="b",
            head=head,
            **fields,
        )
        return key, Path(worktree), head

    return make


def finish(proj, run_id, *, code=0, reason="exit", result=None):
    """Stand in for the wrapper: write result.md and the exit file."""
    rd = store.run_dir(proj, run_id)
    if result is not None:
        store.out_dir(rd).mkdir(exist_ok=True)
        (store.out_dir(rd) / "result.md").write_text(result)
    assert runs.create_exit(rd, code=code, reason=reason)
    req = runs.read_request(proj, run_id)
    if req["kind"] == "codex-fix":
        wrap.write_repair(rd, req)


def res(status="DONE", verdict="", head=""):
    return f"status: {status}\nhead: {head}\nverdict: {verdict}\n---\nbody\n"


def go(proj, brief, kind, key, **kw):
    return runs.dispatch(proj, kind, key, brief=brief, **kw)


# --- admission ----------------------------------------------------------------


def test_admission_refuses_ninth_builder(proj, ready, brief):
    for _ in range(8):
        go(proj, brief, "builder", ready()[0])
    with pytest.raises(runs.Refused, match="builder slot"):
        go(proj, brief, "builder", ready()[0])


def test_admission_refuses_fifth_tester(proj, ready, brief):
    for _ in range(4):
        go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    with pytest.raises(runs.Refused, match="tester slot"):
        go(proj, brief, "test", ready("VERIFY")[0], level="t1")


def test_admission_refuses_second_writer_same_worktree(proj, ready, brief):
    k1, wt, _ = ready()
    k2, _, _ = ready(worktree=wt)
    go(proj, brief, "builder", k1)
    with pytest.raises(runs.Refused, match="reserved"):
        go(proj, brief, "builder", k2)


def test_admission_refuses_low_memory(proj, ready, brief, monkeypatch):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", "1000")
    with pytest.raises(runs.Refused, match="memory"):
        go(proj, brief, "builder", ready()[0])


def test_admission_refuses_while_paused_or_capped(proj, ready, brief, home):
    (home / "p" / "PAUSE").write_text("")
    with pytest.raises(runs.Refused, match="paused"):
        go(proj, brief, "builder", ready()[0])
    (home / "p" / "PAUSE").unlink()
    (home / "p" / "cap-until").write_text("2999-01-01T00:00:00+00:00")
    with pytest.raises(runs.Refused, match="cap"):
        go(proj, brief, "builder", ready()[0])


def test_writer_reserved_until_applied(proj, ready, brief):
    k1, wt, _ = ready()
    k2, _, _ = ready(worktree=wt)
    r = go(proj, brief, "builder", k1)
    finish(proj, r, result=res("DONE"))
    with pytest.raises(runs.Refused, match="reserved"):
        go(proj, brief, "builder", k2)
    runs.apply_run(proj, r)
    go(proj, brief, "builder", k2)


def test_unknown_kind_refused(proj, ready, brief):
    with pytest.raises(runs.Refused, match="unknown kind"):
        go(proj, brief, "slice-runner", ready()[0])


def test_every_kind_has_reader_and_slot(proj):
    readers = {"claude", "claude-verdict", "verdict", "selfverdict", "testexit", "exit"}
    for name, kind in runs.KINDS.items():
        assert kind.reader in readers, name
        assert kind.slot in (None, "planner", "builder", "reviewer", "tester"), name
    assert runs.KINDS["test"].slot == "tester"


def test_argv_built_from_config_only(proj, ready, brief):
    import inspect

    assert "argv" not in inspect.signature(runs.dispatch).parameters
    r = go(proj, brief, "builder", ready()[0])
    argv = runs.read_request(proj, r)["argv"]
    assert argv[:2] == ["claude", "-p"] and "sonnet" in argv
    assert "--permission-mode" in argv and "auto" in argv


def test_planner_refuses_non_opus_model(proj, ready, brief, cfg_edit):
    cfg_edit(lambda c: c["models"].update(planner="sonnet"))
    with pytest.raises(runs.Refused, match="Opus"):
        go(proj, brief, "planner", ready("PLAN")[0])


def test_planner_creates_absent_plan(proj, ready, brief):
    key, wt, _ = ready("PLAN", plan_path="new-plan.md")
    r = go(proj, brief, "planner", key)
    assert runs.read_request(proj, r)["plan_sha"] is None
    assert slices.load(proj, key)["plan_frozen"] is True
    with pytest.raises(slices.SliceError, match="frozen"):
        slices.set_fields(proj, key, plan_path="other.md")


def test_dispatch_from_unrelated_cwd(proj, ready, brief, tmp_path, monkeypatch):
    key, _, _ = ready("PLAN", risk="R2")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    assert Path(runs.read_request(proj, r)["plan_copy"]).is_file()


def test_dispatch_closes_capture_pipes(proj, ready, brief, home):
    key, _, _ = ready()
    start = time.time()
    out = subprocess.run(
        [
            str(Path(__file__).resolve().parents[2] / "bin" / "devloop-dispatch"),
            proj,
            "builder",
            key,
            "--brief",
            str(brief),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert out.returncode == 0 and out.stdout.strip().startswith("builder.")
    assert time.time() - start < 20  # the wrapper sleeps 30 s with no pipe held


def test_dispatch_cli_prints_refusal(proj, ready, brief):
    out = subprocess.run(
        [
            str(Path(__file__).resolve().parents[2] / "bin" / "devloop-dispatch"),
            proj,
            "slice-runner",
            ready()[0],
            "--brief",
            str(brief),
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 3 and "REFUSED: unknown kind" in out.stderr


# --- liveness and results -----------------------------------------------------


def _dead_run(proj, key, **extra):
    rd = store.run_dir(proj, "builder.dead")
    rd.mkdir(parents=True)
    req = {
        "id": "builder.dead",
        "kind": "builder",
        "row": key.split("--")[0],
        "slice": key.split("--")[1],
        "key": key,
        "pid": 2**22 + 5,
        "pid_start": "1",
        "boot_id": runs.boot_id(),
        "reader": "claude",
        **extra,
    }
    store.write_json(rd / "request.json", req)
    return rd


def test_dead_run_without_exit_is_failed(proj, ready):
    key, _, _ = ready()
    rd = _dead_run(proj, key)
    assert runs.collect(proj) == ["builder.dead"]
    assert store.read_json(rd / "exit")["reason"] == "lost"
    assert runs.parse_result(rd, "builder").status == "FAILED"


def test_reused_pid_reads_dead(proj):
    me = os.getpid()
    live = {"pid": me, "pid_start": runs.proc_start(me), "boot_id": runs.boot_id()}
    assert runs.wrapper_alive(live) is True
    assert runs.wrapper_alive({**live, "pid_start": "1"}) is False
    assert runs.wrapper_alive({**live, "boot_id": "other"}) is False


def test_lost_wrapper_kills_payload_group(proj, ready):
    key, _, _ = ready()
    rd = _dead_run(proj, key)
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    runs.update_request(
        proj, "builder.dead", payload_pgid=p.pid, payload_start=runs.proc_start(p.pid)
    )
    runs.collect(proj)
    assert p.wait(timeout=10) == -signal.SIGKILL
    assert (rd / "exit").is_file()


def test_collect_does_not_overwrite_wrapper_exit(proj, ready):
    key, _, _ = ready()
    rd = _dead_run(proj, key)
    runs.create_exit(rd, code=0, reason="exit")
    runs.collect(proj)
    assert store.read_json(rd / "exit")["reason"] == "exit"


def test_status_contradicts_exit_is_failed(proj, ready):
    key, _, _ = ready()
    rd = _dead_run(proj, key)
    store.out_dir(rd).mkdir(exist_ok=True)
    (store.out_dir(rd) / "result.md").write_text(res("DONE"))
    runs.create_exit(rd, code=1, reason="exit")
    assert runs.parse_result(rd, "builder").status == "FAILED"


def test_zero_exit_without_result_is_failed(proj, ready):
    key, _, _ = ready()
    rd = _dead_run(proj, key)
    runs.create_exit(rd, code=0, reason="exit")
    assert runs.parse_result(rd, "builder").status == "FAILED"
    store.out_dir(rd).mkdir(exist_ok=True)
    (store.out_dir(rd) / "result.md").write_text(res("DONE", head="abc"))
    r = runs.parse_result(rd, "builder")
    assert (r.status, r.head) == ("DONE", "abc")


def test_lost_test_holds_slot(proj, ready, brief):
    runs_ = [go(proj, brief, "test", ready("VERIFY")[0], level="t1") for _ in range(4)]
    finish(proj, runs_[0], code=-1, reason="lost")
    with pytest.raises(runs.Refused, match="tester slot"):
        go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    runs.release(proj, runs_[0])
    go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    finish(proj, runs_[1], code=1, reason="exit")  # a real failing result frees it
    go(proj, brief, "test", ready("VERIFY")[0], level="t1")


# --- briefs -------------------------------------------------------------------


def test_brief_contains_rules_lessons_and_contract(proj, ready, brief, tmp_path):
    key, _, _ = ready()
    row = key.split("--")[0]
    notes.lesson(proj, "L1", area=row, wrong="bad habit", instead="good habit")
    r = go(proj, brief, "builder", key)
    text = (store.run_dir(proj, r) / "brief.md").read_text()
    for needle in (
        "Risk level: R1",
        str(tmp_path / "rules" / "a.md"),
        "recurring-defects.md",
        "Standing: keep commits short.",
        "NODEDIR-MARKER",
        "bad habit",
        "BODY-MARKER",
        "result.md",
    ):
        assert needle in text, needle


def test_brief_role_section_comes_from_the_playbook(
    proj, ready, brief, monkeypatch, tmp_path
):
    skill = tmp_path / "skill"
    shutil.copytree(store.skill_dir(), skill)
    (skill / "PLAYBOOK.md").write_text(
        "## Worker briefs\n\n### builder\n\nBUILDER-MARK\n\n### dennis\n\nDENNIS-MARK\n"
    )
    monkeypatch.setenv("DEVLOOP_SKILL_DIR", str(skill))
    r = go(proj, brief, "builder", ready()[0])
    text = (store.run_dir(proj, r) / "brief.md").read_text()
    assert "BUILDER-MARK" in text and "DENNIS-MARK" not in text


def test_review_brief_ends_with_verdict_line_instruction(proj, ready, brief):
    key, _, _ = ready("PLAN", risk="R2")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    assert "VERDICT: GO" in (store.run_dir(proj, r) / "brief.md").read_text()


# --- reviews: roster, predecessor, binding ------------------------------------


def test_review_runs_on_the_private_checkout_of_a_dirty_worktree(proj, ready, brief):
    key, wt, head = ready("REVIEW", risk="R1")
    (wt / "f.txt").write_text("edited\n")
    (wt / "stray.txt").write_text("uncommitted\n")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    tree = Path(runs.read_request(proj, r)["tree"])
    assert (tree / "f.txt").read_text() == "one\n" and not (tree / "stray.txt").exists()


def test_review_refused_when_the_head_is_not_a_commit(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R1")
    s = slices.load(proj, key)
    s["head"] = "d" * 40
    slices._save(proj, s)
    with pytest.raises(runs.Refused, match="not a commit"):
        go(proj, brief, "codex-review", key, reviewer="codex")


def test_codex_code_review_refused_before_dennis_go(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R2")
    with pytest.raises(runs.Refused, match="dennis"):
        go(proj, brief, "codex-review", key, reviewer="codex")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    c = go(proj, brief, "codex-review", key, reviewer="codex")
    assert runs.read_request(proj, c)["predecessor_run"] == r


def test_reviewer_kind_binding_enforced(proj, ready, brief, cfg_edit):
    key, _, _ = ready("REVIEW", risk="R2")
    with pytest.raises(runs.Refused, match="may not run"):
        go(proj, brief, "codex-review", key, reviewer="dennis")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, r, result=res("DONE", "GO"))
    # The pairing is checked again at apply time, against the config as it is then.
    cfg_edit(lambda c: c["reviewers"]["dennis"].update(kinds=["codex-review"]))
    with pytest.raises(runs.RunError, match="do not match"):
        runs.apply_run(proj, r)


def test_apply_refuses_head_mismatch(proj, ready, brief):
    key, wt, _ = ready("REVIEW", risk="R1")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    finish(proj, r, result=res("DONE", "GO"))
    (wt / "f.txt").write_text("two\n")
    git(wt, "commit", "-qam", "c2")
    slices.set_fields(proj, key, head=git(wt, "rev-parse", "HEAD"))
    assert runs.apply_run(proj, r) == "stale"
    s = slices.load(proj, key)
    assert s["evidence"] == {} and r in s["processed_run_ids"]


def test_apply_records_review_evidence_for_the_frozen_head(proj, ready, brief):
    key, _, head = ready("REVIEW", risk="R1")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    finish(proj, r, result=res("DONE", "GO-WITH-FIXES"))
    runs.apply_run(proj, r)
    e = slices.load(proj, key)["evidence"][head]["code:codex"]
    assert (e["verdict"], e["run"]) == ("GO-WITH-FIXES", r)
    assert slices.load(proj, key)["run"] is None


def test_plan_review_stale_after_plan_edit(proj, ready, brief):
    key, wt, _ = ready("PLAN", risk="R2")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, r, result=res("DONE", "GO"))
    (wt / "plan.md").write_text("Rules consulted: y\n")
    assert runs.apply_run(proj, r) == "stale"
    assert slices.load(proj, key)["evidence"] == {}


def test_late_plan_review_grants_no_code_go(proj, ready, brief):
    key, _, head = ready("PLAN", risk="R2")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, r, result=res("DONE", "GO"))
    # The slice has moved on by the time the plan review is applied.
    s = slices.load(proj, key)
    s["phase"] = "REVIEW"
    slices._save(proj, s)
    runs.apply_run(proj, r)
    s = slices.load(proj, key)
    kinds = {k for bucket in s["evidence"].values() for k in bucket}
    assert kinds == {"plan:dennis"} and head not in s["evidence"]


def test_simultaneous_plan_reviews_refused(proj, ready, brief):
    key, _, _ = ready("PLAN", risk="R2")
    go(proj, brief, "dennis", key, reviewer="dennis")
    with pytest.raises(runs.Refused, match="plan review"):
        go(proj, brief, "dennis", key, reviewer="dennis")


def test_plan_rounds_reset_on_new_plan_path(proj, ready, brief, monkeypatch):
    key, wt, _ = ready("PLAN", risk="R2")
    for _ in range(2):
        r = go(proj, brief, "dennis", key, reviewer="dennis")
        finish(proj, r, result=res("DONE", "NO-GO"))
        runs.apply_run(proj, r)
    assert slices.load(proj, key)["plan_rounds"]["plan.md"]["dennis"] == 2
    with pytest.raises(runs.Refused, match="round cap"):
        go(proj, brief, "dennis", key, reviewer="dennis")
    (wt / "plan2.md").write_text("Rules consulted: z\n")
    s = slices.load(proj, key)  # an interactive reframe starts a new lineage
    slices.move(proj, key, "FRAME", "new plan")
    slices.set_fields(proj, key, plan_path="plan2.md")
    s = slices.load(proj, key)
    assert s["plan_rounds"] == {}
    s["phase"] = "PLAN"
    slices._save(proj, s)
    go(proj, brief, "dennis", key, reviewer="dennis")


def test_infra_exit_counts_no_failure(proj, ready, brief):
    key, _, _ = ready("VERIFY")
    r = go(proj, brief, "test", key, level="t1")
    finish(proj, r, code=255, reason="infra", result=res("FAILED"))
    runs.apply_run(proj, r)
    s = slices.load(proj, key)
    assert s["failures"] == {} and s["evidence"] == {}


def test_failed_twice_parks(proj, ready, brief):
    key, _, _ = ready()
    for i in range(2):
        r = go(proj, brief, "builder", key)
        finish(proj, r, code=1, result=res("FAILED"))
        runs.apply_run(proj, r)
        if i == 0:
            assert slices.load(proj, key)["phase"] == "DEVELOP"
    s = slices.load(proj, key)
    assert (s["phase"], s["park_reason"]) == ("PARKED", "failed-twice")


def test_apply_replay_counts_once(proj, ready, brief):
    key, _, _ = ready("PLAN", risk="R2")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    (store.run_dir(proj, r) / "applied").unlink()  # crash before the marker
    runs.apply_run(proj, r)
    s = slices.load(proj, key)
    assert s["plan_rounds"]["plan.md"]["dennis"] == 1
    assert s["processed_run_ids"].count(r) == 1


# --- plan repair --------------------------------------------------------------


def _repair(proj, ready, brief, *, plant=False):
    key, wt, _ = ready("PLAN", risk="R2")
    d = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, d, result=res("DONE", "NO-GO"))
    runs.apply_run(proj, d)
    d2 = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, d2, result=res("DONE", "NO-GO"))
    runs.apply_run(proj, d2)
    r = go(proj, brief, "codex-fix", key, reviewer="codex")
    req = runs.read_request(proj, r)
    out = Path(req["repair_out"])
    out.parent.mkdir(parents=True)
    out.write_text("Rules consulted: repaired\n")
    if plant:
        planted = store.run_dir(proj, r) / "repair.json"
        planted.write_text(json.dumps({"input_sha": "f" * 64, "output_sha": "f" * 64}))
    return key, wt, r


def test_repair_installed_only_if_plan_unchanged(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    (wt / "plan.md").write_text("Rules consulted: edited meanwhile\n")
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert "edited meanwhile" in (wt / "plan.md").read_text()
    kinds = {k for b in slices.load(proj, key)["evidence"].values() for k in b}
    assert "plan:codex" not in kinds


def test_repair_installs_and_credits_the_installed_bytes(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert (wt / "plan.md").read_text() == "Rules consulted: repaired\n"
    sha = slices.plan_sha(slices.load(proj, key))
    assert slices.load(proj, key)["evidence"][sha]["plan:codex"]["verdict"] == "GO"
    # The repair's GO counts toward the plan exit because the credit names the file.
    assert all(ok for _, ok in slices.exit_checklist(proj, key, "DEVELOP"))


def test_repair_replay_after_install(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    (store.run_dir(proj, r) / "applied").unlink()
    s = slices.load(proj, key)
    s["processed_run_ids"].remove(r)  # crash between install and credit
    s["evidence"] = {}
    slices._save(proj, s)
    runs.apply_run(proj, r)
    sha = slices.plan_sha(slices.load(proj, key))
    assert "plan:codex" in slices.load(proj, key)["evidence"][sha]


def test_repair_refused_outside_plan(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R2")
    with pytest.raises(runs.Refused, match="does not run in REVIEW"):
        go(proj, brief, "codex-fix", key, reviewer="codex")


# --- tests, pinned checkouts, trains ------------------------------------------


def test_pinned_run_ignores_uncommitted_files(proj, ready, brief):
    key, wt, _ = ready("VERIFY")
    (wt / "stray.txt").write_text("not committed")
    r = go(proj, brief, "test", key, level="t1")
    tree = Path(runs.read_request(proj, r)["tree"])
    assert (tree / "f.txt").is_file() and not (tree / "stray.txt").exists()
    assert runs.read_request(proj, r)["cwd"] == str(tree)


def _train(proj, ready, brief):
    key, wt, h1 = ready("REVIEW")
    (wt / "f.txt").write_text("two\n")
    git(wt, "commit", "-qam", "c2")
    top, _, h2 = ready("VERIFY", worktree=wt)
    return key, h1, top, h2


def test_train_member_must_be_ancestor(proj, ready, brief):
    key, h1, top, h2 = _train(proj, ready, brief)
    r = go(proj, brief, "test", top, level="full", covers=[key])
    assert runs.read_request(proj, r)["covers"] == {key: h1}
    with pytest.raises(runs.Refused, match="ancestor"):
        go(proj, brief, "test", key, level="full", covers=[top])


def test_stacked_train_credits_members_and_crash_after_first(
    proj, ready, brief, monkeypatch
):
    key, h1, top, h2 = _train(proj, ready, brief)
    r = go(proj, brief, "test", top, level="full", covers=[key])
    finish(proj, r, result=res("DONE", "GO", head=h2))
    real = slices.apply_effects
    calls = []

    def crash_on_second(*a, **kw):
        calls.append(a[1])
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(slices, "apply_effects", crash_on_second)
    with pytest.raises(RuntimeError):
        runs.apply_run(proj, r)
    assert not (store.run_dir(proj, r) / "applied").exists()
    monkeypatch.setattr(slices, "apply_effects", real)
    runs.apply_run(proj, r)
    for k, h in ((top, h2), (key, h1)):
        s = slices.load(proj, k)
        assert s["evidence"][h]["full"]["run"] == r
        assert s["processed_run_ids"].count(r) == 1


def run_state(home, *args):
    bin_ = Path(__file__).resolve().parents[2] / "bin" / "devloop-state"
    return subprocess.run(
        [str(bin_), "p", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "DEVLOOP_HOME": str(home)},
    )


def test_state_cli_applied_release_and_list(proj, ready, brief, home):
    key, _, head = ready("REVIEW")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    live = run_state(home, "list", "--live")
    assert r in live.stdout and live.returncode == 0
    finish(proj, r, result=res("DONE", "GO"))
    assert run_state(home, "applied", r).stdout.strip() == "applied"
    assert run_state(home, "release", r).returncode == 3  # not a test run
    t = go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    finish(proj, t, code=-1, reason="lost")
    assert t in run_state(home, "list", "--live").stdout
    assert run_state(home, "release", t).returncode == 0
    assert t not in run_state(home, "list", "--live").stdout
    assert run_state(home, "list", "--writers").returncode == 0


# --- control files are out of the payload's reach ---------------------------------


def test_payload_dir_is_the_only_writable_place(proj, ready, brief):
    key, _, _ = ready("REVIEW")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    req = runs.read_request(proj, r)
    rd = store.run_dir(proj, r)
    assert req["cwd"] == str(store.out_dir(rd)) or "tree" in req
    assert req["out"].startswith(str(store.out_dir(rd)))
    assert req["last_msg"].startswith(str(store.out_dir(rd)))
    with pytest.raises(PermissionError):
        (rd / "request.json").write_text("{}")
    with pytest.raises(runs.RunError, match="process fields"):
        runs.update_request(proj, r, head="ff")


def test_payload_that_rewrites_request_gets_no_evidence(proj, ready, brief):
    key, _, head = ready("REVIEW")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    finish(proj, r, result=res("DONE", "NO-GO"))
    rd = store.run_dir(proj, r)
    forged = store.read_json(rd / "request.json")
    forged["head"] = "ff"  # try to move the verdict onto another head
    os.chmod(rd / "request.json", 0o644)
    store.write_json(rd / "request.json", forged)
    with pytest.raises(runs.RunError, match="ledger"):
        runs.apply_run(proj, r)
    assert slices.load(proj, key)["evidence"] == {}
    # A forged request cannot support a gate check either.
    slices.record_evidence(proj, key, "code:codex", "GO", r, head=head)
    assert any(
        "ledger" in why
        for why, ok in slices.exit_checklist(proj, key, "GATE")
        if not ok
    )


def test_preplanted_repair_json_is_replaced_by_the_wrapper(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief, plant=True)
    finish(proj, r, result=res("DONE", "GO"))
    data = store.read_json(store.run_dir(proj, r) / "repair.json")
    assert data["output_sha"] != "f" * 64
    runs.apply_run(proj, r)
    assert (wt / "plan.md").read_text() == "Rules consulted: repaired\n"


def test_missing_repair_record_is_a_failure(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    rd = store.run_dir(proj, r)
    runs.create_exit(rd, code=0, reason="exit")  # wrapper never ran write_repair
    store.out_dir(rd).mkdir(exist_ok=True)
    (store.out_dir(rd) / "result.md").write_text(res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert "repaired" not in (wt / "plan.md").read_text()
    assert slices.load(proj, key)["failures"]


def test_tampered_repaired_copy_is_caught_before_install(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    (store.run_dir(proj, r) / "repaired").write_text("Rules consulted: evil\n")
    with pytest.raises(runs.RunError, match="repaired copy"):
        runs.apply_run(proj, r)
    assert "evil" not in (wt / "plan.md").read_text()


def test_tampered_repaired_copy_is_caught_by_the_gate_recompute(proj, ready, brief):
    key, wt, r = _repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert all(ok for _, ok in slices.exit_checklist(proj, key, "DEVELOP"))
    (store.run_dir(proj, r) / "repaired").write_text("Rules consulted: evil\n")
    assert not all(ok for _, ok in slices.exit_checklist(proj, key, "DEVELOP"))


# --- permanent refusals close the run ---------------------------------------------


def _events(home, kind):
    path = home / "p" / "events.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if f'"{kind}"' in x]


def test_ledger_mismatch_rejects_the_run_and_frees_the_slot(proj, ready, brief, home):
    started = [
        go(proj, brief, "test", ready("VERIFY")[0], level="t1") for _ in range(4)
    ]
    bad = started[0]
    finish(
        proj, bad, code=-1, reason="lost"
    )  # a lost test would hold its slot for good
    rd = store.run_dir(proj, bad)
    forged = store.read_json(rd / "request.json")
    forged["head"] = "ff"
    store.write_json(rd / "request.json", forged)
    with pytest.raises(runs.RunError, match="ledger"):
        runs.apply_run(proj, bad)
    assert (rd / "rejected").is_file()
    assert _events(home, "apply-refused")[0]["why"] == "ledger"
    assert bad not in [r for r, _ in runs.live_runs(proj)]
    assert bad not in runs.collect(proj)
    go(proj, brief, "test", ready("VERIFY")[0], level="t1")  # the slot is free again
    assert runs.apply_run(proj, bad) == "rejected"  # terminal, no second event
    assert len(_events(home, "apply-refused")) == 1


def test_binding_mismatch_rejects_the_run_and_frees_the_slot(
    proj, ready, brief, cfg_edit
):
    keys = [ready("REVIEW", risk="R2")[0] for _ in range(3)]
    r1 = go(proj, brief, "dennis", keys[0], reviewer="dennis")
    go(proj, brief, "dennis", keys[1], reviewer="dennis")
    with pytest.raises(runs.Refused, match="reviewer slot"):
        go(proj, brief, "dennis", keys[2], reviewer="dennis")
    finish(proj, r1, result=res("DONE", "GO"))
    cfg_edit(lambda c: c["reviewers"]["dennis"].update(kinds=["codex-review"]))
    with pytest.raises(runs.RunError, match="do not match"):
        runs.apply_run(proj, r1)
    assert (store.run_dir(proj, r1) / "rejected").is_file()
    assert slices.load(proj, keys[0])["evidence"] == {}
    cfg_edit(lambda c: c["reviewers"]["dennis"].update(kinds=["dennis"]))
    go(proj, brief, "dennis", keys[2], reviewer="dennis")


def test_repaired_hash_mismatch_rejects_the_run_and_frees_the_worktree(
    proj, ready, brief
):
    key, wt, r = _repair(proj, ready, brief)
    other, _, _ = ready("PLAN", worktree=wt, plan_path="plan.md")
    finish(proj, r, result=res("DONE", "GO"))
    assert runs.writer_runs(proj)
    (store.run_dir(proj, r) / "repaired").write_text("Rules consulted: evil\n")
    with pytest.raises(runs.RunError, match="repaired copy"):
        runs.apply_run(proj, r)
    assert (store.run_dir(proj, r) / "rejected").is_file()
    assert runs.writer_runs(proj) == [] and runs.live_runs(proj) == []
    assert slices.load(proj, key)["evidence"].get("x") is None
    go(proj, brief, "planner", other)  # the worktree reservation is gone


def test_discard_frees_a_stuck_run(proj, ready, brief, home, monkeypatch):
    key, wt, _ = ready()
    r = go(proj, brief, "builder", key)
    assert runs.writer_runs(proj)
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        runs.discard(proj, r)
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    runs.discard(proj, r)
    rd = store.run_dir(proj, r)
    assert store.read_json(rd / "exit")["reason"] == "discarded"
    assert (rd / "rejected").read_text() == "discarded"
    assert runs.writer_runs(proj) == [] and runs.live_runs(proj) == []
    assert slices.load(proj, key)["run"] is None
    assert _events(home, "apply-refused")[0]["why"] == "discarded"
    go(proj, brief, "builder", key)


def test_state_cli_discard_is_interactive_only(proj, ready, brief, home):
    key, _, _ = ready()
    r = go(proj, brief, "builder", key)
    bin_ = Path(__file__).resolve().parents[2] / "bin" / "devloop-state"
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    headless = subprocess.run(
        [str(bin_), "p", "discard", r],
        capture_output=True,
        text=True,
        env={**env, "DEVLOOP_HEADLESS": "1"},
    )
    assert headless.returncode == 3
    assert subprocess.run([str(bin_), "p", "discard", r], env=env).returncode == 0


# --- failure lineage and the no-verdict reason ------------------------------------


def test_builder_failures_count_across_new_heads(proj, ready, brief):
    key, wt, _ = ready()
    for n in range(2):
        r = go(proj, brief, "builder", key)
        finish(proj, r, code=1, result=res("FAILED"))
        runs.apply_run(proj, r)
        (wt / "f.txt").write_text(f"try {n}\n")
        git(wt, "commit", "-qam", f"try {n}")
        if n == 0:  # the builder moved the branch before failing again
            slices.set_fields(proj, key, head=git(wt, "rev-parse", "HEAD"))
    s = slices.load(proj, key)
    assert (s["phase"], s["park_reason"]) == ("PARKED", "failed-twice")
    assert len(s["failures"]) == 1 and next(iter(s["failures"])).startswith("builder:")


def test_planner_failures_are_keyed_by_plan_path(proj, ready, brief):
    key, _, _ = ready("PLAN", plan_path="new-plan.md")
    for _ in range(2):
        r = go(proj, brief, "planner", key)
        finish(proj, r, code=1, result=res("FAILED"))
        runs.apply_run(proj, r)
    s = slices.load(proj, key)
    assert s["failures"] == {"planner:new-plan.md": 2} and s["phase"] == "PARKED"


def test_no_verdict_retries_once_and_is_not_a_double_failure(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R1")
    for i in range(2):
        r = go(proj, brief, "codex-review", key, reviewer="codex")
        finish(proj, r, code=0, reason="no-verdict", result=res("FAILED"))
        runs.apply_run(proj, r)
        if i == 0:
            assert slices.load(proj, key)["phase"] == "REVIEW"
    s = slices.load(proj, key)
    assert s["park_reason"] == "no-verdict" and s["phase"] == "PARKED"
    assert all(k.endswith(":no-verdict") for k in s["failures"])


def test_one_no_verdict_and_one_real_failure_do_not_park(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R1")
    r1 = go(proj, brief, "codex-review", key, reviewer="codex")
    finish(proj, r1, code=0, reason="no-verdict", result=res("FAILED"))
    runs.apply_run(proj, r1)
    r2 = go(proj, brief, "codex-review", key, reviewer="codex")
    finish(proj, r2, code=1, result=res("FAILED"))
    runs.apply_run(proj, r2)
    assert slices.load(proj, key)["phase"] == "REVIEW"


def test_remove_tree_ignores_a_path_named_in_the_request(proj, ready, brief, tmp_path):
    key, _, _ = ready("REVIEW", risk="R1")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    req = dict(runs.read_request(proj, r))
    req["tree"] = str(outside)
    runs.remove_tree(proj, r, req)
    assert (outside / "keep.txt").exists()
    assert not (store.run_dir(proj, r) / "tree").exists()


def test_remove_tree_skips_a_symlinked_tree(proj, ready, brief, tmp_path):
    key, _, _ = ready("REVIEW", risk="R1")
    r = go(proj, brief, "codex-review", key, reviewer="codex")
    rd = store.run_dir(proj, r)
    runs.remove_tree(proj, r, runs.read_request(proj, r))
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    (rd / "tree").symlink_to(outside)
    runs.remove_tree(proj, r, {})
    assert (outside / "keep.txt").exists()
