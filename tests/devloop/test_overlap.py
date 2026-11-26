"""The tick snapshot's Overlap section and the tester-slot resume list."""

import pytest
from gitfix import Fix

from devloop import slices, snapshot


@pytest.fixture
def fx(tmp_path, proj, cfg_edit):
    f = Fix(tmp_path)
    cfg_edit(lambda c: c["repos"][0].update(path=str(f.path), base="main"))
    return f


def _branch(fx, name, **files):
    fx.branch(name)
    for path, text in files.items():
        fx.write(path.replace("__", "/"), text)
    fx.commit(f"work on {name}")
    fx.checkout("main")


def _section(proj):
    md = snapshot.build_snapshot(proj)[0]
    return md.split("## Overlap\n", 1)[1].split("\n## ", 1)[0]


@pytest.mark.real_git
def test_two_rows_changing_one_file_are_listed(proj, mk_slice, fx):
    _branch(fx, "a", src__x_py="1", only_a="1")
    _branch(fx, "b", src__x_py="2", only_b="1")
    ka = mk_slice(proj, phase="DEVELOP", branch="a")
    kb = mk_slice(proj, phase="REVIEW", branch="b")
    out = _section(proj)
    assert f"{ka} and {kb}: src/x_py" in out
    assert "only_a" not in out


@pytest.mark.real_git
def test_disjoint_slices_are_not_listed(proj, mk_slice, fx):
    _branch(fx, "a", one="1")
    _branch(fx, "b", two="2")
    mk_slice(proj, phase="DEVELOP", branch="a")
    mk_slice(proj, phase="DEVELOP", branch="b")
    assert _section(proj).strip() == "- none"


@pytest.mark.real_git
def test_same_row_is_not_listed(proj, mk_slice, fx):
    _branch(fx, "a", shared="1")
    _branch(fx, "b", shared="2")
    for sid, branch in (("S1", "a"), ("S2", "b")):
        s = slices.new_slice(proj, "ROWX", sid, repo="main")
        s.update(phase="DEVELOP", branch=branch)
        slices._save(proj, s)
    assert _section(proj).strip() == "- none"


@pytest.mark.real_git
def test_done_slices_do_not_count_and_shared_paths_are_capped(proj, mk_slice, fx):
    many = {f"d__f{i}": "1" for i in range(7)}
    _branch(fx, "a", **many)
    _branch(fx, "b", **{k: "2" for k in many})
    _branch(fx, "c", **many)
    ka = mk_slice(proj, phase="DEVELOP", branch="a")
    kb = mk_slice(proj, phase="DEVELOP", branch="b")
    mk_slice(proj, phase="DONE", branch="c")
    out = _section(proj)
    assert f"{ka} and {kb}:" in out and "(+2 more)" in out
    assert out.count(" and ") == 1


def test_plan_placement_table_is_used_without_a_branch(proj, mk_slice, tmp_path):
    def plan(name, *paths):
        f = tmp_path / name
        rows = "\n".join(f"| `{p}` | new |" for p in paths)
        f.write_text(
            f"# Plan\n\n## Placement\n\n| File | Why |\n|---|---|\n{rows}\n\n## Other\n| `no/pe.py` |\n"
        )
        return str(f)

    ka = mk_slice(proj, phase="PLAN", plan_path=plan("a.md", "devloop/x.py", "a.py"))
    kb = mk_slice(proj, phase="PLAN", plan_path=plan("b.md", "devloop/x.py"))
    out = _section(proj)
    assert f"{ka} and {kb}: devloop/x.py" in out
    assert "no/pe.py" not in out


def test_unknown_when_no_branch_and_no_table(proj, mk_slice):
    ka = mk_slice(proj, phase="PLAN")
    out = _section(proj)
    assert f"files unknown: {ka}" in out


def test_slice_parked_for_a_tester_slot_is_listed_for_resume(proj, mk_slice):
    k = mk_slice(
        proj,
        phase="PARKED",
        parked_from="VERIFY",
        park_reason="no tester slot",
        waiting_on="tester slot",
    )
    other = mk_slice(
        proj,
        phase="PARKED",
        parked_from="VERIFY",
        park_reason="dep",
        waiting_on="row X",
    )
    owner = mk_slice(
        proj,
        phase="PARKED",
        parked_from="VERIFY",
        park_reason="failed-twice",
        waiting_on="tester slot",
    )
    md = snapshot.build_snapshot(proj)[0]
    block = md.split("## Parked for a tester slot, resume now\n", 1)[1].split(
        "\n## ", 1
    )[0]
    assert f"slice move {k} VERIFY" in block
    assert other not in block and owner not in block
