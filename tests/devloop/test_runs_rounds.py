import pytest
from runs_support import (  # noqa: F401
    brief,
    config,
    env,
    finish,
    git,
    go,
    ready,
    res,
)

from devloop import runs, slices, store, wrap


def at_round(proj, key, n):
    s = slices.load(proj, key)
    s["round"] = n
    slices._save(proj, s)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_rounds_one_to_three_are_fixed_by_the_claude_builder(proj, ready, brief, n):
    key, _, _ = ready("REMEDIATE")
    at_round(proj, key, n)
    with pytest.raises(runs.Refused, match="round 4 and up"):
        go(proj, brief, "codex-builder", key)
    r = go(proj, brief, "builder", key)
    assert runs.read_request(proj, r)["kind"] == "builder"


@pytest.mark.parametrize("n", [4, 5, 6])
def test_rounds_four_to_six_are_fixed_by_codex(proj, ready, brief, n):
    key, _, _ = ready("REMEDIATE")
    at_round(proj, key, n)
    with pytest.raises(runs.Refused, match="codex-builder, not builder"):
        go(proj, brief, "builder", key)
    r = go(proj, brief, "codex-builder", key)
    req = runs.read_request(proj, r)
    assert req["kind"] == "codex-builder" and req["slot"] == "builder"
    assert req["argv"][0] == "codex-builder" and req["reader"] == "exit"


def test_codex_builder_runs_only_in_remediate(proj, ready, brief):
    key, _, _ = ready("DEVELOP")
    at_round(proj, key, 5)
    with pytest.raises(runs.Refused, match="does not run in DEVELOP"):
        go(proj, brief, "codex-builder", key)


def test_codex_builder_reserves_the_worktree_and_uses_a_builder_slot(
    proj, ready, brief
):
    key, wt, _ = ready("REMEDIATE")
    at_round(proj, key, 4)
    r = go(proj, brief, "codex-builder", key)
    other, _, _ = ready("DEVELOP", worktree=wt)
    with pytest.raises(runs.Refused, match="worktree is reserved"):
        go(proj, brief, "builder", other)
    finish(proj, r, code=0, result=res("DONE"))
    runs.apply_run(proj, r)
    go(proj, brief, "builder", other)


def test_codex_builder_result_carries_the_commit_it_finished_on(
    proj, ready, brief, fakebin, monkeypatch
):
    key, wt, head = ready("REMEDIATE")
    at_round(proj, key, 4)
    r = go(proj, brief, "codex-builder", key)
    wrap.run(proj, r)
    rd = store.run_dir(proj, r)
    new = git(wt, "rev-parse", "HEAD")
    assert new != head
    hdr = store.read_header(store.out_dir(rd) / "result.md")
    assert hdr["status"] == "DONE" and hdr["head"] == new
    assert runs.apply_run(proj, r) == "applied"


def test_a_failing_codex_builder_counts_a_failure_not_a_park_for_rounds(
    proj, ready, brief, fakebin, monkeypatch
):
    key, _, _ = ready("REMEDIATE")
    at_round(proj, key, 4)
    monkeypatch.setenv("FAKE_EXIT", "3")
    r = go(proj, brief, "codex-builder", key)
    wrap.run(proj, r)
    runs.apply_run(proj, r)
    s = slices.load(proj, key)
    assert s["phase"] == "REMEDIATE" and any(
        k.startswith("codex-builder:") for k in s["failures"]
    )


def test_seventh_round_redesigns_the_plan_and_nothing_parks(proj, mk_slice):
    k = mk_slice(proj, phase="REVIEW", pr=9, worktree="/tmp/wt-keep")
    s = slices.load(proj, k)
    s.update(
        plan_frozen=True, approved_plan_sha="abc", plan_rounds={"p.md": {"codex": 2}}
    )
    slices._save(proj, s)
    seen = []
    for n in range(1, 7):
        s = slices.move(proj, k, "REMEDIATE", "review findings")
        assert (s["phase"], s["round"]) == ("REMEDIATE", n), n
        seen.append(s["phase"])
        s = slices.load(proj, k)
        s["phase"] = "REVIEW"
        slices._save(proj, s)
    s = slices.move(proj, k, "REMEDIATE", "review findings")
    assert (s["phase"], s["round"]) == ("PLAN", 0)
    assert s["history"][-1]["reason"] == "redesign"
    assert s["park_reason"] is None and s["hold_question"] is None
    assert (s["plan_frozen"], s["approved_plan_sha"], s["plan_rounds"]) == (
        False,
        None,
        {},
    )
    assert (s["pr"], s["worktree"]) == (9, "/tmp/wt-keep")
    assert "PARKED" not in seen


def test_snapshot_next_command_follows_the_ladder(proj, mk_slice):
    from devloop import snapshot

    k = mk_slice(proj, phase="REMEDIATE", round=2)
    assert f"devloop-dispatch {proj} builder {k}" in snapshot.build_snapshot(proj)[0]
    s = slices.load(proj, k)
    s["round"] = 5
    slices._save(proj, s)
    assert (
        f"devloop-dispatch {proj} codex-builder {k}" in snapshot.build_snapshot(proj)[0]
    )
