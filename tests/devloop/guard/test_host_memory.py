"""Host memory reservations come from each run's frozen request, in its own project."""

import datetime
import json
import os

import pytest

from devloop import runs, store


@pytest.fixture
def two(home, proj, write_project, minimal_project, cfg_edit):
    """Project p estimates a builder at 600 MB; project q at 1 MB."""
    cfg_edit(lambda c: c.update(memory_mb={"builder": 600}))
    q = minimal_project("q")
    q["memory_mb"] = {"builder": 1}
    write_project(home, "q", q)
    return "p", "q"


def plant(project, run_id, *, started=None, kind="builder", **extra):
    rd = store.run_dir(project, run_id)
    rd.mkdir(parents=True)
    started = started or datetime.datetime.now(datetime.timezone.utc).isoformat()
    store.write_json(
        rd / "request.json",
        {"id": run_id, "kind": kind, "started_at": started, **extra},
    )
    return rd


def test_legacy_request_uses_its_owning_projects_estimate(two):
    p, q = two
    plant(p, "legacy.1")  # frozen before memory_mb existed
    assert runs.reserved_mb() == 600
    plant(q, "legacy.2")
    assert runs.reserved_mb() == 601


def test_frozen_amount_wins_over_any_config(two):
    p, _ = two
    plant(p, "new.1", memory_mb=250)
    assert runs.reserved_mb() == 250


def test_damaged_process_record_in_another_project_keeps_its_reservation(two):
    p, _ = two
    rd = plant(p, "new.1", memory_mb=700)
    (rd / "proc.json").write_text("{not json")
    assert runs.reserved_mb() == 700


def test_naive_start_time_is_read_as_utc(two):
    p, _ = two
    naive = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    plant(p, "naive.1", started=naive.isoformat(), memory_mb=300)
    assert runs.reserved_mb() == 300
    old = naive - datetime.timedelta(seconds=runs.RESERVE_WINDOW_S + 60)
    plant(p, "naive.2", started=old.isoformat(), memory_mb=300)
    assert runs.reserved_mb() == 300


def test_a_naive_clock_argument_is_read_as_utc(two):
    p, _ = two
    plant(p, "new.1", memory_mb=100)
    naive_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    assert runs.reserved_mb(naive_now) == 100


def test_an_ended_run_holds_nothing(two):
    p, _ = two
    rd = plant(p, "new.1", memory_mb=100)
    (rd / "exit").write_text(json.dumps({"code": 0, "reason": "exit"}))
    assert runs.reserved_mb() == 0


def test_a_project_whose_config_cannot_load_still_counts_a_legacy_run(two, home):
    p, q = two
    plant(q, "legacy.1")
    (home / "q" / "project.json").write_text("{bad")
    assert runs.reserved_mb() > 0


def test_damaged_request_files_are_skipped_and_healthy_runs_still_count(two):
    p, q = two
    plant(p, "ok.1", memory_mb=200)
    bad = plant(p, "bad.1")
    (bad / "request.json").write_bytes(b"\xff\xfe{")
    plant(q, "ok.2", memory_mb=50)
    plant(q, "kind.1", kind=[])  # a legacy request whose kind is not a name
    plant(q, "kind.2", kind={"a": 1}, memory_mb=None)
    assert runs.reserved_mb() == 250


def test_unreadable_foreign_request_leaves_healthy_reservation(two, monkeypatch):
    p, q = two
    path = plant(p, "bad.1", memory_mb=900) / "request.json"
    path.chmod(0)
    if os.access(path, os.R_OK):
        # A privileged test runner can read mode 000. Model the normal user's error.
        read = store.read_json

        def read_as_user(candidate):
            if candidate == path:
                raise PermissionError(path)
            return read(candidate)

        monkeypatch.setattr(store, "read_json", read_as_user)
    plant(q, "ok.1", memory_mb=700)
    assert runs.reserved_mb() == 700


def test_frozen_memory_counts_with_a_damaged_kind(two):
    p, _ = two
    plant(p, "new.1", kind=[], memory_mb=4096)
    assert runs.reserved_mb() == 4096
