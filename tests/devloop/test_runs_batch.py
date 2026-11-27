"""One t1 run for several slices: refusals, apply, evidence, and the real git merge."""

import os

import pytest
from gitfix import Fix
from gitfix import git as fgit
from runs_support import (  # noqa: F401
    brief,
    config,
    env,
    finish,
    go,
    ready,
    res,
)

from devloop import batch as batchmod
from devloop import runs, slices, store


@pytest.fixture
def fake_build(monkeypatch):
    """No fetch or merge: the 'batch commit' is the lead's own HEAD, a real commit."""
    from runs_support import git

    monkeypatch.setattr(
        batchmod, "build", lambda path, base, frozen: git(path, "rev-parse", "HEAD")
    )


def _verify(ready, n=3):
    return [ready("VERIFY") for _ in range(n)]


def _dispatch(proj, brief, ks, **kw):
    lead, *rest = [k for k, _, _ in ks]
    return go(proj, brief, "test", lead, level="t1", batch=rest, **kw)


def _t1(proj, key):
    s = slices.load(proj, key)
    return (s["evidence"].get(s["head"]) or {}).get("t1")


def _red(proj, brief, ks, *, no_result=False):
    r = _dispatch(proj, brief, ks)
    result = None if no_result else res("DONE", "NO-GO", ks[0][2])
    finish(proj, r, code=1, result=result)
    runs.apply_run(proj, r)
    return r


# --- apply ----------------------------------------------------------------------


def test_green_batch_credits_every_member_and_each_moves_on(
    proj, ready, brief, fake_build
):
    ks = _verify(ready)
    r = _dispatch(proj, brief, ks)
    req = runs.read_request(proj, r)
    assert req["batch"] == {k: h for k, _, h in ks}
    assert req["head"] == ks[0][2]
    finish(proj, r, result=res("DONE", "GO", ks[0][2]))
    runs.apply_run(proj, r)
    for k, _, h in ks:
        assert slices.load(proj, k)["evidence"][h]["t1"]["verdict"] == "GO"
        assert slices.move(proj, k, "REVIEW", "t1 green")["phase"] == "REVIEW"


def test_member_that_moved_on_gets_nothing_others_still_get_go(
    proj, ready, brief, fake_build
):
    ks = _verify(ready)
    r = _dispatch(proj, brief, ks)
    slices.set_fields(proj, ks[1][0], head="f" * 40)
    finish(proj, r, result=res("DONE", "GO", ks[0][2]))
    runs.apply_run(proj, r)
    assert _t1(proj, ks[0][0])["verdict"] == "GO"
    assert _t1(proj, ks[1][0]) is None
    assert "f" * 40 not in slices.load(proj, ks[1][0])["evidence"]
    assert slices.load(proj, ks[1][0])["evidence"].get(ks[1][2], {}).get("t1") is None
    assert _t1(proj, ks[2][0])["verdict"] == "GO"


@pytest.mark.parametrize("no_result", [False, True])
def test_red_batch_records_nothing_and_never_parks(
    proj, ready, brief, fake_build, home, no_result
):
    ks = _verify(ready, 2)
    for _ in range(3):
        _red(proj, brief, ks, no_result=no_result)
    for k, _, h in ks:
        s = slices.load(proj, k)
        assert s["evidence"].get(h, {}).get("t1") is None
        assert s["failures"] == {} and s["phase"] == "VERIFY" and s["run"] is None
    lines = (home / "p" / "events.jsonl").read_text().splitlines()
    red = [x for x in lines if '"batch-red"' in x]
    assert len(red) == 3 and all(ks[1][0] in x for x in red)


def test_red_batch_frees_the_slot(proj, ready, brief, fake_build):
    ks = _verify(ready, 2)
    r = _red(proj, brief, ks)
    assert r not in [rid for rid, _ in runs.live_runs(proj)]


def test_red_run_of_a_single_member_batch_is_a_plain_no_go(proj, ready, brief):
    key, _, head = ready("VERIFY")
    r = go(proj, brief, "test", key, level="t1")
    rd = store.run_dir(proj, r)
    req = store.read_json(rd / "request.json")
    req["batch"] = {key: head}
    os.chmod(rd / "request.json", 0o644)
    store.write_json(rd / "request.json", req)
    store.register_request(proj, r)
    finish(proj, r, code=1, result=res("DONE", "NO-GO", head))
    runs.apply_run(proj, r)
    assert _t1(proj, key)["verdict"] == "NO-GO"


def test_infra_batch_run_touches_only_the_lead(proj, ready, brief, fake_build):
    ks = _verify(ready, 2)
    r = _dispatch(proj, brief, ks)
    finish(proj, r, code=124, reason="exit", result=res("FAILED", "", ks[0][2]))
    runs.apply_run(proj, r)
    for k, _, h in ks:
        assert slices.load(proj, k)["evidence"].get(h, {}).get("t1") is None
        assert slices.load(proj, k)["failures"] == {}


# --- evidence -------------------------------------------------------------------


def test_member_evidence_needs_the_frozen_head_to_be_current(
    proj, ready, brief, fake_build
):
    ks = _verify(ready, 2)
    r = _dispatch(proj, brief, ks)
    finish(proj, r, result=res("DONE", "GO", ks[0][2]))
    runs.apply_run(proj, r)
    member = ks[1][0]
    assert slices.move(proj, member, "REVIEW", "ok")["phase"] == "REVIEW"
    # The same entry on a head the batch never froze is refused.
    s = slices.load(proj, member)
    entry = s["evidence"][ks[1][2]]["t1"]
    s2 = {**s, "head": "other"}
    from devloop import evidence

    problem, _ = evidence._verify_run(
        proj, s2, entry, kinds=("test",), expect={"level": "t1"}, batch_ok=True
    )
    assert "different head" in problem


def test_live_batch_run_is_outstanding_for_each_member(proj, ready, brief, fake_build):
    from devloop import evidence

    ks = _verify(ready, 2)
    r = _dispatch(proj, brief, ks)
    s = slices.load(proj, ks[1][0])
    assert evidence._outstanding(proj, s) == [r]


# --- refusals -------------------------------------------------------------------


def test_refuses_each_bad_batch(proj, ready, brief, fake_build, mk_slice, cfg_edit):
    cfg_edit(lambda c: c["tests"].update(t2={"argv": ["x"]}))
    (a, _, _), (b, _, _), (c, _, _) = _verify(ready)
    with pytest.raises(runs.Refused, match="only a t1"):
        go(proj, brief, "test", a, level="t2", batch=[b])
    with pytest.raises(runs.Refused, match="--select"):
        go(proj, brief, "test", a, level="t1", batch=[b], select=["x"])
    with pytest.raises(runs.Refused, match=f"{b} appears|twice"):
        go(proj, brief, "test", a, level="t1", batch=[b, b])
    with pytest.raises(runs.Refused, match="twice"):
        go(proj, brief, "test", a, level="t1", batch=[a])
    extra = [ready("VERIFY")[0] for _ in range(4)]
    with pytest.raises(runs.Refused, match="at most 4"):
        go(proj, brief, "test", a, level="t1", batch=[b, c, *extra[:2]])
    nohead = mk_slice(proj, phase="VERIFY", worktree=str(ready()[1]))
    with pytest.raises(runs.Refused, match=f"{nohead} has no head"):
        go(proj, brief, "test", a, level="t1", batch=[nohead])
    dev = ready("DEVELOP")[0]
    with pytest.raises(runs.Refused, match=f"{dev} is not in VERIFY"):
        go(proj, brief, "test", a, level="t1", batch=[dev])
    cfg_edit(lambda c: c["repos"].append({"name": "o", "path": "/x", "slug": "o/o"}))
    other = ready("VERIFY")[0]
    s = slices.load(proj, other)
    s["repo"] = "o"
    slices._save(proj, s)
    with pytest.raises(runs.Refused, match=f"{other} is in another repo"):
        go(proj, brief, "test", a, level="t1", batch=[other])
    with pytest.raises(runs.Refused, match="not a slice"):
        go(proj, brief, "test", a, level="t1", batch=["NOPE--S1"])


def test_refuses_a_member_with_a_live_test_run(proj, ready, brief, fake_build):
    (a, _, _), (b, _, _), (c, _, _) = _verify(ready)
    go(proj, brief, "test", b, level="t1")
    with pytest.raises(runs.Refused, match=f"{b} already has a live test run"):
        go(proj, brief, "test", a, level="t1", batch=[b])
    r = go(proj, brief, "test", a, level="t1", batch=[c])
    with pytest.raises(runs.Refused, match=f"{c} already has a live test run"):
        go(proj, brief, "test", b, level="t1", batch=[c])
    assert r


def test_batch_run_uses_one_tester_slot(proj, ready, brief, fake_build, cfg_edit):
    cfg_edit(lambda c: c["capacity"].update(tester=1))
    ks = _verify(ready, 3)
    _dispatch(proj, brief, ks)
    assert len(runs.live_runs(proj)) == 1


# --- real git -------------------------------------------------------------------


@pytest.fixture
def fx(tmp_path):
    return Fix(tmp_path / "git")


@pytest.fixture
def gitproj(proj, fx, cfg_edit):
    cfg_edit(
        lambda c: (
            c["repos"][0].update(path=str(fx.path)),
            c["tests"].update(
                t1={"argv": ["decoy-test", "t1", "{worktree}", "{head}", "{label}"]}
            ),
        )
    )
    return proj


def _slice(proj, mk_slice, fx, branch, row):
    return mk_slice(
        proj,
        phase="VERIFY",
        risk="R1",
        worktree=str(fx.path),
        branch=branch,
        head=fx.sha(branch),
        plan_path="plan.md",
    )


@pytest.mark.real_git
def test_batch_commit_merges_every_head_onto_the_base(
    gitproj, fx, mk_slice, brief, tmp_path
):
    heads = {}
    for name in ("feat-a", "feat-b", "feat-c"):
        fx.branch(name)
        fx.write(f"{name}.txt", name)
        heads[name] = fx.commit(name)
    fx.checkout("main")
    ks = [_slice(gitproj, mk_slice, fx, n, i) for i, n in enumerate(heads)]
    r = go(gitproj, brief, "test", ks[0], level="t1", batch=ks[1:])
    req = runs.read_request(gitproj, r)
    commit = req["batch_commit"]
    parents = fgit(fx.path, "rev-list", "--parents", "-n1", commit).split()[1:]
    assert parents == [fx.sha("main"), *(req["batch"][k] for k in sorted(req["batch"]))]
    assert sorted(req["batch"].values()) == sorted(heads.values())
    assert req["head"] == req["batch"][ks[0]]
    files = fgit(fx.path, "ls-tree", "-r", "--name-only", commit).split()
    assert {"feat-a.txt", "feat-b.txt", "feat-c.txt", "base.txt"} <= set(files)
    # The tree is the clean merge of all three.
    fgit(fx.path, "checkout", "-q", "--detach", "main")
    fgit(fx.path, "merge", "-q", "--no-edit", *heads.values())
    assert fgit(fx.path, "rev-parse", f"{commit}^{{tree}}") == fgit(
        fx.path, "rev-parse", "HEAD^{tree}"
    )
    tree = req["tree"]
    assert fgit(tree, "rev-parse", "HEAD") == commit
    assert req["argv"][3] == commit
    assert fgit(fx.path, "rev-parse", f"refs/devloop/batch/{r}") == commit
    runs.remove_tree(gitproj, r, req)
    assert fgit(fx.path, "for-each-ref", "refs/devloop/batch") == ""


@pytest.mark.real_git
def test_conflicting_member_is_named(gitproj, fx, mk_slice, brief):
    fx.branch("feat-a")
    fx.write("same.txt", "one\n")
    fx.commit("a")
    fx.branch("feat-b")
    fx.write("b.txt", "b")
    fx.commit("b")
    fx.branch("feat-c")
    fx.write("same.txt", "two\n")
    fx.commit("c")
    fx.checkout("main")
    a, b, c = (
        _slice(gitproj, mk_slice, fx, n, i)
        for i, n in enumerate(("feat-a", "feat-b", "feat-c"))
    )
    with pytest.raises(runs.Refused, match=f"{c} conflicts"):
        go(gitproj, brief, "test", a, level="t1", batch=[b, c])
    assert list((store.state_dir(gitproj) / "runs").glob("test.*")) == []
