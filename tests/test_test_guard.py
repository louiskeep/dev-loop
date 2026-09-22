import json

from hooks import test_guard as tg

# --- detect_weakening: the pure directional detector -------------------------


def test_removing_check_metadata_fires():
    old = "assert a.schema.equals(b.schema, check_metadata=True)"
    new = "assert a.schema.equals(b.schema)"
    signals = tg.detect_weakening(old, new)
    assert "removed check_metadata=True" in signals
    assert "removed a .equals(...) parity assertion" not in signals  # .equals still present


def test_removing_equals_assertion_fires():
    old = "assert ot.schema.equals(nt.schema, check_metadata=True)\nassert x == y\n"
    new = "assert x == y\n"
    signals = tg.detect_weakening(old, new)
    assert "removed a .equals(...) parity assertion" in signals
    assert "removed check_metadata=True" in signals


def test_removing_strong_equality_assert_fires():
    # A strong (==) assertion deleted outright: a clear coverage loss.
    old = "result = compute()\nassert result == expected\n"
    new = "result = compute()\n"
    signals = tg.detect_weakening(old, new)
    assert "removed a strong (==/equals/is) assertion" in signals


def test_adding_skip_marker_fires():
    old = "def test_parity():\n    assert a == b\n"
    new = "@pytest.mark.skip(reason='flaky')\ndef test_parity():\n    assert a == b\n"
    assert "added a skip/xfail suppressor" in tg.detect_weakening(old, new)


def test_adding_xfail_marker_fires():
    old = "def test_parity():\n    assert a == b\n"
    new = "@pytest.mark.xfail\ndef test_parity():\n    assert a == b\n"
    assert "added a skip/xfail suppressor" in tg.detect_weakening(old, new)


def test_additive_edit_does_not_fire():
    # Adding a NEW check_metadata assertion (today's real pattern) must be silent.
    old = "assert a.to_pylist() == b.to_pylist()\n"
    new = (
        "assert a.to_pylist() == b.to_pylist()\n"
        "assert a.schema.equals(b.schema, check_metadata=True)\n"
    )
    assert tg.detect_weakening(old, new) == []


def test_soft_assert_churn_does_not_fire():
    # Moving/renaming a truthiness assert is not a coverage loss.
    old = "assert ready\nassert x == y\n"
    new = "assert is_ready\nassert x == y\n"
    assert tg.detect_weakening(old, new) == []


# --- _is_protected_test ------------------------------------------------------


def test_protected_test_path_matching():
    fg, td = tg._DEFAULT_FILE_GLOBS, tg._DEFAULT_TEST_DIRS
    assert tg._is_protected_test("/repo/tests/physical/test_shadow_group_key.py", fg, td)
    assert tg._is_protected_test("/repo/pkg/foo_test.py", fg, td)
    assert tg._is_protected_test("/repo/tests/unit/helpers.py", fg, td)  # under tests/
    assert not tg._is_protected_test("/repo/src/pkg/module.py", fg, td)
    # A non-test file under a directory that merely starts with "test_" is NOT protected.
    assert not tg._is_protected_test("/repo/test_fixtures/data_loader.py", fg, td)
    assert not tg._is_protected_test("", fg, td)


# --- main_from_event: end to end --------------------------------------------


def _edit_event(tmp_path, old, new, *, name="test_parity.py", tool="Edit", **extra):
    fp = str(tmp_path / name)
    ev = {
        "tool_name": tool,
        "tool_input": {"file_path": fp, "old_string": old, "new_string": new},
        "cwd": str(tmp_path),
    }
    ev.update(extra)
    return ev


def test_warn_mode_allows_and_audits(tmp_path, capsys):
    ev = _edit_event(
        tmp_path,
        "assert a.schema.equals(b.schema, check_metadata=True)",
        "assert a.schema.equals(b.schema)",
        agent_id="sub-123",
        agent_type="build-agent",
    )
    rc = tg.main_from_event(ev)
    assert rc == 0  # warn never blocks
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "WEAKENS" in out["hookSpecificOutput"]["permissionDecisionReason"]
    log = (tmp_path / ".loop-test-guard.log").read_text().strip()
    entry = json.loads(log)
    assert entry["agent_id"] == "sub-123"
    assert entry["agent_type"] == "build-agent"
    assert "removed check_metadata=True" in entry["signals"]


def test_block_mode_denies(tmp_path, capsys):
    (tmp_path / ".loop-config.json").write_text('{"test_guard_mode": "block"}')
    ev = _edit_event(
        tmp_path,
        "assert ot.schema.equals(nt.schema, check_metadata=True)",
        "assert ot.to_pylist() == nt.to_pylist()",
    )
    rc = tg.main_from_event(ev)
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_off_mode_is_silent(tmp_path, capsys):
    (tmp_path / ".loop-config.json").write_text('{"test_guard_mode": "off"}')
    ev = _edit_event(
        tmp_path,
        "assert a.schema.equals(b.schema, check_metadata=True)",
        "assert a.schema.equals(b.schema)",
    )
    assert tg.main_from_event(ev) == 0
    assert capsys.readouterr().out.strip() == ""
    assert not (tmp_path / ".loop-test-guard.log").exists()


def test_non_test_file_ignored(tmp_path, capsys):
    ev = _edit_event(
        tmp_path,
        "assert a.schema.equals(b.schema, check_metadata=True)",
        "assert a.schema.equals(b.schema)",
        name="module.py",
    )
    assert tg.main_from_event(ev) == 0
    assert capsys.readouterr().out.strip() == ""


def test_additive_test_edit_is_silent(tmp_path, capsys):
    ev = _edit_event(
        tmp_path,
        "assert a == b\n",
        "assert a == b\nassert a.schema.equals(b.schema, check_metadata=True)\n",
    )
    assert tg.main_from_event(ev) == 0
    assert capsys.readouterr().out.strip() == ""


def test_non_edit_tool_ignored(capsys):
    assert tg.main_from_event({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == 0
    assert capsys.readouterr().out.strip() == ""


def test_subagent_only_skips_main_session(tmp_path, capsys):
    (tmp_path / ".loop-config.json").write_text('{"test_guard_subagent_only": true}')
    ev = _edit_event(
        tmp_path,
        "assert a.schema.equals(b.schema, check_metadata=True)",
        "assert a.schema.equals(b.schema)",
    )  # no agent_id -> main session
    assert tg.main_from_event(ev) == 0
    assert capsys.readouterr().out.strip() == ""


def test_multiedit_weakening_detected(tmp_path, capsys):
    fp = str(tmp_path / "test_x.py")
    ev = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": fp,
            "edits": [
                {"old_string": "y = 1", "new_string": "y = 2"},
                {
                    "old_string": "assert a.schema.equals(b.schema, check_metadata=True)",
                    "new_string": "assert a.schema.equals(b.schema)",
                },
            ],
        },
        "cwd": str(tmp_path),
    }
    assert tg.main_from_event(ev) == 0
    out = json.loads(capsys.readouterr().out)
    assert "removed check_metadata=True" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_write_over_existing_test_detects_drop(tmp_path, capsys):
    fp = tmp_path / "test_w.py"
    fp.write_text("assert a.schema.equals(b.schema, check_metadata=True)\n")
    ev = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(fp), "content": "assert a.schema.equals(b.schema)\n"},
        "cwd": str(tmp_path),
    }
    assert tg.main_from_event(ev) == 0
    out = json.loads(capsys.readouterr().out)
    assert "removed check_metadata=True" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_write_new_test_file_is_silent(tmp_path, capsys):
    ev = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(tmp_path / "test_new.py"),
            "content": "assert a == b\n",
        },
        "cwd": str(tmp_path),
    }
    assert tg.main_from_event(ev) == 0
    assert capsys.readouterr().out.strip() == ""
