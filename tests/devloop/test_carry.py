"""Evidence survives a main-only merge into the head, and nothing else."""

import json

import pytest
from gitfix import Fix, git

from devloop import carry, evidence, slices, store

pytestmark = pytest.mark.real_git


@pytest.fixture
def fx(tmp_path):
    return Fix(tmp_path)


def _feature(fx):
    """A slice branch with one commit that changes `own.txt`."""
    fx.branch("feat")
    fx.write("own.txt", "x")
    return fx.commit("slice work")


# --- the git decision ---------------------------------------------------------


def test_disjoint_main_merge_carries(fx):
    old = _feature(fx)
    fx.advance_main("other.txt")
    new = fx.merge_main()
    assert carry.carry_reason(fx.path, "main", old, new) is None


def test_main_touching_a_slice_file_does_not_carry(fx):
    fx.branch("feat")
    fx.edit_top()
    old = fx.commit("slice edits shared.txt")
    fx.advance_main_bottom()
    new = fx.merge_main()
    assert "slice file" in carry.carry_reason(fx.path, "main", old, new)


def test_merge_with_an_extra_edit_does_not_carry(fx):
    old = _feature(fx)
    fx.advance_main("other.txt")
    git(fx.path, "merge", "-q", "--no-edit", "--no-commit", "main")
    fx.write("sneaky.txt", "not part of either side")
    git(fx.path, "add", "-A")
    git(fx.path, "commit", "-q", "--no-edit")
    new = fx.sha()
    assert "more than a clean merge" in carry.carry_reason(fx.path, "main", old, new)


def test_non_merge_head_change_does_not_carry(fx):
    old = _feature(fx)
    fx.write("more.txt", "y")
    new = fx.commit("more work")
    assert "not a merge" in carry.carry_reason(fx.path, "main", old, new)


def test_second_parent_off_base_does_not_carry(fx):
    old = _feature(fx)
    fx.branch("side", "main")
    fx.write("side.txt", "s")
    fx.commit("side work")
    fx.checkout("feat")
    git(fx.path, "merge", "-q", "--no-edit", "side")
    new = fx.sha()
    assert "not on the base" in carry.carry_reason(fx.path, "main", old, new)


def test_unpushed_main_commit_is_not_base(fx):
    old = _feature(fx)
    fx.checkout("main")
    fx.write("local.txt", "l")
    fx.commit("local main, never pushed")
    fx.checkout("feat")
    git(fx.path, "merge", "-q", "--no-edit", "main")
    new = fx.sha()
    assert carry.carry_reason(fx.path, "main", old, new) is not None


def test_first_parent_must_be_the_old_head(fx):
    _feature(fx)
    fx.advance_main("other.txt")
    old = fx.sha()
    fx.write("again.txt", "z")
    fx.commit("moves on")
    fx.advance_main("other2.txt")
    new = fx.merge_main()
    assert "not a merge" in carry.carry_reason(fx.path, "main", old, new)


# --- through set_fields -------------------------------------------------------


@pytest.fixture
def slice_on(proj, mk_slice, cfg_edit, fx, give):
    cfg_edit(lambda c: c["repos"][0].update(path=str(fx.path), base="main"))
    old = _feature(fx)
    k = mk_slice(proj, phase="SELF_CHECK", risk="R1", head=old)
    give(proj, k, "t1", "r-t1")
    give(proj, k, "code:codex", "r-rev")
    slices.selfcheck(proj, k, "GO", head=old)
    return k, old


def _gate_missing(proj, k):
    """Gate items still missing, leaving out the plan checks these tests do not set up."""
    return [m for m in evidence.validate_gate(proj, k) if "plan" not in m]


def _events(proj):
    path = store.state_dir(proj) / "events.jsonl"
    return (
        [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []
    )


def test_set_head_carries_evidence_and_records_event(proj, slice_on, fx):
    k, old = slice_on
    fx.advance_main("other.txt")
    new = fx.merge_main()
    s = slices.set_fields(proj, k, head=new)
    bucket = s["evidence"][new]
    assert set(bucket) == {"t1", "selfcheck", "code:codex"}
    assert all(e["note"] == f"carried-from {old}" for e in bucket.values())
    assert bucket["selfcheck"]["head"] == new
    ev = [e for e in _events(proj) if e["kind"] == "evidence-carried"]
    assert len(ev) == 1 and ev[0]["from"] == old and ev[0]["to"] == new
    # The carried evidence satisfies the gate checks on the new head.
    assert _gate_missing(proj, k) == []


def test_set_head_resets_when_main_touches_a_slice_file(
    proj, mk_slice, give, cfg_edit, fx
):
    cfg_edit(lambda c: c["repos"][0].update(path=str(fx.path), base="main"))
    fx.branch("feat")
    fx.edit_top()
    old = fx.commit("slice edits shared.txt")
    k = mk_slice(proj, phase="SELF_CHECK", risk="R1", head=old)
    give(proj, k, "t1", "r-t1")
    fx.advance_main_bottom()
    new = fx.merge_main()
    s = slices.set_fields(proj, k, head=new)
    assert new not in s["evidence"]
    assert not [e for e in _events(proj) if e["kind"] == "evidence-carried"]


def test_set_head_resets_for_a_plain_new_commit(proj, slice_on, fx):
    k, _old = slice_on
    fx.write("more.txt", "y")
    new = fx.commit("more")
    assert new not in slices.set_fields(proj, k, head=new)["evidence"]


def test_a_second_hop_keeps_the_original_run_binding(proj, slice_on, fx):
    k, old = slice_on
    fx.advance_main("o1.txt")
    mid = fx.merge_main()
    slices.set_fields(proj, k, head=mid)
    fx.advance_main("o2.txt")
    new = fx.merge_main()
    s = slices.set_fields(proj, k, head=new)
    assert s["evidence"][new]["t1"]["carried_from"] == old
    assert _gate_missing(proj, k) == []


def test_a_carried_entry_still_needs_its_run_to_match(proj, slice_on, fx):
    k, old = slice_on
    fx.advance_main("other.txt")
    new = fx.merge_main()
    slices.set_fields(proj, k, head=new)
    with slices._edit(proj, k) as s:
        s["evidence"][new]["t1"]["carried_from"] = "f" * 40
    assert any(m.startswith("t1 GO") for m in _gate_missing(proj, k))
