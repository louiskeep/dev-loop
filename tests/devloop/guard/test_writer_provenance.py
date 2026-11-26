"""One record binds the writer kind to the commit it wrote; readers trust nothing else."""

import pytest
from runs_support import brief, config, env, finish, go, ready, res  # noqa: F401

from devloop import evidence, runs, slices, store
from devloop import project as projmod


def gate(proj, key):
    return slices.exit_checklist(proj, key, "GATE")


def code_labels(proj, key):
    return [n for n, _ in gate(proj, key) if n.startswith("code review")]


def unmet(proj, key):
    return [n for n, ok in gate(proj, key) if not ok]


def set_head(proj, key, head):
    s = slices.load(proj, key)
    s["head"] = head
    slices._save(proj, s)


def write_head(proj, brief, key, head, kind="builder"):
    extra = {"reviewer": "codex"} if kind == "codex-builder" else {}
    r = go(proj, brief, kind, key, **extra)
    finish(proj, r, result=res("DONE", head=head))
    runs.apply_run(proj, r)
    return r


@pytest.mark.parametrize(
    "kind,phase", [("builder", "DEVELOP"), ("codex-builder", "REMEDIATE")]
)
def test_apply_run_binds_kind_commit_and_lineage(proj, ready, brief, kind, phase):
    key, _, head = ready(phase, head_writer=None, round=4)
    write_head(proj, brief, key, head, kind)
    assert slices.load(proj, key)["head_writer"] == {
        "kind": kind,
        "sha": head,
        "plan_lineage": 0,
    }


def test_codex_written_head_needs_dennis_first_after_a_real_apply(proj, ready, brief):
    key, _, head = ready("REMEDIATE", risk="R1", head_writer=None, round=4)
    write_head(proj, brief, key, head, "codex-builder")
    s = slices.load(proj, key)
    s["phase"] = "REVIEW"
    slices._save(proj, s)
    assert code_labels(proj, key)[0] == "code review dennis GO on head"


def test_head_moved_without_a_writer_run_fails_closed(proj, ready, brief):
    key, _, head = ready("DEVELOP", risk="R1", head_writer=None)
    write_head(proj, brief, key, head, "builder")
    s = slices.load(proj, key)
    s["phase"] = "REVIEW"
    slices._save(proj, s)
    assert code_labels(proj, key) == ["code review codex GO on head"]
    set_head(proj, key, "f" * 40)
    assert code_labels(proj, key) == [
        "code review dennis GO on head",
        "code review codex GO on head",
    ]
    assert "an applied builder run wrote this head" in unmet(proj, key)


def test_legacy_string_without_a_run_is_an_unmet_item(proj, ready):
    key, _, head = ready("REVIEW", risk="R1", head_writer="codex-builder")
    assert "an applied builder run wrote this head" in unmet(proj, key)
    assert code_labels(proj, key)[0] == "code review dennis GO on head"


def test_legacy_slice_recovers_the_writer_from_its_applied_run(proj, ready, mk_run):
    key, _, head = ready("REVIEW", risk="R1", head_writer=None)
    mk_run(proj, "cb1", kind="codex-builder", key=key, head=head)
    assert "an applied builder run wrote this head" not in unmet(proj, key)
    assert code_labels(proj, key)[0] == "code review dennis GO on head"


def test_legacy_slice_recovers_a_claude_writer_too(proj, ready, mk_run):
    key, _, head = ready("REVIEW", risk="R1", head_writer="codex-builder")
    mk_run(proj, "b1", kind="builder", key=key, head=head)
    assert code_labels(proj, key) == ["code review codex GO on head"]


def test_builder_evidence_reads_the_same_record(proj, ready):
    key, _, head = ready("DEVELOP", risk="R1", head_writer=None)
    done = dict(slices.exit_checklist(proj, key))
    assert done["applied builder run DONE on head"] is False
    s = slices.load(proj, key)
    s["head_writer"] = slices.writer_record("builder", head)
    slices._save(proj, s)
    assert (
        dict(slices.exit_checklist(proj, key))["applied builder run DONE on head"]
        is True
    )


# --- the Senior seat takes the dennis kind only --------------------------------


@pytest.fixture
def dual(cfg_edit):
    def edit(c):
        c["reviewers"]["dennis"]["kinds"] = ["dennis", "codex-review"]
        c["reviewers"]["dennis"]["argv"] = {
            "codex-review": ["codex-review", "{worktree}", "{brief}", "{out}"]
        }

    cfg_edit(edit)


def test_dual_kind_senior_cannot_run_codex_review_on_a_codex_head(
    proj, ready, brief, dual
):
    key, _, head = ready("REVIEW", risk="R1", head_writer=None)
    s = slices.load(proj, key)
    s["head_writer"] = slices.writer_record("codex-builder", head)
    slices._save(proj, s)
    with pytest.raises(runs.Refused, match="Senior seat"):
        go(proj, brief, "codex-review", key, reviewer="dennis")
    assert go(proj, brief, "dennis", key, reviewer="dennis")


def test_dual_kind_codex_go_does_not_satisfy_the_senior_seat(proj, ready, dual, give):
    key, _, head = ready("REVIEW", risk="R1", head_writer=None)
    s = slices.load(proj, key)
    s["head_writer"] = slices.writer_record("codex-builder", head)
    slices._save(proj, s)
    d = give(proj, key, "code:dennis", "d1", run_kind="codex-review")
    give(proj, key, "code:codex", "c1", pred=d)
    assert any(
        n.startswith("code review dennis") and not ok for n, ok in gate(proj, key)
    )


def test_dual_kind_senior_may_run_codex_review_on_a_claude_head(
    proj, ready, brief, dual
):
    key, _, head = ready("REVIEW", risk="R2")
    assert go(proj, brief, "codex-review", key, reviewer="dennis")


def test_a_failed_run_with_a_done_header_is_not_credited(proj, ready, mk_run):
    key, _, head = ready("REVIEW", risk="R1", head_writer=None)
    d = mk_run(proj, "b1", kind="builder", key=key, head=head)
    store.write_json(d / "exit", {"code": 1, "reason": "exit", "ended_at": "t"})
    assert evidence.head_writer(proj, slices.load(proj, key)) is None
    assert "an applied builder run wrote this head" in unmet(proj, key)
    assert code_labels(proj, key)[0] == "code review dennis GO on head"


def builder_done(proj, key):
    return dict(slices.exit_checklist(proj, key))["applied builder run DONE on head"]


# --- an import allowance belongs to the imported commit ------------------------


def imported(proj, tmp_path, head="a" * 40):
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: x\n")
    return slices.import_slice(
        proj,
        "IMP",
        "S1",
        repo="main",
        risk="R1",
        rulebooks=["a.md"],
        worktree=str(tmp_path),
        branch="feat/x",
        pr=None,
        head=head,
        plan_path=str(plan),
        purpose="p",
        phase="DEVELOP",
        plan_approved=True,
    )["key"]


def test_the_imported_head_counts_as_claude_written(proj, tmp_path):
    key = imported(proj, tmp_path)
    assert evidence.head_writer(proj, slices.load(proj, key)) == "imported"
    assert builder_done(proj, key) is True


def test_moving_an_imported_head_restores_the_unmet_writer_item(proj, tmp_path):
    key = imported(proj, tmp_path)
    set_head(proj, key, "b" * 40)
    assert evidence.head_writer(proj, slices.load(proj, key)) is None
    assert builder_done(proj, key) is False


def test_a_new_plan_lineage_drops_the_import_allowance(proj, tmp_path):
    key = imported(proj, tmp_path)
    s = slices.load(proj, key)
    s["plan_lineage"] += 1
    slices._save(proj, s)
    assert evidence.head_writer(proj, slices.load(proj, key)) is None


# --- the Senior seat goes to a reviewer that may run dennis --------------------


def test_senior_seat_skips_a_dennis_kind_reviewer_that_cannot_run_dennis(
    proj, ready, brief, cfg_edit
):
    def edit(c):
        first = dict(c["reviewers"]["dennis"])
        first["kinds"] = ["codex-review"]
        first["argv"] = {
            "codex-review": ["codex-review", "{worktree}", "{brief}", "{out}"]
        }
        c["reviewers"] = {"dennis0": first, **c["reviewers"]}

    cfg_edit(edit)
    key, _, head = ready("REVIEW", risk="R1", head_writer=None)
    s = slices.load(proj, key)
    s["head_writer"] = slices.writer_record("codex-builder", head)
    slices._save(proj, s)
    cfg = projmod.load_project(proj)
    assert evidence.senior_slot(proj, cfg, s) == "dennis"
    assert go(proj, brief, "dennis", key, reviewer="dennis")


# --- one registry of writer kinds ------------------------------------------------


def test_push_confirmation_and_provenance_read_one_writer_registry(
    proj, tmp_path, monkeypatch
):
    from devloop import push, wrap
    from test_wrap import mk

    assert not hasattr(push, "WRITER_KINDS")
    assert all(runs.KINDS[k].writes_worktree for k in slices.WRITER_KINDS)
    pushed = []
    monkeypatch.setattr(
        wrap, "_push_writer", lambda rd, req: pushed.append(req["kind"])
    )
    monkeypatch.setattr(slices, "WRITER_KINDS", ("duty",))
    mk(proj, tmp_path, ["true"], kind="duty", reader="exit")
    wrap.run(proj, "r1")
    assert pushed == ["duty"]
