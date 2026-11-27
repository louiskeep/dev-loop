import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from runs_support import (  # noqa: F401
    brief,
    config,
    dead_run,
    env,
    events,
    finish,
    git,
    go,
    make_repo,
    ready,
    repair,
    res,
)

from devloop import runs, slices, snapshot, store

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
    _key, wt, r = repair(proj, ready, brief, plant=True)
    finish(proj, r, result=res("DONE", "GO"))
    data = store.read_json(store.run_dir(proj, r) / "repair.json")
    assert data["output_sha"] != "f" * 64
    runs.apply_run(proj, r)
    assert (wt / "plan.md").read_text() == "Rules consulted: repaired\n"


def test_missing_repair_record_is_a_failure(proj, ready, brief):
    key, wt, r = repair(proj, ready, brief)
    rd = store.run_dir(proj, r)
    runs.create_exit(rd, code=0, reason="exit")  # wrapper never ran write_repair
    store.out_dir(rd).mkdir(exist_ok=True)
    (store.out_dir(rd) / "result.md").write_text(res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert "repaired" not in (wt / "plan.md").read_text()
    assert slices.load(proj, key)["failures"]


def test_tampered_repaired_copy_is_caught_before_install(proj, ready, brief):
    _key, wt, r = repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    (store.run_dir(proj, r) / "repaired").write_text("Rules consulted: evil\n")
    with pytest.raises(runs.RunError, match="repaired copy"):
        runs.apply_run(proj, r)
    assert "evil" not in (wt / "plan.md").read_text()


def test_tampered_repaired_copy_is_caught_by_the_gate_recompute(proj, ready, brief):
    key, _wt, r = repair(proj, ready, brief)
    finish(proj, r, result=res("DONE", "GO"))
    runs.apply_run(proj, r)
    assert all(ok for _, ok in slices.exit_checklist(proj, key, "DEVELOP"))
    (store.run_dir(proj, r) / "repaired").write_text("Rules consulted: evil\n")
    assert not all(ok for _, ok in slices.exit_checklist(proj, key, "DEVELOP"))


# --- permanent refusals close the run ---------------------------------------------


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
    assert events(home, "apply-refused")[0]["why"] == "ledger"
    assert bad not in [r for r, _ in runs.live_runs(proj)]
    assert bad not in runs.collect(proj)
    go(proj, brief, "test", ready("VERIFY")[0], level="t1")  # the slot is free again
    assert runs.apply_run(proj, bad) == "rejected"  # terminal, no second event
    assert len(events(home, "apply-refused")) == 1


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
    key, wt, r = repair(proj, ready, brief)
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
    key, _wt, _ = ready()
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
    assert events(home, "apply-refused")[0]["why"] == "discarded"
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
        check=False,
    )
    assert headless.returncode == 3
    assert (
        subprocess.run([str(bin_), "p", "discard", r], env=env, check=False).returncode
        == 0
    )


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


def test_third_no_verdict_after_a_resume_is_owner_gated(proj, ready, brief):
    key, _, _ = ready("REVIEW", risk="R1")

    def no_verdict():
        r = go(proj, brief, "codex-review", key, reviewer="codex")
        finish(proj, r, code=0, reason="no-verdict", result=res("FAILED"))
        runs.apply_run(proj, r)

    no_verdict()
    no_verdict()
    s = slices.load(proj, key)
    assert s["park_reason"] == "no-verdict" and s["phase"] == "PARKED"
    slices.move(proj, key, "REVIEW", "tick resume")  # allowed without the owner
    no_verdict()
    s = slices.load(proj, key)
    assert (s["phase"], s["park_reason"]) == ("PARKED", "failed-twice")
    with pytest.raises(slices.TransitionError, match="owner"):
        slices.move(proj, key, "REVIEW", "tick resume")


def test_test_argv_label_is_unique_per_run(proj, ready, brief, cfg_edit):
    cfg_edit(lambda c: c["tests"]["t1"].update(argv=["runner", "--label", "{label}"]))
    key, _, _ = ready("VERIFY")
    first = go(proj, brief, "test", key, level="t1")
    finish(proj, first, code=255, reason="infra", result=res("FAILED"))
    runs.apply_run(proj, first)
    second = go(proj, brief, "test", key, level="t1")
    a = runs.read_request(proj, first)["argv"]
    b = runs.read_request(proj, second)["argv"]
    assert a[-1] != b[-1] and a[-1].endswith(first) and b[-1].endswith(second)


# --- exit record integrity -----------------------------------------------------


def test_exit_is_published_whole_or_not_at_all(proj, ready, brief):
    key, _, _ = ready()
    r = go(proj, brief, "builder", key)
    rd = store.run_dir(proj, r)
    assert runs.create_exit(rd, code=0, reason="exit") is True
    assert runs.create_exit(rd, code=9, reason="lost") is False  # first writer wins
    assert store.read_json(rd / "exit")["code"] == 0
    assert not list(rd.glob(".exit.*"))
    assert runs.exit_valid(rd)


@pytest.mark.parametrize("junk", ["", "{", "[1]", '{"code": "x"}'])
def test_collect_recovers_a_damaged_exit_once_the_wrapper_is_dead(proj, ready, junk):
    key, _, _ = ready()
    rd = dead_run(proj, key)
    (rd / "exit").write_text(junk)
    assert not runs.exit_valid(rd)
    snapshot.build_snapshot(proj)  # never raises on a damaged record
    assert runs.collect(proj) == ["builder.dead"]
    assert runs.exit_valid(rd) and store.read_json(rd / "exit")["reason"] == "lost"


def test_plan_review_credits_and_names_the_frozen_copy(proj, ready, brief, monkeypatch):
    key, _wt, _ = ready("PLAN", risk="R2")
    live = slices.plan_file(slices.load(proj, key))
    original = live.read_bytes()
    # The live file changes between admission reading it and the copy being made.
    real = shutil.copyfile

    def edit_then_copy(src, dst):
        live.write_text("Rules consulted: edited in the window\n")
        return real(src, dst)

    monkeypatch.setattr(runs.shutil, "copyfile", edit_then_copy)
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    monkeypatch.setattr(runs.shutil, "copyfile", real)
    req = runs.read_request(proj, r)
    copy = Path(req["plan_copy"])
    assert req["plan_sha"] == hashlib.sha256(copy.read_bytes()).hexdigest()
    assert (
        copy.read_bytes() != original
    )  # it holds what was copied, and is hashed as such
    text = (store.run_dir(proj, r) / "brief.md").read_text()
    assert str(copy) in text and "frozen copy" in text
    live.write_text("Rules consulted: edited after dispatch\n")
    finish(proj, r, result=res("DONE", "GO"))
    assert runs.apply_run(proj, r) == "stale"  # the live plan no longer matches
    assert slices.load(proj, key)["evidence"] == {}


def test_docs_reserve_the_worktree_but_take_no_slot(proj, ready, brief):
    key, wt, _ = ready("DOCUMENT")
    r = go(proj, brief, "docs", key)
    assert runs.read_request(proj, r)["root"]
    other, _, _ = ready("DEVELOP", worktree=wt)
    with pytest.raises(runs.Refused, match="worktree is reserved"):
        go(proj, brief, "builder", other)
    with pytest.raises(runs.Refused, match="worktree is reserved"):
        go(proj, brief, "docs", key)
    assert runs.KINDS["docs"].slot is None
    finish(proj, r, result=res("DONE", head=""))
    runs.apply_run(proj, r)
    go(proj, brief, "builder", other)  # freed when applied
