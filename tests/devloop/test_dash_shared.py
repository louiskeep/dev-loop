"""Shared read helpers and role labels that the dashboard, STATUS and the tick use."""

import datetime
import os

import pytest

from devloop import project as projmod
from devloop import runs, scrub, slices, status, store

UTC = datetime.UTC


# --- store.cap_active ----------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        (None, False),
        ("2000-01-01T00:00:00+00:00", False),
        ("2999-01-01T00:00:00+00:00", True),
        ("2999-01-01T00:00:00", True),  # naive reads as UTC
        ("2000-01-01T00:00:00", False),
        ("not a time", True),  # damaged file blocks
        ("", True),
    ],
)
def test_cap_active_cases(proj, home, text, want):
    path = home / "p" / "cap-until"
    if text is not None:
        path.write_text(text)
    assert store.cap_active(proj) is want


def test_cap_active_takes_a_clock(proj, home):
    (home / "p" / "cap-until").write_text("2030-01-01T00:00:00+00:00")
    before = datetime.datetime(2029, 1, 1, tzinfo=UTC)
    after = datetime.datetime(2031, 1, 1, tzinfo=UTC)
    assert store.cap_active(proj, before) and not store.cap_active(proj, after)


# --- run state and slot use ---------------------------------------------------


def _alive(proj, run_id):
    runs.update_request(
        proj,
        run_id,
        pid=os.getpid(),
        pid_start=runs.proc_start(os.getpid()),
        boot_id=runs.boot_id(),
    )


def test_exited_builder_is_awaiting_apply(proj, mk_slice, mk_run):
    key = mk_slice(proj, phase="DEVELOP")
    rd = mk_run(proj, "b1", kind="builder", key=key, head="a1", applied=False)
    req = runs.read_request(proj, "b1")
    assert runs.holds_slot(rd, req)  # still reserves a slot
    assert runs.run_state(rd, req) == "awaiting-apply"


def test_live_builder_is_executing_and_applied_builder_is_none(proj, mk_slice, mk_run):
    key = mk_slice(proj, phase="DEVELOP")
    rd = mk_run(proj, "b1", kind="builder", key=key, head="a1", applied=False)
    (rd / "exit").unlink()
    _alive(proj, "b1")
    assert runs.run_state(rd, runs.read_request(proj, "b1")) == "executing"
    done = mk_run(proj, "b2", kind="builder", key=key, head="a1")
    assert runs.run_state(done, runs.read_request(proj, "b2")) is None


def test_unconfirmed_test_is_awaiting_release(proj, mk_slice, mk_run):
    key = mk_slice(proj, phase="VERIFY")
    rd = mk_run(proj, "t1", kind="test", key=key, head="a1", level="t1", applied=False)
    store.write_json(
        rd / "exit", {"code": 255, "reason": "exit"}
    )  # infra, not a result
    req = runs.read_request(proj, "t1")
    assert runs.holds_slot(rd, req)
    assert runs.run_state(rd, req) == "awaiting-release"
    (rd / "released").write_text("")
    assert runs.run_state(rd, req) is None


def test_slot_usage_matches_status(proj, mk_slice, mk_run):
    key = mk_slice(proj, phase="DEVELOP")
    mk_run(proj, "b1", kind="builder", key=key, head="a1", applied=False)
    mk_run(proj, "b2", kind="builder", key=key, head="a1")
    assert runs.slot_usage(proj) == {"builder": 1}
    assert "- Developers: 1" in status.render_status(proj)


def test_readers_create_no_directories(proj, home):
    assert not (home / "p" / "runs").exists()
    assert list(runs.iter_runs(proj)) == []
    assert slices.list_slices(proj) == []
    assert runs.slot_usage(proj) == {}
    assert not (home / "p" / "runs").exists()
    assert not (home / "p" / "slices").exists()


# --- checklist ----------------------------------------------------------------


def test_exit_checklist_labels_unchanged(proj, mk_slice):
    k = mk_slice(proj, phase="GATE", risk="R2", head="a1")
    assert [label for label, _ in slices.exit_checklist(proj, k)] == [
        "no test or review outstanding on head",
        "no other slice of this row is in DOCUMENT",
        "approved plan unchanged",
        "selfcheck GO on head",
        "t1 GO on head",
        "code review dennis GO on head",
        "code review codex GO on head",
    ]
    k2 = mk_slice(proj, phase="PLAN", risk="R2")
    assert [label for label, _ in slices.exit_checklist(proj, k2)][:3] == [
        "plan file exists",
        "no plan review or repair outstanding",
        "plan names the Rules consulted",
    ]


def test_exit_items_carry_fixed_codes(proj, mk_slice):
    from devloop import evidence

    k = mk_slice(proj, phase="GATE", risk="R2", head="a1")
    items = slices.exit_items(proj, k)
    assert {i.code for i in items} <= set(evidence.CODES)
    reviews = [i for i in items if i.code == "code-review"]
    assert [i.reviewer for i in reviews] == ["dennis", "codex"]
    assert all(i.reason is None for i in items)


def test_a_run_problem_becomes_a_fixed_reason(proj, mk_slice, mk_run, give):
    from devloop import evidence

    k = mk_slice(proj, phase="GATE", risk="R1", head="a1")
    give(proj, k, "code:codex", "c1")
    (store.run_dir(proj, "c1") / "applied").unlink()
    (item,) = [i for i in slices.exit_items(proj, k) if i.reviewer == "codex"]
    assert not item.ok and item.reason == "not-applied"
    assert item.reason in evidence.REASON_WORDS
    assert "c1" not in repr(item.reason)


def test_kind_names_unchanged():
    assert tuple(runs.KINDS) == (
        "planner",
        "builder",
        "codex-builder",
        "dennis",
        "codex-review",
        "codex-fix",
        "test",
        "docs",
        "duty",
    )


# --- IDs ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "ghp_DEMOTOKEN123",
        "sk-abcdefgh12345",
        "a" * 40,
        "deadbeef00",
        "has space",
        "../etc",
        "ok\n",
        "",
    ],
)
def test_token_in_id_is_redacted(value):
    assert scrub.plain(value) == "?"


@pytest.mark.parametrize("value", ["ROW-1", "S1", "v2.1", "slice-moved", 7])
def test_plain_ids_pass(value):
    assert scrub.plain(value) == str(value)


# --- role labels --------------------------------------------------------------


def test_roles_config_overrides_and_validates(proj, cfg_edit):
    cfg = projmod.load_project(proj)
    assert projmod.role_label(cfg, "builder") == "Developer"
    assert projmod.role_label(cfg, "codex-builder") == "Developer"
    assert projmod.role_label(cfg, "dennis") == "Senior reviewer"
    assert projmod.role_label(cfg, "owner") == "Product owner (you)"
    assert projmod.role_label(cfg, "nonsense") == "Worker"
    assert projmod.pool_label(cfg, "builder") == "Developers"
    cfg_edit(lambda c: c.update(roles={"builder": "Engineer"}))
    cfg = projmod.load_project(proj)
    assert projmod.role_label(cfg, "builder") == "Engineer"
    assert projmod.role_label(cfg, "codex-builder") == "Developer"
    for bad in (
        {"nope": "x"},
        {"builder": ""},
        {"builder": "a\nb"},
        {"builder": "x" * 41},
    ):
        cfg_edit(lambda c, bad=bad: c.update(roles=bad))
        with pytest.raises(projmod.ConfigError, match="roles"):
            projmod.load_project(proj)


@pytest.mark.parametrize(
    "bad", [{"port": 80}, {"port": "9000"}, {"port": True}, {"host": 5}]
)
def test_dash_config_is_validated(proj, cfg_edit, bad):
    cfg_edit(lambda c: c.update(dash=bad))
    with pytest.raises(projmod.ConfigError, match="dash"):
        projmod.load_project(proj)


def test_status_and_tick_line_use_role_labels(proj, mk_slice, mk_run, home):
    key = mk_slice(proj, phase="DEVELOP")
    mk_run(proj, "b1", kind="builder", key=key, head="a1", applied=False)
    line = status.tick_line(proj, "model", "did things")
    assert "Running: Developers 1/8, QA 0/4, Reviewers 0/2, Architects 0/3." in line
    for word in ("builders", "tests,", "reviews."):
        assert word not in line
    store.append_event(proj, "run-dispatched", run_kind="codex-builder", key="R--S")
    store.append_event(proj, "run-dispatched", run_kind="dennis", key="R--S")
    out = status.summarize(proj, "2000-01-01T00:00:00+00:00")
    assert "started Developer on R--S" in out
    assert "started Senior reviewer on R--S" in out
    assert "codex" not in out and "dennis" not in out


def test_counts_from_marks_an_unreadable_ledger_unknown():
    now = datetime.datetime.now(UTC)
    stamp = now.isoformat()
    got = status.counts_from({"a": {"at": stamp}}, None, now)
    assert got["merged_total"] == 1 and got["rows_closed"] is None
    got = status.counts_from(None, {"R": {"closed": True}}, now)
    assert got["merged_week"] is None and got["rows_closed"] == 1


def test_event_words_use_fixed_wording(proj):
    cfg = projmod.load_project(proj)
    ev = {"kind": "merged", "row": "R1", "slice": "ghp_ABCDEFGH12345"}
    assert status.event_words(ev, cfg) == "merged R1 ?"
    assert status.event_words({"kind": "mystery"}, cfg) is None


def test_snapshot_records_node_status_without_text(proj, cfg_edit, home):
    import json

    from devloop import snapshot

    cfg_edit(
        lambda c: c.update(
            snapshot_cmds=[
                {"name": "good", "argv": ["echo", "SECRET-TEXT"]},
                {"name": "bad", "argv": ["false"]},
            ]
        )
    )
    snapshot.build_snapshot(proj)
    text = (home / "p" / "node-status.json").read_text()
    data = json.loads(text)
    assert data["good"]["ok"] is True and data["bad"]["ok"] is False
    assert "SECRET-TEXT" not in text
