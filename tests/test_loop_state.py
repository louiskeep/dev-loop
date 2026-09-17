import subprocess

import pytest

from hooks import loop_state as ls


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _repo(tmp):
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "a").write_text("1")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "base")
    return ls.rev_parse(tmp, "HEAD")


def _state(head, **over):
    base = {
        "schema_version": 1,
        "slice": "s",
        "risk": "R1",
        "risk_rationale": "x",
        "slice_base": head,
        "gates": {
            "dennis": {
                "status": "green",
                "reviewer": "dennis",
                "at_commit": head,
                "ts": "t",
            }
        },
        "remediation": {"open_findings": 0},
    }
    base.update(over)
    return base


def _cfg(**over):
    c = {
        "codex_required_risks": ["R2", "R3"],
        "roadmap_paths": [],
        "shipped_log_paths": [],
    }
    c.update(over)
    return c


def test_gate_status_green_stale_missing_red(tmp_path):
    head = _repo(tmp_path)
    assert ls.gate_status(_state(head), "dennis", head) == "green"
    assert ls.gate_status(_state(head), "dennis", "OTHER") == "stale"
    assert ls.gate_status(_state(head, gates={}), "dennis", head) == "missing"
    red = _state(head)
    red["gates"]["dennis"]["status"] = "red"
    assert ls.gate_status(red, "dennis", head) == "red"


def test_validate_rejects_bad_risk(tmp_path):
    head = _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.validate_state(_state(head, risk="R9"))


def test_validate_rejects_bool_schema_version(tmp_path):
    head = _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.validate_state(_state(head, schema_version=True))


def test_validate_rejects_unknown_fields(tmp_path):
    head = _repo(tmp_path)
    s = _state(head)
    s["extra"] = 1
    with pytest.raises(ls.StateError):
        ls.validate_state(s)


def test_validate_rejects_negative_findings(tmp_path):
    head = _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.validate_state(_state(head, remediation={"open_findings": -1}))


def test_validate_rejects_unhashable_risk_as_stateerror(tmp_path):
    head = _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.validate_state(_state(head, risk=[]))


def test_is_mergeable_r1_needs_dennis_only(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head), head, tmp_path, _cfg())
    assert ok and reasons == []


def test_is_mergeable_r2_needs_codex(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head, risk="R2"), head, tmp_path, _cfg())
    assert not ok and any("codex" in r for r in reasons)


def test_is_mergeable_blocks_when_landing_differs(tmp_path):
    head = _repo(tmp_path)
    (tmp_path / "b").write_text("2")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    new_head = ls.rev_parse(tmp_path, "HEAD")
    ok, reasons = ls.is_mergeable(_state(head), new_head, tmp_path, _cfg())
    assert not ok and any("stale" in r for r in reasons)


def test_is_mergeable_requires_docs_when_configured(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(
        _state(head),
        head,
        tmp_path,
        _cfg(roadmap_paths=["docs/ROADMAP.md"], shipped_log_paths=["docs/SHIPPED.md"]),
    )
    assert not ok and any("roadmap" in r for r in reasons)


def test_missing_risk_fails_closed(tmp_path):
    head = _repo(tmp_path)
    s = _state(head)
    del s["risk"]
    with pytest.raises(ls.StateError):
        ls.validate_state(s)


def test_docs_current_evaluated_against_landing_not_head(tmp_path):
    base = _repo(tmp_path)  # c0
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    gated = ls.rev_parse(tmp_path, "HEAD")  # c1: the commit the gate attests to
    (tmp_path / "docs" / "ROADMAP.md").write_text("y")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c2")  # HEAD = c2 touches roadmap
    s = _state(base)
    s["slice_base"] = base
    s["gates"]["dennis"]["at_commit"] = gated
    cfg = _cfg(roadmap_paths=["docs/ROADMAP.md"])
    ok, reasons = ls.is_mergeable(s, gated, tmp_path, cfg)  # landing c1 != HEAD c2
    assert not ok and any("roadmap" in r for r in reasons)


def test_save_rejects_invalid_state(tmp_path):
    _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.save_state(tmp_path, {"schema_version": 1})
