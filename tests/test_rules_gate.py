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


# ---- plan-lint (PostToolUse advisory) ----

_RULES_OK = "Rules consulted: 00-universal, development-loop\n"


def _fake_lint(tmp_path, body, name="fake-plan-lint"):
    """A stand-in plan-lint: `body` is shell run with $1=kind, $2=file."""
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


def _bad_lint(tmp_path):
    return _fake_lint(tmp_path, 'echo "problem one"; echo "problem two"; exit 1')


def _post(path):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(path)},
    }


def _plan(repo, name="2026-10-30-x.md", text=_RULES_OK):
    p = repo / "docs/plans" / name
    p.write_text(text)
    return p


def _handoff(repo, name="h.md"):
    d = repo / "docs/handoffs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("# handoff\n")
    return p


def test_classify_handoff(repo):
    (repo / "docs/handoffs").mkdir()
    assert rg.classify(str(repo / "docs/handoffs/a.md"), repo) == "handoff"
    assert rg.classify(str(repo / "docs/handoffs/a.txt"), repo) is None
    assert rg.classify(str(repo / "docs/plans/a.md"), repo) == "plan"


def test_handoff_write_needs_no_rules_read(repo, tmp_path):
    t = _transcript(tmp_path, [])
    assert rg.pre_tool_use(_event(repo / "docs/handoffs/h.md", t), {}) is None


def test_plan_lint_warn_returns_advice_with_problem_lines(repo, tmp_path):
    cfg = {"plan_lint_cmd": _bad_lint(tmp_path)}
    out = rg.post_tool_use(_post(_plan(repo)), cfg)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    ctx = hso["additionalContext"]
    assert ctx.startswith(
        "dev-loop plan-lint: 2 problem(s) in docs/plans/2026-10-30-x.md"
    )
    assert "problem one" in ctx and "problem two" in ctx
    assert "DEFECT-CATALOG.md" in ctx


def test_plan_lint_passes_kind_and_file_to_command(repo, tmp_path):
    log = tmp_path / "args.log"
    cmd = _fake_lint(tmp_path, f'echo "$1 $2" > {log}; echo OK; exit 0')
    plan = _plan(repo)
    assert rg.post_tool_use(_post(plan), {"plan_lint_cmd": cmd}) is None
    assert log.read_text().split() == ["plan", str(plan)]


def test_plan_lint_caps_lines_and_trims_long_ones(repo, tmp_path):
    body = (
        'i=0; while [ $i -lt 12 ]; do echo "line$i '
        + "x" * 300
        + '"; i=$((i+1)); done; exit 1'
    )
    out = rg.post_tool_use(
        _post(_plan(repo)), {"plan_lint_cmd": _fake_lint(tmp_path, body)}
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "12 problem(s)" in ctx
    assert "line7" in ctx and "line8" not in ctx
    assert "x" * 161 not in ctx


def test_plan_lint_block_mode_blocks(repo, tmp_path):
    cfg = {"plan_lint_cmd": _bad_lint(tmp_path), "plan_lint_mode": "block"}
    out = rg.post_tool_use(_post(_plan(repo)), cfg)
    assert out["decision"] == "block"
    assert "dev-loop plan-lint: 2 problem(s)" in out["reason"]


def test_plan_lint_off_and_missing_command_return_none(repo, tmp_path):
    plan = _plan(repo)
    bad = _bad_lint(tmp_path)
    assert (
        rg.post_tool_use(_post(plan), {"plan_lint_cmd": bad, "plan_lint_mode": "off"})
        is None
    )
    assert (
        rg.post_tool_use(_post(plan), {"plan_lint_cmd": str(tmp_path / "nope")}) is None
    )
    notexec = tmp_path / "notexec"
    notexec.write_text("#!/bin/sh\nexit 1\n")
    assert rg.post_tool_use(_post(plan), {"plan_lint_cmd": str(notexec)}) is None


def test_plan_lint_skips_plans_before_cutoff_and_undated(repo, tmp_path):
    cfg = {"plan_lint_cmd": _bad_lint(tmp_path)}
    assert rg.post_tool_use(_post(_plan(repo, "2026-10-29-old.md")), cfg) is None
    assert rg.post_tool_use(_post(_plan(repo, "undated.md")), cfg) is None
    newer = rg.post_tool_use(_post(_plan(repo, "2026-11-02-new.md")), cfg)
    assert newer is not None
    since = {**cfg, "plan_lint_since": "2026-10-01"}
    assert rg.post_tool_use(_post(_plan(repo, "2026-10-29-old.md")), since) is not None


def test_handoff_is_linted_regardless_of_name(repo, tmp_path):
    cfg = {"plan_lint_cmd": _bad_lint(tmp_path)}
    out = rg.post_tool_use(_post(_handoff(repo, "no-date.md")), cfg)
    assert (
        "problem(s) in docs/handoffs/no-date.md"
        in out["hookSpecificOutput"]["additionalContext"]
    )


def test_handoff_uses_handoff_subcommand_and_skips_rules_line(repo, tmp_path):
    log = tmp_path / "args.log"
    cmd = _fake_lint(tmp_path, f'echo "$1" > {log}; exit 0')
    assert rg.post_tool_use(_post(_handoff(repo)), {"plan_lint_cmd": cmd}) is None
    assert log.read_text().strip() == "handoff"


@pytest.mark.parametrize("body", ["exit 2", "echo boom; exit 3", "sleep 5; exit 1"])
def test_plan_lint_failures_never_break_the_hook(repo, tmp_path, monkeypatch, body):
    monkeypatch.setattr(rg, "PLAN_LINT_TIMEOUT", 0.3)
    cfg = {"plan_lint_cmd": _fake_lint(tmp_path, body)}
    assert rg.post_tool_use(_post(_plan(repo)), cfg) is None


def test_plan_lint_clean_is_silent(repo, tmp_path):
    cmd = _fake_lint(tmp_path, "echo OK; exit 0")
    assert rg.post_tool_use(_post(_plan(repo)), {"plan_lint_cmd": cmd}) is None


def test_rules_consulted_block_wins_over_plan_lint(repo, tmp_path):
    cfg = {"plan_lint_cmd": _bad_lint(tmp_path)}
    out = rg.post_tool_use(_post(_plan(repo, text="no rules line\n")), cfg)
    assert out["decision"] == "block"
    assert "Rules consulted" in out["reason"]
