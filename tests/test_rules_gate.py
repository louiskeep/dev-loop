import json
import subprocess

import pytest

from hooks import rules_gate as rg

RULES = "/home/cam/dev-rules"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "src").mkdir()
    # Existing source files: editing them needs only the core rules.
    (root / "src" / "a.py").write_text("")
    (root / "src" / "b.py").write_text("")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return root


def _transcript(tmp_path, reads, name="t.jsonl"):
    """A transcript whose assistant turns Read each path in `reads`."""
    path = tmp_path / name
    lines = []
    for fp in reads:
        item = {"type": "tool_use", "name": "Read", "input": {"file_path": fp}}
        lines.append(json.dumps({"message": {"role": "assistant", "content": [item]}}))
    path.write_text("\n".join(lines) + "\n")
    return path


def _event(file_path, transcript, **extra):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(file_path)},
        "transcript_path": str(transcript),
        "session_id": "s1",
        **extra,
    }


def _all_rules():
    return [f"{RULES}/{n}" for n in rg.REQUIRED_RULES]


def test_classify_plan_code_and_ungated(repo):
    assert rg.classify(str(repo / "docs/plans/x.md"), repo) == "plan"
    assert rg.classify(str(repo / "docs/specs/y.md"), repo) == "plan"
    assert rg.classify(str(repo / "src/a.py"), repo) == "code"
    assert rg.classify(str(repo / "README.md"), repo) is None
    assert rg.classify(str(repo / "data.json"), repo) is None
    assert rg.classify("/elsewhere/a.py", repo) is None
    assert rg.classify(str(repo / "src/a.py"), None) is None


def test_code_write_denied_until_rules_read(repo, tmp_path):
    t = _transcript(tmp_path, [f"{RULES}/README.md"])
    out = rg.pre_tool_use(_event(repo / "src/a.py", t), {})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "00-universal.md" in decision["permissionDecisionReason"]
    assert "development-loop.md" in decision["permissionDecisionReason"]
    assert (
        "README.md"
        not in decision["permissionDecisionReason"]
        .split("Not yet read")[1]
        .split("Read them")[0]
    )


def test_allowed_once_all_rules_read_and_marker_skips_rescan(repo, tmp_path):
    t = _transcript(tmp_path, _all_rules())
    assert rg.pre_tool_use(_event(repo / "src/a.py", t), {}) is None
    # Second call in the same session passes on the marker, even with an empty transcript.
    empty = _transcript(tmp_path, [], name="empty.jsonl")
    assert rg.pre_tool_use(_event(repo / "src/b.py", empty), {}) is None


def test_plugin_rules_snapshot_counts_as_read(repo, tmp_path):
    plugin_rules = str(rg.ls._plugin_root() / "rules")
    t = _transcript(tmp_path, [f"{plugin_rules}/{n}" for n in rg.REQUIRED_RULES])
    assert rg.pre_tool_use(_event(repo / "docs/plans/p.md", t), {}) is None


def test_rules_read_via_shell_command_counts(repo, tmp_path):
    cmd = " && ".join(f"cat {RULES}/{n}" for n in rg.REQUIRED_RULES)
    item = {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
    t = tmp_path / "bash.jsonl"
    t.write_text(json.dumps({"message": {"content": [item]}}) + "\n")
    assert rg.pre_tool_use(_event(repo / "src/a.py", t), {}) is None


def test_a_file_with_the_same_name_elsewhere_does_not_count(repo, tmp_path):
    t = _transcript(tmp_path, [f"/somewhere/else/{n}" for n in rg.REQUIRED_RULES])
    out = rg.pre_tool_use(_event(repo / "src/a.py", t), {})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_mentioning_rules_in_prose_does_not_count(repo, tmp_path):
    t = tmp_path / "prose.jsonl"
    text = {"type": "text", "text": " ".join(_all_rules())}
    t.write_text(json.dumps({"message": {"content": [text]}}) + "\n")
    out = rg.pre_tool_use(_event(repo / "src/a.py", t), {})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_subagent_must_read_rules_in_its_own_transcript(repo, tmp_path):
    main_t = _transcript(tmp_path, _all_rules(), name="main.jsonl")
    agent_t = _transcript(tmp_path, [], name="agent.jsonl")
    ev = _event(
        repo / "src/a.py", main_t, agent_id="a1", agent_transcript_path=str(agent_t)
    )
    out = rg.pre_tool_use(ev, {})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_warn_mode_adds_context_instead_of_denying(repo, tmp_path):
    t = _transcript(tmp_path, [])
    out = rg.pre_tool_use(_event(repo / "src/a.py", t), {"rules_gate_mode": "warn"})
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "rules_gate" in out["hookSpecificOutput"]["additionalContext"]


def test_ungated_files_pass_without_reading(repo, tmp_path):
    t = _transcript(tmp_path, [])
    assert rg.pre_tool_use(_event(repo / "notes.md", t), {}) is None
    assert rg.pre_tool_use(_event(tmp_path / "outside.py", t), {}) is None


def test_plan_without_rules_line_is_blocked_after_write(repo):
    plan = repo / "docs/plans/p.md"
    plan.write_text("Status: plan\n\n# Title\n")
    ev = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(plan)},
    }
    out = rg.post_tool_use(ev, {})
    assert out["decision"] == "block"
    assert "Rules consulted" in out["reason"]
    plan.write_text("Status: plan\nRules consulted: 00-universal, development-loop\n")
    assert rg.post_tool_use(ev, {}) is None


def test_main_end_to_end_denies_via_stdin(repo, tmp_path, monkeypatch, capsys):
    t = _transcript(tmp_path, [])
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO(json.dumps(_event(repo / "src/a.py", t)))
    )
    assert rg.main() == 0
    assert (
        json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )


def test_local_operations_profile_is_required(repo, tmp_path):
    core = [f"{RULES}/{n}" for n in ("README.md", "00-universal.md", "development-loop.md")]
    out = rg.pre_tool_use(_event(repo / "src/a.py", _transcript(tmp_path, core)), {})
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "profiles/local-operations.md" in reason.split("Not yet read")[1]


def test_new_source_file_requires_architecture(repo, tmp_path):
    t = _transcript(tmp_path, _all_rules())
    out = rg.pre_tool_use(_event(repo / "src/new_mod.py", t), {})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    missing = decision["permissionDecisionReason"].split("Not yet read")[1].split("Read them")[0]
    assert "architecture.md" in missing
    assert "development-loop.md" not in missing


def test_editing_existing_source_does_not_require_architecture(repo, tmp_path):
    t = _transcript(tmp_path, _all_rules())
    assert rg.pre_tool_use(_event(repo / "src/a.py", t), {}) is None


def test_new_plan_does_not_require_architecture(repo, tmp_path):
    t = _transcript(tmp_path, _all_rules())
    assert rg.pre_tool_use(_event(repo / "docs/plans/new.md", t), {}) is None


def test_new_source_allowed_after_architecture_read_and_cached(repo, tmp_path):
    t = _transcript(tmp_path, _all_rules() + [f"{RULES}/architecture.md"])
    assert rg.pre_tool_use(_event(repo / "src/new_mod.py", t), {}) is None
    empty = _transcript(tmp_path, [], name="empty.jsonl")
    assert rg.pre_tool_use(_event(repo / "src/other_new.py", empty), {}) is None


def test_core_marker_does_not_cover_a_new_file(repo, tmp_path):
    # A session that passed the core check earlier still needs architecture.md
    # before it creates a file, and only architecture.md is asked for.
    assert rg.pre_tool_use(_event(repo / "src/a.py", _transcript(tmp_path, _all_rules())), {}) is None
    empty = _transcript(tmp_path, [], name="empty.jsonl")
    assert rg.pre_tool_use(_event(repo / "src/b.py", empty), {}) is None
    out = rg.pre_tool_use(_event(repo / "src/new_mod.py", empty), {})
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    missing = reason.split("Not yet read")[1].split("Read them")[0]
    assert "architecture.md" in missing
    assert "00-universal.md" not in missing


def test_dev_rules_dir_comes_from_config(repo, tmp_path):
    other = tmp_path / "rules-elsewhere"
    other.mkdir()
    config = {"dev_rules_dir": str(other)}
    assert str(other) in rg.rules_dirs(config)
    t = _transcript(tmp_path, [f"{other}/{n}" for n in rg.REQUIRED_RULES])
    assert rg.pre_tool_use(_event(repo / "src/a.py", t), config) is None


def test_dev_rules_dir_defaults_to_home_dev_rules():
    assert rg.DEFAULT_DEV_RULES_DIR == "/home/cam/dev-rules"
    dirs = rg.rules_dirs({})
    assert dirs[0] == str(rg.ls._plugin_root() / "rules")
    if __import__("pathlib").Path(RULES).is_dir():
        assert RULES in dirs


def test_deny_reason_points_at_a_copy_that_has_the_file(repo, tmp_path):
    # A canonical checkout older than the profile: point at the plugin snapshot.
    old = tmp_path / "old-dev-rules"
    old.mkdir()
    for n in ("README.md", "00-universal.md", "development-loop.md"):
        (old / n).write_text("")
    config = {"dev_rules_dir": str(old)}
    out = rg.pre_tool_use(_event(repo / "src/a.py", _transcript(tmp_path, [])), config)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    snapshot = rg.ls._plugin_root() / "rules"
    assert f"{old}/00-universal.md" in reason
    assert f"{snapshot}/profiles/local-operations.md" in reason
