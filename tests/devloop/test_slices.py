import hashlib
import json
import multiprocessing
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import evidence, slices, store

STATE_BIN = str(Path(__file__).resolve().parents[2] / "bin" / "devloop-state")


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def force_phase(proj, key, phase):
    s = slices.load(proj, key)
    s["phase"] = phase
    slices._save(proj, s)


@pytest.fixture
def ready(proj, mk_slice, give, tmp_path):
    """A slice at REVIEW holding every piece of evidence its roster needs."""
    counter = iter(range(1, 100))

    def make(risk="R1", head="a1"):
        n = next(counter)
        plan = tmp_path / f"plan{n}.md"
        plan.write_text("Rules consulted: x\n")
        k = mk_slice(
            proj,
            phase="REVIEW",
            risk=risk,
            head=head,
            plan_path=str(plan),
            approved_plan_sha=sha_of(plan),
        )
        slices.record_tick_evidence(proj, k, "selfcheck", "GO", head=head)
        give(proj, k, "t1", f"t1-{n}")
        if risk == "R2":
            give(proj, k, "code:dennis", f"d-{n}")
            give(proj, k, "code:codex", f"c-{n}", pred=f"d-{n}")
        else:
            give(proj, k, "code:codex", f"c-{n}")
        return k

    return make


# --- transitions -------------------------------------------------------------


def test_bad_transition_leaves_file_unchanged(proj, mk_slice, raw):
    k = mk_slice(proj, phase="FRAME")
    before = raw(proj, k)
    with pytest.raises(slices.TransitionError):
        slices.move(proj, k, "GATE", "x")
    assert raw(proj, k) == before


def test_frame_exit_requires_risk_and_rulebooks(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk="R2", rulebooks=[])
    with pytest.raises(slices.TransitionError, match="rulebooks"):
        slices.move(proj, k, "PLAN", "x")
    k = mk_slice(proj, phase="FRAME", risk=None, rulebooks=["a.md"])
    with pytest.raises(slices.TransitionError, match="risk"):
        slices.move(proj, k, "PLAN", "x")


def test_forward_path_reaches_done(proj, tmp_path, mk_run, give):
    s = slices.new_slice(proj, "FWD", "S1", repo="main")
    k = s["key"]
    slices.set_fields(proj, k, risk="R1", rulebooks=["a.md"])
    slices.move(proj, k, "PLAN", "framed")
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: a.md\n")
    slices.set_fields(proj, k, plan_path=str(plan))
    give(proj, k, "plan:codex", "pr1")
    assert slices.move(proj, k, "DEVELOP", "plan ok")["approved_plan_sha"] == sha_of(
        plan
    )
    slices.set_fields(proj, k, branch="feat/fwd", head="a1")
    mk_run(proj, "b1", kind="builder", key=k, head="a0", result_head="a1")
    slices.move(proj, k, "SELF_CHECK", "built")
    slices.selfcheck(proj, k, "GO", head="a1")
    slices.move(proj, k, "VERIFY", "self-check read")
    give(proj, k, "t1", "t1r")
    slices.move(proj, k, "REVIEW", "t1 green")
    give(proj, k, "code:codex", "cr1")
    slices.move(proj, k, "GATE", "reviews green")
    with pytest.raises(slices.TransitionError, match="merge"):
        slices.move(proj, k, "DOCUMENT", "by hand")
    slices.finish_merge(proj, k, "merged")
    slices.docs(proj, k, "GO", head="a1")
    assert slices.move(proj, k, "DONE", "closed")["phase"] == "DONE"


def test_gate_refused_without_cross_model_go_on_head(proj, mk_slice, give):
    k = mk_slice(proj, phase="REVIEW", risk="R2", head="a1")
    give(proj, k, "code:dennis", "r1")
    with pytest.raises(slices.TransitionError, match="codex"):
        slices.move(proj, k, "GATE", "x")


def test_ready_slice_reaches_gate(proj, ready):
    assert slices.move(proj, ready("R2"), "GATE", "x")["phase"] == "GATE"
    assert slices.move(proj, ready("R1"), "GATE", "x")["phase"] == "GATE"


def test_new_head_clears_evidence(proj, ready):
    k = ready("R1")
    slices.set_fields(proj, k, head="b2")
    with pytest.raises(slices.TransitionError, match="head"):
        slices.move(proj, k, "GATE", "x")


def test_rounds_count_and_never_park(proj, mk_slice):
    k = mk_slice(proj, phase="REVIEW")
    for n in range(1, 4):
        assert slices.move(proj, k, "REMEDIATE", "x")["round"] == n
        force_phase(proj, k, "REVIEW")


def test_rebase_at_gate_costs_no_round(proj, mk_slice):
    k = mk_slice(proj, phase="GATE", round=1)
    assert slices.move(proj, k, "SELF_CHECK", "rebased")["round"] == 1
    k = mk_slice(proj, phase="GATE", round=1)
    assert slices.move(proj, k, "REMEDIATE", "ci red")["round"] == 1


def test_risk_change_outside_frame_refused(proj, mk_slice):
    k = mk_slice(proj, phase="PLAN", risk="R1")
    with pytest.raises(slices.SliceError, match="risk"):
        slices.set_fields(proj, k, risk="R2")
    assert slices.load(proj, k)["risk"] == "R1"


def test_r3_held_until_owner_answer(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    with pytest.raises(slices.TransitionError, match="R3"):
        slices.move(proj, k, "PLAN", "x")
    with pytest.raises(slices.TransitionError, match="question"):
        slices.move(proj, k, "HELD", "x")
    slices.set_fields(proj, k, waiting_on="question q1")
    assert slices.move(proj, k, "HELD", "R3 waits")["phase"] == "HELD"
    with pytest.raises(slices.TransitionError, match="owner"):
        slices.move(proj, k, "FRAME", "x")
    s = slices.release_hold(proj, k)
    assert (s["phase"], s["risk"], s["waiting_on"]) == ("FRAME", "R2", None)
    assert slices.move(proj, k, "PLAN", "x")["phase"] == "PLAN"


def test_parked_resumes_only_to_recorded_phase(proj, mk_slice):
    k = mk_slice(proj, phase="VERIFY")
    s = slices.move(proj, k, "PARKED", "stuck")
    assert (s["parked_from"], s["park_reason"]) == ("VERIFY", "stuck")
    with pytest.raises(slices.TransitionError):
        slices.move(proj, k, "REVIEW", "x")
    s = slices.move(proj, k, "VERIFY", "resumed")
    assert (s["phase"], s["parked_from"], s["park_reason"]) == ("VERIFY", None, None)


def test_gate_to_document_only_through_merge(proj, ready):
    k = ready("R1")
    slices.move(proj, k, "GATE", "x")
    with pytest.raises(slices.TransitionError, match="merge"):
        slices.move(proj, k, "DOCUMENT", "x")
    assert slices.finish_merge(proj, k, "merged")["phase"] == "DOCUMENT"


def test_finish_merge_does_not_recheck_evidence_after_github_confirmed(proj, ready):
    k = ready("R1")
    slices.move(proj, k, "GATE", "x")
    slices.set_fields(proj, k, head="b2")  # evidence no longer matches the head
    assert slices.finish_merge(proj, k, "merged")["phase"] == "DOCUMENT"
    k2 = ready("R1")
    slices.move(proj, k2, "GATE", "x")
    slices.set_fields(proj, k2, head="b2")
    with pytest.raises(slices.TransitionError):  # a plain move still re-checks
        slices.move(proj, k2, "DOCUMENT", "x")


def test_move_back_to_frame_starts_a_new_lineage(proj, ready):
    k = ready("R1")
    s = slices.load(proj, k)
    s["round"] = 2
    s["plan_frozen"] = True
    slices._save(proj, s)
    s = slices.move(proj, k, "FRAME", "tier change")
    assert (s["round"], s["evidence"], s["approved_plan_sha"], s["plan_frozen"]) == (
        0,
        {},
        None,
        False,
    )
    slices.set_fields(proj, k, risk="R2")


def test_develop_exit_needs_an_applied_builder_run(proj, mk_slice, mk_run):
    k = mk_slice(proj, phase="DEVELOP", branch="feat/x", head="a1", head_writer=None)
    with pytest.raises(slices.TransitionError, match="builder"):
        slices.move(proj, k, "SELF_CHECK", "x")
    mk_run(proj, "b0", kind="builder", key=k, result_head="zz", applied=True)
    mk_run(proj, "b1", kind="builder", key=k, result_head="a1", applied=False)
    mk_run(proj, "b2", kind="builder", key=k, result_head="a1", status="FAILED")
    with pytest.raises(slices.TransitionError, match="builder"):
        slices.move(proj, k, "SELF_CHECK", "x")
    mk_run(proj, "b3", kind="builder", key=k, result_head="a1")
    assert slices.move(proj, k, "SELF_CHECK", "x")["phase"] == "SELF_CHECK"


def test_selfcheck_must_name_the_slice_head(proj, mk_slice):
    k = mk_slice(proj, phase="SELF_CHECK", head="a1")
    with pytest.raises(slices.SliceError, match="head"):
        slices.selfcheck(proj, k, "GO", head="b2")
    slices.selfcheck(proj, k, "GO", head="a1")
    assert slices.load(proj, k)["evidence"]["a1"]["selfcheck"]["source"] == "tick"
    with pytest.raises(slices.SliceError):
        slices.record_evidence(proj, k, "selfcheck", "GO", "r1", head="a1")


# --- plan review roster ------------------------------------------------------


@pytest.fixture
def r2_plan(proj, mk_slice, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: x\nv1\n")
    k = mk_slice(proj, phase="PLAN", risk="R2", rulebooks=["a.md"], plan_path=str(plan))
    return k, plan


def test_r2_plan_passes_after_two_dennis_rounds_and_codex_go(proj, r2_plan, give):
    k, plan = r2_plan
    give(proj, k, "plan:dennis", "d1", verdict="NO-GO")
    plan.write_text("Rules consulted: x\nv2\n")
    give(proj, k, "plan:dennis", "d2", verdict="NO-GO")
    plan.write_text("Rules consulted: x\nv3 repaired\n")
    with pytest.raises(slices.TransitionError, match="codex"):
        slices.move(proj, k, "DEVELOP", "x")
    give(proj, k, "plan:codex", "c1", pred="d2", run_kind="codex-fix")
    s = slices.move(proj, k, "DEVELOP", "x")
    assert s["approved_plan_sha"] == sha_of(plan)
    assert s["plan_rounds"][str(plan)]["dennis"] == 2


def test_r2_plan_refused_after_one_dennis_round(proj, r2_plan, give):
    k, plan = r2_plan
    give(proj, k, "plan:dennis", "d1", verdict="NO-GO")
    plan.write_text("Rules consulted: x\nv2\n")
    give(proj, k, "plan:codex", "c1", pred="d1")
    with pytest.raises(slices.TransitionError, match="dennis"):
        slices.move(proj, k, "DEVELOP", "x")


def test_r2_plan_passes_on_a_dennis_go(proj, r2_plan, give):
    k, _ = r2_plan
    give(proj, k, "plan:dennis", "d1")
    give(proj, k, "plan:codex", "c1", pred="d1")
    assert slices.move(proj, k, "DEVELOP", "x")["phase"] == "DEVELOP"


def test_r2_plan_refused_with_codex_go_on_old_sha(proj, r2_plan, give):
    k, plan = r2_plan
    give(proj, k, "plan:dennis", "d1")
    give(proj, k, "plan:codex", "c1", pred="d1")
    plan.write_text("Rules consulted: x\nedited after the review\n")
    with pytest.raises(slices.TransitionError, match="codex"):
        slices.move(proj, k, "DEVELOP", "x")


def test_late_codex_go_must_follow_the_satisfying_dennis_run(proj, r2_plan, give):
    k, _ = r2_plan
    give(proj, k, "plan:dennis", "d1")
    give(proj, k, "plan:codex", "c1", pred="someone-else")
    with pytest.raises(slices.TransitionError, match="predecessor"):
        slices.move(proj, k, "DEVELOP", "x")


def test_plan_file_must_name_rules_consulted(proj, r2_plan, give):
    k, plan = r2_plan
    plan.write_text("no rules named here\n")
    give(proj, k, "plan:dennis", "d1")
    give(proj, k, "plan:codex", "c1", pred="d1")
    with pytest.raises(slices.TransitionError, match="Rules consulted"):
        slices.move(proj, k, "DEVELOP", "x")


def test_plan_rounds_are_keyed_by_plan_path(proj, r2_plan, give, tmp_path):
    k, plan = r2_plan
    give(proj, k, "plan:dennis", "d1", verdict="NO-GO")
    slices.record_evidence(
        proj,
        k,
        "plan:dennis",
        "NO-GO",
        "d1",
        plan_sha=slices.plan_sha(slices.load(proj, k)),
    )
    assert slices.load(proj, k)["plan_rounds"][str(plan)] == {"dennis": 1}
    slices.move(proj, k, "FRAME", "restart")
    other = tmp_path / "other.md"
    other.write_text("Rules consulted: y\n")
    slices.set_fields(proj, k, plan_path=str(other))
    assert slices.load(proj, k)["plan_rounds"] == {}


# --- gate evidence is re-read from the run dirs -------------------------------


@pytest.mark.parametrize(
    "tamper",
    [
        "head_mismatch",
        "not_applied",
        "wrong_kind",
        "wrong_reviewer",
        "wrong_purpose",
        "verdict_differs",
        "other_slice",
        "missing_run",
        "predecessor",
    ],
)
def test_gate_rejects_forged_or_stale_run_evidence(proj, ready, tamper):
    k = ready("R2")
    ev = slices.load(proj, k)["evidence"]["a1"]["code:codex"]
    d = store.run_dir(proj, ev["run"])
    req = store.read_json(d / "request.json")
    if tamper == "head_mismatch":
        req["head"] = "ff"
    elif tamper == "not_applied":
        (d / "applied").unlink()
    elif tamper == "wrong_kind":
        req["kind"] = "builder"
    elif tamper == "wrong_reviewer":
        req["reviewer"] = "dennis"
    elif tamper == "wrong_purpose":
        req["purpose"] = "plan"
    elif tamper == "verdict_differs":
        (store.out_dir(d) / "result.md").write_text(
            "status: DONE\nverdict: NO-GO\n---\n"
        )
    elif tamper == "other_slice":
        req["row"] = "ELSEWHERE"
    elif tamper == "missing_run":
        (d / "request.json").unlink()
    elif tamper == "predecessor":
        req["predecessor_run"] = None
    if (d / "request.json").exists():
        store.write_json(d / "request.json", req)
    with pytest.raises(slices.TransitionError, match="codex"):
        slices.move(proj, k, "GATE", "x")


def test_gate_needs_t2_and_full_when_required(proj, ready, cfg_edit, give):
    cfg_edit(lambda c: c.update(tests={"t2": {"argv": ["x"]}}))
    k = ready("R1")
    slices.set_fields(proj, k, full_suite_required=True)
    with pytest.raises(slices.TransitionError, match="t2.*full|full.*t2"):
        slices.move(proj, k, "GATE", "x")
    give(proj, k, "t2", "t2r")
    with pytest.raises(slices.TransitionError, match="full"):
        slices.move(proj, k, "GATE", "x")
    give(proj, k, "full", "fr")
    assert slices.move(proj, k, "GATE", "x")["phase"] == "GATE"


def test_full_run_covers_stacked_members(proj, ready, mk_run):
    k = ready("R1")
    slices.set_fields(proj, k, full_suite_required=True)
    top = ready("R1", head="b2")
    mk_run(
        proj, "topfull", kind="test", key=top, head="b2", level="full", covers={k: "a1"}
    )
    slices.record_evidence(proj, k, "full", "GO", "topfull", head="a1")
    assert slices.move(proj, k, "GATE", "x")["phase"] == "GATE"


def test_gate_refused_when_approved_plan_changed(proj, ready, tmp_path):
    k = ready("R1")
    Path(slices.load(proj, k)["plan_path"]).write_text("Rules consulted: x\nchanged\n")
    with pytest.raises(slices.TransitionError, match="plan"):
        slices.move(proj, k, "GATE", "x")


def test_exit_checklist_matches_move(proj, mk_slice, tmp_path, ready):
    plan = tmp_path / "bare.md"
    plan.write_text("nothing\n")
    cases = [
        (mk_slice(proj, phase="FRAME"), "PLAN"),
        (mk_slice(proj, phase="PLAN", risk="R2", plan_path=str(plan)), "DEVELOP"),
        (mk_slice(proj, phase="DEVELOP"), "SELF_CHECK"),
        (mk_slice(proj, phase="SELF_CHECK", head="a1"), "VERIFY"),
        (mk_slice(proj, phase="VERIFY", head="a1"), "REVIEW"),
        (mk_slice(proj, phase="REVIEW", risk="R2", head="a1"), "GATE"),
        (mk_slice(proj, phase="DOCUMENT", head="a1"), "DONE"),
    ]
    for k, to in cases:
        missing = [n for n, ok in slices.exit_checklist(proj, k) if not ok]
        assert missing, k
        assert slices.exit_checklist(proj, k, to) == slices.exit_checklist(proj, k)
        with pytest.raises(slices.TransitionError) as exc:
            slices.move(proj, k, to, "x")
        assert all(name in str(exc.value) for name in missing)
    good = ready("R1")
    assert all(ok for _, ok in slices.exit_checklist(proj, good))
    assert slices.validate_gate(proj, good) == []


def test_review_checklist_shows_gate_items_while_remediate_stays_open(proj, mk_slice):
    k = mk_slice(proj, phase="REVIEW", risk="R1", head="a1")
    assert any(
        "code review codex" in n for n, ok in slices.exit_checklist(proj, k) if not ok
    )
    assert slices.exit_checklist(proj, k, "REMEDIATE") == []
    assert slices.move(proj, k, "REMEDIATE", "x")["phase"] == "REMEDIATE"


# --- fields ------------------------------------------------------------------


def test_set_fields_rules(proj, mk_slice):
    k = mk_slice(proj, phase="PLAN", risk="R1")
    with pytest.raises(slices.SliceError, match="unknown"):
        slices.set_fields(proj, k, phase="DONE")
    slices.set_fields(proj, k, full_suite_required=True)
    with pytest.raises(slices.SliceError, match="full_suite_required"):
        slices.set_fields(proj, k, full_suite_required=False)
    slices.set_fields(proj, k, plan_path="plan.md")
    slices.mark_plan_started(proj, k)
    with pytest.raises(slices.SliceError, match="plan_path"):
        slices.set_fields(proj, k, plan_path="other.md")
    done = mk_slice(proj, phase="DONE")
    with pytest.raises(slices.SliceError, match="DONE"):
        slices.set_fields(proj, done, next_action="x")


@pytest.mark.parametrize(
    "field, value",
    [
        ("pr", True),
        ("pr", "5"),
        ("pr", 5.0),
        ("head", "A1"),
        ("head", "a1\n"),
        ("head", ""),
        ("branch", "bad branch"),
        ("branch", "feat/x\n"),
        ("risk", "r1"),
        ("risk", "R1\n"),
        ("rulebooks", "a.md"),
        ("rulebooks", [1]),
        ("full_suite_required", "yes"),
        ("full_suite_required", 1),
        ("waiting_on", 5),
    ],
)
def test_set_fields_refuses_wrong_types_and_unanchored_values(
    proj, mk_slice, field, value
):
    k = mk_slice(proj, phase="FRAME")
    with pytest.raises(slices.SliceError):
        slices.set_fields(proj, k, **{field: value})


@pytest.mark.parametrize(
    "field, value",
    [
        ("round", True),
        ("round", "1"),
        ("round", -1),
        ("pr", "5"),
        ("full_suite_required", 1),
        ("phase", "NOPE"),
        ("phase", "FRAME\n"),
        ("risk", "R9"),
        ("rulebooks", "a.md"),
        ("evidence", {"a1": {"t1": {"verdict": "MAYBE", "run": "r", "at": "t"}}}),
        ("evidence", {"a1": {"free text": {"verdict": "GO", "run": "r", "at": "t"}}}),
        ("schema_version", 2),
        ("schema_version", None),
    ],
)
def test_reader_refuses_corrupt_slice_files(proj, mk_slice, field, value):
    k = mk_slice(proj)
    path = slices.slice_path(proj, k)
    data = json.loads(path.read_text())
    if value is None:
        data.pop(field)
    else:
        data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(slices.SliceError) as exc:
        slices.load(proj, k)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_reader_tolerates_extra_fields(proj, mk_slice):
    k = mk_slice(proj)
    path = slices.slice_path(proj, k)
    data = json.loads(path.read_text())
    data["added_in_a_later_minor"] = {"x": 1}
    path.write_text(json.dumps(data))
    assert slices.load(proj, k)["key"] == k


@pytest.mark.parametrize(
    "row, sid",
    [("A\n", "S1"), ("A", "S1\n"), ("A--B", "S1"), ("../x", "S1"), ("", "S1")],
)
def test_ids_are_anchored(proj, row, sid):
    with pytest.raises(slices.SliceError):
        slices.new_slice(proj, row, sid, repo="main")


def test_many_slices_of_a_row_may_be_open(proj):
    slices.new_slice(proj, "R9", "S1", repo="main")
    slices.new_slice(proj, "R9", "S2", repo="main")
    slices.move(proj, "R9--S1", "PARKED", "stuck")
    assert slices.new_slice(proj, "R9", "S3", repo="main")["phase"] == "FRAME"
    with pytest.raises(slices.SliceError, match="already exists"):
        slices.new_slice(proj, "R9", "S2", repo="main")
    with pytest.raises(slices.SliceError, match="repo"):
        slices.new_slice(proj, "R8", "S1", repo="nope")


def test_new_slice_opens_after_done(proj, mk_slice):
    k = mk_slice(proj, phase="DONE")
    row = slices.load(proj, k)["row"]
    assert slices.new_slice(proj, row, "S2", repo="main")["phase"] == "FRAME"


def test_import_refused_headless_and_capped_at_review(
    proj, tmp_path, monkeypatch, fakebin
):
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: x\n")
    kw = dict(
        repo="main",
        risk="R2",
        rulebooks=["a.md"],
        worktree=str(tmp_path / "wt"),
        branch="feat/x",
        pr=7,
        head="a" * 40,
        plan_path=str(plan),
        purpose="p",
    )
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        slices.import_slice(proj, "I1", "S1", phase="GATE", **kw)
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    s = slices.import_slice(proj, "I1", "S1", phase="GATE", plan_approved=True, **kw)
    assert (s["phase"], s["approved_plan_sha"], s["imported"]) == (
        "REVIEW",
        sha_of(plan),
        True,
    )
    assert (
        slices.import_slice(proj, "I2", "S1", phase="REVIEW", **kw)["phase"] == "PLAN"
    )
    monkeypatch.setenv("FAKE_GH_STATE", "MERGED")
    assert (
        slices.import_slice(proj, "I3", "S1", phase="GATE", **kw)["phase"] == "DOCUMENT"
    )
    monkeypatch.setenv("FAKE_GH_BASE", "other")
    assert slices.import_slice(proj, "I4", "S1", phase="GATE", **kw)["phase"] == "PLAN"
    monkeypatch.setenv("FAKE_GH_BASE", "main")
    monkeypatch.setenv("FAKE_GH_HEAD", "c" * 40)
    assert slices.import_slice(proj, "I5", "S1", phase="GATE", **kw)["phase"] == "PLAN"


def test_imported_slice_skips_the_builder_run_check(proj, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: x\n")
    s = slices.import_slice(
        proj,
        "I6",
        "S1",
        repo="main",
        risk="R1",
        rulebooks=["a.md"],
        worktree=str(tmp_path),
        branch="feat/x",
        pr=None,
        head="a1",
        plan_path=str(plan),
        purpose="p",
        phase="DEVELOP",
        plan_approved=True,
    )
    assert (
        slices.move(proj, s["key"], "SELF_CHECK", "imported")["phase"] == "SELF_CHECK"
    )


# --- board mirror ------------------------------------------------------------


def _board(tmp_path, body):
    script = tmp_path / "board"
    script.write_text("#!/usr/bin/env python3\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_board_mirror_calls_cli_only(proj, mk_slice, cfg_edit, tmp_path, home):
    log = tmp_path / "board.log"
    script = _board(
        tmp_path,
        f"import json, sys\nopen({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
    )
    phase_map = {"FRAME": "planning", "PLAN": "planning"}
    cfg_edit(lambda c: c.update(board={"cmd": [str(script)], "phase_map": phase_map}))
    k = mk_slice(proj, risk="R1", rulebooks=["a.md"])
    slices.move(proj, k, "PLAN", "x")
    assert json.loads(log.read_text().splitlines()[-1]) == [
        "slice",
        "ROW1",
        "S1",
        "planning",
    ]
    assert not list(home.rglob("rows.json"))
    slices.row_close(proj, "ROW1")
    assert json.loads(log.read_text().splitlines()[-1]) == ["close", "ROW1"]


@pytest.mark.parametrize("body", ["raise SystemExit(1)\n", None])
def test_board_mirror_failure_does_not_fail_the_move(
    proj, mk_slice, cfg_edit, tmp_path, home, body
):
    cmd = [str(_board(tmp_path, body))] if body else [str(tmp_path / "absent-board")]
    cfg_edit(lambda c: c.update(board={"cmd": cmd, "phase_map": {"PLAN": "planning"}}))
    k = mk_slice(proj, risk="R1", rulebooks=["a.md"])
    assert slices.move(proj, k, "PLAN", "x")["phase"] == "PLAN"
    assert "board-mirror-failed" in (home / "p" / "events.jsonl").read_text()


def test_move_appends_event_and_history(proj, mk_slice, home):
    k = mk_slice(proj, risk="R1", rulebooks=["a.md"])
    s = slices.move(proj, k, "PLAN", "framed")
    assert s["history"][-1]["to"] == "PLAN" and s["last_moved_at"]
    events = [
        json.loads(x) for x in (home / "p" / "events.jsonl").read_text().splitlines()
    ]
    assert events[-1]["kind"] == "slice-moved" and events[-1]["to"] == "PLAN"


# --- concurrency and the CLI --------------------------------------------------


def _setter(proj, key, barrier, field, i):
    barrier.wait()
    slices.set_fields(proj, key, **{field: f"v{i}"})


def test_concurrent_set_no_lost_update(proj, mk_slice):
    ctx = multiprocessing.get_context("fork")
    for i in range(50):
        k = mk_slice(proj)
        barrier = ctx.Barrier(2)
        procs = [
            ctx.Process(target=_setter, args=(proj, k, barrier, "next_action", i)),
            ctx.Process(target=_setter, args=(proj, k, barrier, "waiting_on", i)),
        ]
        [p.start() for p in procs]
        [p.join() for p in procs]
        assert [p.exitcode for p in procs] == [0, 0]
        s = slices.load(proj, k)
        assert (s["next_action"], s["waiting_on"]) == (f"v{i}", f"v{i}")


def run_cli(home, *args, headless=False):
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    env.pop("DEVLOOP_HEADLESS", None)
    if headless:
        env["DEVLOOP_HEADLESS"] = "1"
    return subprocess.run(
        [sys.executable, STATE_BIN, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd="/",
    )


@pytest.mark.real_git
def test_cli_slice_lifecycle(proj, home, cfg_edit, tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    git_ok = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git_ok, "init", "-q"], check=True)
    subprocess.run([*git_ok, "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    cfg_edit(lambda c: c["repos"][0].update(path=str(repo)))
    r = run_cli(home, "p", "slice", "new", "C1", "S1", "--repo", "main", "--risk", "R1")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "C1--S1 FRAME head=- round=0"
    k = "C1--S1"
    r = run_cli(home, "p", "slice", "set", k, "--rulebook", "a.md", "--head", sha[:8])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"C1--S1 FRAME head={sha[:8]} round=0"
    full = run_cli(home, "p", "slice", "set", k, "--next-action", "x", "--json")
    assert json.loads(full.stdout)["head"] == sha
    refused = run_cli(home, "p", "slice", "move", k, "GATE", "--reason", "x")
    assert refused.returncode == 3 and "REFUSED" in refused.stderr
    assert (
        run_cli(home, "p", "slice", "move", k, "PLAN", "--reason", "x").returncode == 0
    )
    shown = json.loads(run_cli(home, "p", "slice", "show", k).stdout)
    assert shown["phase"] == "PLAN" and shown["rulebooks"] == ["a.md"]
    assert k in run_cli(home, "p", "slice", "list").stdout
    assert run_cli(home, "p", "selfcheck", k, "GO", "--head", "a1").returncode == 3
    assert run_cli(home, "p", "event", "custom-kind", "k=v").returncode == 0
    assert run_cli(home, "p", "slice", "show", "nope--x").returncode == 3


def test_cli_refuses_import_when_headless(proj, home):
    r = run_cli(
        home,
        "p",
        "slice",
        "import",
        "X1",
        "S1",
        "--repo",
        "main",
        "--risk",
        "R1",
        "--rulebook",
        "a.md",
        "--head",
        "a1",
        "--branch",
        "feat/x",
        "--phase",
        "DEVELOP",
        "--purpose",
        "p",
        headless=True,
    )
    assert r.returncode == 3 and "headless" in r.stderr


def test_gate_refused_when_the_slice_has_no_head(proj, mk_slice):
    k = mk_slice(proj, phase="REVIEW", risk="R0")
    with pytest.raises(slices.TransitionError, match="head set"):
        slices.move(proj, k, "GATE", "x")


# --- owner gates (review round on the first commits) --------------------------


def test_headless_cannot_clear_a_hold(proj, mk_slice, monkeypatch):
    k = mk_slice(proj, phase="FRAME", risk="R3", rulebooks=["a.md"])
    slices.set_fields(proj, k, waiting_on="question q1")
    slices.move(proj, k, "HELD", "R3 waits")
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(slices.SliceError, match="held"):
        slices.set_fields(proj, k, waiting_on=None)
    with pytest.raises(slices.SliceError, match="held"):
        slices.set_fields(proj, k, risk="R2")
    with pytest.raises(slices.TransitionError, match="owner"):
        slices.move(proj, k, "FRAME", "x")
    with pytest.raises(store.HeadlessRefused):
        slices.owner_answer(proj, k)
    with pytest.raises(store.HeadlessRefused):
        slices.release_hold(proj, k)
    assert slices.load(proj, k)["phase"] == "HELD"


@pytest.mark.parametrize("reason", ["failed-twice"])
def test_owner_parks_leave_only_after_an_interactive_answer(
    proj, mk_slice, monkeypatch, reason
):
    k = mk_slice(proj, phase="REMEDIATE", round=2)
    s = slices.load(proj, k)
    s.update(phase="PARKED", parked_from="REMEDIATE", park_reason=reason)
    slices._save(proj, s)
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(slices.TransitionError, match="owner"):
        slices.move(proj, k, "REMEDIATE", "x")
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    slices.owner_answer(proj, k)
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    assert slices.move(proj, k, "REMEDIATE", "x")["phase"] == "REMEDIATE"
    assert slices.load(proj, k)["owner_answered"] is False


def test_other_parks_resume_without_the_owner(proj, mk_slice, monkeypatch):
    k = mk_slice(proj, phase="VERIFY")
    slices.move(proj, k, "PARKED", "stuck")
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    assert slices.move(proj, k, "VERIFY", "x")["phase"] == "VERIFY"


def test_headless_reframe_keeps_rounds_and_evidence(proj, ready, monkeypatch):
    k = ready("R1")
    s = slices.load(proj, k)
    s["round"] = 2
    s["plan_rounds"] = {"p": {"dennis": 2}}
    slices._save(proj, s)
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    s = slices.move(proj, k, "FRAME", "dodge")
    assert s["round"] == 2 and s["plan_rounds"] and s["evidence"]
    k2 = ready("R1")
    s = slices.load(proj, k2)
    s["round"] = 2
    slices._save(proj, s)
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    s = slices.move(proj, k2, "FRAME", "owner reframe")
    assert (s["round"], s["evidence"]) == (0, {})


def test_headless_cannot_lower_risk_of_an_r3_slice(proj, mk_slice, monkeypatch):
    k = mk_slice(proj, phase="FRAME", risk="R3")
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        slices.set_fields(proj, k, risk="R2")
    assert slices.load(proj, k)["risk"] == "R3"
    # A slice that starts below R3 is unaffected, and raising to R3 is allowed.
    k2 = mk_slice(proj, phase="FRAME", risk="R2")
    slices.set_fields(proj, k2, risk="R1")
    slices.set_fields(proj, k2, risk="R3")
    with pytest.raises(store.HeadlessRefused):
        slices.set_fields(proj, k2, risk="R1")
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    slices.set_fields(proj, k2, risk="R1")


def test_covers_must_record_the_members_current_head(proj, ready, mk_run):
    k = ready("R1")
    slices.set_fields(proj, k, full_suite_required=True)
    top = ready("R1", head="b2")
    mk_run(
        proj,
        "full-old",
        kind="test",
        key=top,
        head="b2",
        level="full",
        covers={k: "zz"},
    )
    slices.record_evidence(proj, k, "full", "GO", "full-old", head="a1")
    with pytest.raises(slices.TransitionError, match="head"):
        slices.move(proj, k, "GATE", "x")
    mk_run(proj, "full-list", kind="test", key=top, head="b2", level="full")
    req = store.read_json(store.run_dir(proj, "full-list") / "request.json")
    req["covers"] = [k]
    store.write_json(store.run_dir(proj, "full-list") / "request.json", req)
    slices.record_evidence(proj, k, "full", "GO", "full-list", head="a1")
    with pytest.raises(slices.TransitionError):
        slices.move(proj, k, "GATE", "x")


def test_gate_rejects_full_evidence_from_a_selected_run(proj, ready, cfg_edit, give):
    cfg_edit(lambda c: c.update(tests={"t2": {"argv": ["x"]}}))
    k = ready("R1")
    slices.set_fields(proj, k, full_suite_required=True)
    give(proj, k, "t2", "t2r")
    give(proj, k, "full", "fr")
    d = store.run_dir(proj, "fr")
    req = store.read_json(d / "request.json")
    req["select"] = ["tests/test_one.py::test_a"]
    os.chmod(d / "request.json", 0o644)
    store.write_json(d / "request.json", req)
    store.register_request(proj, "fr")
    with pytest.raises(slices.TransitionError, match="selection"):
        slices.move(proj, k, "GATE", "x")


def _close_board(tmp_path, ok_file):
    return _board(
        tmp_path,
        "import os, sys\n"
        f"sys.exit(0 if os.path.exists({str(ok_file)!r}) else 1) if sys.argv[1:2] == ['close'] else None\n",
    )


def test_failed_board_close_is_not_a_closure_and_a_retry_counts_once(
    proj, cfg_edit, tmp_path
):
    from devloop import status

    ok = tmp_path / "board-up"
    board_cmd = tmp_path / "absent-board"  # the command never starts
    cfg_edit(lambda c: c.update(board={"cmd": [str(board_cmd)], "phase_map": {}}))
    assert slices.row_close(proj, "ROW1") is False
    assert status.counts(proj)["rows_closed"] == 0
    cfg_edit(
        lambda c: c.update(
            board={"cmd": [str(_close_board(tmp_path, ok))], "phase_map": {}}
        )
    )
    ok.write_text("")
    assert slices.row_close(proj, "ROW1") is True
    assert slices.row_close(proj, "ROW1") is True  # a repeat is not a second closure
    slices.row_close(proj, "ROW2")
    assert status.counts(proj)["rows_closed"] == 2
    assert [k for k, r in slices.closure_records(proj).items() if r["closed"]] == [
        "ROW1",
        "ROW2",
    ]


def test_close_attempt_without_a_saved_closure_is_ambiguous_and_never_retried(
    proj, cfg_edit, tmp_path
):
    from devloop import board, status

    log = tmp_path / "calls.log"
    script = _board(
        tmp_path,
        f"import sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n",
    )
    cfg_edit(lambda c: c.update(board={"cmd": [str(script)], "phase_map": {}}))
    # A crash after the board call: attempted, never marked closed. A later retry, even
    # on another day, must not call `close` again.
    store.write_json(
        board._closures_path(proj),
        {"closures": {"ROW1": {"attempted": True, "closed": False}}},
    )
    assert slices.row_close(proj, "ROW1") is False
    assert slices.row_close(proj, "ROW1") is False
    assert not log.exists() or "close ROW1" not in log.read_text()
    assert slices.closure_records(proj)["ROW1"]["close_ambiguous"] is True
    assert status.counts(proj)["rows_closed"] == 0
    problems = store.read_json(store.state_dir(proj) / "problems.json")["problems"]
    assert list(problems) == ["close-ambiguous-ROW1"]


@pytest.mark.parametrize(
    "body", ["raise SystemExit(1)\n", "import time\ntime.sleep(60)\n"]
)
def test_a_close_that_started_and_failed_or_timed_out_is_ambiguous(
    proj, cfg_edit, tmp_path, monkeypatch, body
):
    import subprocess as sp

    from devloop import board

    script = _board(tmp_path, body)
    cfg_edit(lambda c: c.update(board={"cmd": [str(script)], "phase_map": {}}))
    if "sleep" in body:
        real = sp.run

        def quick(*a, **kw):
            kw["timeout"] = 1
            return real(*a, **kw)

        monkeypatch.setattr(board.subprocess, "run", quick)
    assert slices.row_close(proj, "ROW1") is False
    assert slices.closure_records(proj)["ROW1"]["attempted"] is True
    assert slices.row_close(proj, "ROW1") is False  # the next call marks it ambiguous
    assert slices.closure_records(proj)["ROW1"]["close_ambiguous"] is True


def test_row_close_needs_every_slice_done_and_the_confirm_flag(
    proj, cfg_edit, tmp_path
):
    cfg_edit(
        lambda c: c.update(board={"cmd": [str(_board(tmp_path, ""))], "phase_map": {}})
    )
    slices.new_slice(proj, "R7", "S1", repo="main")
    s2 = slices.new_slice(proj, "R7", "S2", repo="main")
    with pytest.raises(slices.SliceError, match="--confirm-last"):
        slices.close_row_checked(proj, "R7")
    with pytest.raises(slices.SliceError, match="must be DONE"):
        slices.close_row_checked(proj, "R7", confirm_last=True)
    for key in ("R7--S1", "R7--S2"):
        s = slices.load(proj, key)
        s["phase"] = "DONE"
        slices._save(proj, s)
    assert slices.closable_rows(proj) == ["R7"]
    assert slices.close_row_checked(proj, "R7", confirm_last=True) is True
    assert slices.closable_rows(proj) == []
    with pytest.raises(slices.SliceError, match="no slices"):
        slices.close_row_checked(proj, "NOPE", confirm_last=True)
    assert s2


def test_row_close_cli_and_snapshot_listing(proj, home, cfg_edit, tmp_path):
    from devloop import snapshot

    cfg_edit(
        lambda c: c.update(board={"cmd": [str(_board(tmp_path, ""))], "phase_map": {}})
    )
    slices.new_slice(proj, "R8", "S1", repo="main")
    s = slices.load(proj, "R8--S1")
    s["phase"] = "DONE"
    slices._save(proj, s)
    md, _ = snapshot.build_snapshot(proj)
    assert f"- R8: devloop-state {proj} row-close R8 --confirm-last" in md
    refused = run_cli(home, "p", "row-close", "R8")
    assert refused.returncode == 3 and "--confirm-last" in refused.stderr
    ok = run_cli(home, "p", "row-close", "R8", "--confirm-last")
    assert ok.returncode == 0 and "R8 closed" in ok.stdout
    assert (
        "R8:"
        not in snapshot.build_snapshot(proj)[0]
        .split("## Rows ready to close")[1]
        .split("##")[0]
    )


# --- live mode: a live loop session records phases for the dashboard ---------


def test_live_mode_moves_without_run_evidence(proj, mk_slice, cfg_edit):
    cfg_edit(lambda c: c.update(mode="live"))
    k = mk_slice(proj, phase="FRAME", risk=None, rulebooks=[])
    assert slices.move(proj, k, "DEVELOP", "live loop")["phase"] == "DEVELOP"
    assert slices.move(proj, k, "GATE", "live loop")["phase"] == "GATE"
    assert slices.move(proj, k, "DOCUMENT", "merged by the live loop")["phase"] == (
        "DOCUMENT"
    )


def test_live_mode_still_needs_the_owner_for_a_hold(proj, mk_slice, cfg_edit):
    cfg_edit(lambda c: c.update(mode="live"))
    k = mk_slice(proj, phase="HELD", parked_from="FRAME", park_reason="R3 waits")
    with pytest.raises(slices.TransitionError, match="owner"):
        slices.move(proj, k, "FRAME", "x")
    with pytest.raises(slices.TransitionError, match="resumes only"):
        slices.move(proj, k, "DEVELOP", "x")


def test_headless_mode_keeps_every_check(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk=None, rulebooks=[])
    with pytest.raises(slices.TransitionError):
        slices.move(proj, k, "DEVELOP", "x")
    k = mk_slice(proj, phase="GATE")
    with pytest.raises(slices.TransitionError, match="merge command"):
        slices.move(proj, k, "DOCUMENT", "x")


def test_pushed_credits_only_the_builders_own_commit(proj, mk_slice, mk_run):
    k = mk_slice(proj, phase="VERIFY", branch="feat/x", head="a1", head_writer=None)
    assert evidence.head_writer(proj, slices.load(proj, k)) is None
    mk_run(proj, "b0", kind="builder", key=k, result_head="zz", status="BLOCKED")
    mk_run(proj, "b1", kind="builder", key=k, result_head="a1", status="FAILED")
    mk_run(proj, "b2", kind="builder", key=k, result_head="a1", applied=False)
    mk_run(proj, "t1", kind="test", key=k, result_head="a1")
    for run in ("b0", "b1", "b2", "t1"):
        with pytest.raises(slices.SliceError):
            slices.pushed(proj, k, run)
    assert evidence.head_writer(proj, slices.load(proj, k)) is None
    mk_run(proj, "b3", kind="builder", key=k, result_head="a1", status="BLOCKED")
    slices.pushed(proj, k, "b3")
    assert evidence.head_writer(proj, slices.load(proj, k)) == "builder"
    slices.set_fields(proj, k, head="c9")  # the Tech lead's own later commit
    assert evidence.head_writer(proj, slices.load(proj, k)) is None


def test_pushed_accepts_a_short_sha_of_the_head(proj, mk_slice, mk_run):
    k = mk_slice(
        proj, phase="VERIFY", branch="feat/x", head="abcdef0123", head_writer=None
    )
    mk_run(proj, "b0", kind="builder", key=k, result_head="abcde", status="BLOCKED")
    with pytest.raises(slices.SliceError):
        slices.pushed(proj, k, "b0")
    mk_run(proj, "b1", kind="builder", key=k, result_head="abcdef0", status="BLOCKED")
    slices.pushed(proj, k, "b1")
    assert evidence.head_writer(proj, slices.load(proj, k)) == "builder"
