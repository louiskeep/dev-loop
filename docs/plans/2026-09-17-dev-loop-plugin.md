# dev-loop plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package Cam's development loop into a Claude Code plugin that mechanically blocks self-certified merges and guides the judgment steps, with the main thread acting as a conductor that delegates heavy work.

**Architecture:** A standalone git repo published as a plugin. A PreToolUse hook (`gate_guard.py`) blocks merge/push-to-main unless a git-commit-keyed state file shows the required green gates; a Stop hook (`done_claim_check.py`) surfaces a warning when a "done" claim lacks evidence; a discipline skill (`conducting-the-loop`) carries the judgment steps the hooks cannot decide. All enforcement keys off `.loop-state.json`, managed only through `loop_state.py`.

**Tech Stack:** Python 3.11 (standard library only, no third-party deps in hooks), pytest for tests, Claude Code plugin format (v2.1.248+ contracts).

**Spec:** `docs/specs/2026-09-17-dev-loop-plugin-design.md`

## Global Constraints

- Python 3.11+, standard library only for the three hook/CLI modules (`json`, `sys`, `subprocess`, `re`, `pathlib`, `dataclasses`). No pip dependencies at runtime; a hook that imports a missing package is a broken hook.
- Hooks must never raise to stdout uncaught. Any internal error on a gated operation results in a fail-closed DENY with a readable reason, not a stack trace.
- PreToolUse block contract: exit code `2` with the reason on stdout as `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"<text>"}}`. Allow = exit `0` with no `permissionDecision`.
- Stop hook contract: this build cannot block. Output only `{"hookSpecificOutput":{"systemMessage":"<text>"}}` at exit `0`; honor `stop_hook_active == true` by exiting `0` immediately.
- State file name: `.loop-state.json` at the target repo root. It is only read/written through `loop_state.py`, never hand-edited by a task.
- Per-repo config file: `.loop-config.json` at the target repo root overrides the plugin's `config.json` defaults.
- `${CLAUDE_PLUGIN_ROOT}` is the only correct way to reference plugin files from hooks; always quote it in shell.
- Do not place `skills/`, `agents/`, `commands/`, or `hooks/` inside `.claude-plugin/`. Only `plugin.json` and `marketplace.json` live there.
- Gate names are exactly: `plan_review`, `dennis`, `codex`. Risk levels are exactly: `R0`, `R1`, `R2`, `R3`. Codex-required risks: `R2`, `R3`.

---

### Task 0: Verify the two uncertain hook behaviors (spike)

**Why first:** The plan's conductor-load mechanism and the Stop-hook role both depend on behavior the docs describe inconsistently with observed behavior. Confirm empirically before building on either. Output is a recorded finding; any throwaway plugin is deleted.

**Files:**
- Create (throwaway): `/tmp/loop-spike/hooks/hooks.json`, `/tmp/loop-spike/.claude-plugin/plugin.json`, `/tmp/loop-spike/hooks/echo_ctx.sh`

- [ ] **Step 1: Build a throwaway plugin** with a SessionStart `command` hook that prints a unique sentinel to stdout, and a PreToolUse(Bash) hook that exits 2 with a deny reason on any command containing `SPIKE_BLOCK`.

- [ ] **Step 2: Install and observe.** Install via `claude plugin marketplace add /tmp/loop-spike-marketplace` then `/plugin install`. Start a new session; check whether the SessionStart sentinel appears in the model's context (ask the model to repeat it). Run a Bash command containing `SPIKE_BLOCK`; confirm it is denied.

- [ ] **Step 3: Record findings** in `docs/specs/2026-09-17-dev-loop-plugin-design.md` under a new "Verified hook behavior" section: (a) does SessionStart `command` stdout reach context in this build? (b) confirm PreToolUse exit-2 deny works. Delete `/tmp/loop-spike*`.

- [ ] **Step 4: Decide the conductor-load mechanism** from the finding: if SessionStart stdout reaches context, use a SessionStart `command` hook that emits the conductor preamble; if not, rely on the skill's description trigger plus a SessionStart `prompt`-type hook. Note the decision in the spec. This decides Task 7's SessionStart entry.

---

### Task 1: Plugin scaffold and manifests

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `config.json`, `README.md`, `pyproject.toml` (pytest config only)

**Interfaces:**
- Produces: an installable, empty-but-valid plugin; `config.json` default keys consumed by `loop_state.py` in Task 2.

- [ ] **Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "dev-loop",
  "displayName": "Dev Loop",
  "version": "0.1.0",
  "description": "Conductor + hook-enforced development loop: blocks self-certified merges, guides the judgment steps.",
  "author": { "name": "cam", "email": "goodneighbor@goodneighbor.design" },
  "license": "MIT",
  "keywords": ["workflow", "gate", "review", "conductor"]
}
```

- [ ] **Step 2: Write `.claude-plugin/marketplace.json`**

```json
{
  "name": "cam-dev-loop",
  "owner": { "name": "cam", "email": "goodneighbor@goodneighbor.design" },
  "plugins": [
    { "name": "dev-loop", "source": "./", "description": "Conductor + hook-enforced development loop." }
  ]
}
```

- [ ] **Step 3: Write `config.json` (plugin defaults, verbatim)**

```json
{
  "gated_branches": ["main", "master"],
  "roadmap_paths": ["docs/ROADMAP.md"],
  "shipped_log_paths": ["docs/backlog/RECENTLY-SHIPPED.md"],
  "codex_required_risks": ["R2", "R3"],
  "fail_closed": true
}
```

- [ ] **Step 4: Write `pyproject.toml`** with a `[tool.pytest.ini_options]` section setting `testpaths = ["tests"]`. No build deps.

- [ ] **Step 5: Validate**

Run: `claude plugin validate ./`
Expected: passes with no errors.

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin config.json README.md pyproject.toml
git commit -m "scaffold dev-loop plugin manifest and config

cam"
```

---

### Task 2: `loop_state.py` core library

**Files:**
- Create: `hooks/loop_state.py`
- Test: `tests/test_loop_state.py`

**Interfaces:**
- Produces (consumed by Tasks 3, 4, 5, 6):
  - `STATE_FILENAME = ".loop-state.json"`
  - `load_config(repo_root: Path) -> dict` (merges plugin `config.json` with repo `.loop-config.json`)
  - `load_state(repo_root: Path) -> dict | None`
  - `save_state(repo_root: Path, state: dict) -> None`
  - `current_head(repo_root: Path) -> str` (returns `git rev-parse HEAD`, or raises `GitError`)
  - `gate_status(state: dict, gate: str, head: str) -> str` returns one of `"green" | "stale" | "red" | "missing"`
  - `is_mergeable(state: dict, head: str, config: dict) -> tuple[bool, list[str]]`
  - `class GitError(Exception)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_loop_state.py
import json
from pathlib import Path
import pytest
from hooks import loop_state as ls


def _state(**over):
    base = {
        "slice": "s", "risk": "R1", "risk_rationale": "x", "slice_base": "base0",
        "gates": {"dennis": {"status": "green", "at_commit": "HEAD1", "ts": "t"}},
        "remediation": {"open_findings": 0},
        "docs_current": {"roadmap": True, "shipped_log": True},
    }
    base.update(over)
    return base


def test_gate_status_green_when_on_head():
    assert ls.gate_status(_state(), "dennis", "HEAD1") == "green"


def test_gate_status_stale_when_commit_moved():
    assert ls.gate_status(_state(), "dennis", "HEAD2") == "stale"


def test_gate_status_missing_when_gate_absent():
    assert ls.gate_status(_state(gates={}), "dennis", "HEAD1") == "missing"


def test_gate_status_red_when_status_red():
    s = _state(gates={"dennis": {"status": "red", "at_commit": "HEAD1"}})
    assert ls.gate_status(s, "dennis", "HEAD1") == "red"


def test_r1_mergeable_with_dennis_only():
    ok, reasons = ls.is_mergeable(_state(risk="R1"), "HEAD1", {"codex_required_risks": ["R2", "R3"]})
    assert ok and reasons == []


def test_r2_blocked_without_codex():
    ok, reasons = ls.is_mergeable(_state(risk="R2"), "HEAD1", {"codex_required_risks": ["R2", "R3"]})
    assert not ok and any("codex" in r for r in reasons)


def test_blocked_when_docs_not_current():
    s = _state(docs_current={"roadmap": False, "shipped_log": True})
    ok, reasons = ls.is_mergeable(s, "HEAD1", {"codex_required_risks": ["R2", "R3"]})
    assert not ok and any("roadmap" in r for r in reasons)


def test_blocked_when_open_findings():
    s = _state(remediation={"open_findings": 2})
    ok, reasons = ls.is_mergeable(s, "HEAD1", {"codex_required_risks": ["R2", "R3"]})
    assert not ok and any("finding" in r for r in reasons)


def test_save_then_load_roundtrip(tmp_path):
    ls.save_state(tmp_path, _state())
    assert ls.load_state(tmp_path)["slice"] == "s"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_loop_state.py -v`
Expected: FAIL (module/functions not defined).

- [ ] **Step 3: Implement `hooks/loop_state.py`**

```python
"""State store and merge-gate logic for the dev-loop plugin.

The state file is the single source of truth for whether a slice may merge.
Everything keys off git commit SHAs so evidence is tied to an exact artifact.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

STATE_FILENAME = ".loop-state.json"
REPO_CONFIG_FILENAME = ".loop-config.json"


class GitError(Exception):
    pass


def _plugin_root() -> Path:
    # ${CLAUDE_PLUGIN_ROOT} is exported for hooks; fall back to this file's parent.
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root) if root else Path(__file__).resolve().parent.parent


def load_config(repo_root: Path) -> dict:
    config = {}
    plugin_cfg = _plugin_root() / "config.json"
    if plugin_cfg.exists():
        config.update(json.loads(plugin_cfg.read_text()))
    repo_cfg = Path(repo_root) / REPO_CONFIG_FILENAME
    if repo_cfg.exists():
        config.update(json.loads(repo_cfg.read_text()))
    return config


def load_state(repo_root: Path) -> dict | None:
    path = Path(repo_root) / STATE_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_state(repo_root: Path, state: dict) -> None:
    path = Path(repo_root) / STATE_FILENAME
    path.write_text(json.dumps(state, indent=2) + "\n")


def current_head(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitError(f"cannot resolve HEAD: {exc}") from exc
    return out.stdout.strip()


def gate_status(state: dict, gate: str, head: str) -> str:
    entry = state.get("gates", {}).get(gate)
    if not entry:
        return "missing"
    if entry.get("status") != "green":
        return "red"
    if entry.get("at_commit") != head:
        return "stale"
    return "green"


def is_mergeable(state: dict, head: str, config: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    required = ["dennis"]
    if state.get("risk") in config.get("codex_required_risks", ["R2", "R3"]):
        required.append("codex")
    for gate in required:
        status = gate_status(state, gate, head)
        if status != "green":
            reasons.append(f"{gate} gate is {status} (need green on current HEAD)")
    docs = state.get("docs_current", {})
    if not docs.get("roadmap"):
        reasons.append("roadmap not marked current for this slice")
    if not docs.get("shipped_log"):
        reasons.append("shipped_log not marked current for this slice")
    open_findings = state.get("remediation", {}).get("open_findings", 0)
    if open_findings:
        reasons.append(f"{open_findings} open remediation finding(s)")
    return (not reasons, reasons)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_loop_state.py -v`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add hooks/loop_state.py tests/test_loop_state.py
git commit -m "add loop_state core: state store + merge-gate logic

cam"
```

---

### Task 3: `loop_state.py` CLI

**Files:**
- Modify: `hooks/loop_state.py` (add `main()` + argparse)
- Test: `tests/test_loop_state_cli.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces (used by conductor and subagents to record evidence): CLI subcommands
  - `init --slice NAME --risk R2 --rationale TEXT` (sets `slice_base` to current HEAD)
  - `record-gate NAME --status green|red [--commit HEAD]` (`HEAD` resolves to current SHA)
  - `set-docs-current --roadmap true|false --shipped-log true|false`
  - `set-findings N`
  - `check-merge` (exit 0 mergeable, exit 1 with reasons on stderr)
  - `status` (prints the state as JSON)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_loop_state_cli.py
import subprocess, sys, json
from pathlib import Path


def _git(tmp, *args):
    subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True, text=True)


def _run(tmp, *args):
    return subprocess.run(
        [sys.executable, "hooks/loop_state.py", *args],
        cwd=Path.cwd(), env={"PWD": str(tmp), **_env(tmp)},
        capture_output=True, text=True,
    )


def _env(tmp):
    import os
    e = dict(os.environ)
    e["LOOP_REPO_ROOT"] = str(tmp)  # CLI resolves repo root from this if set
    return e


def _init_repo(tmp):
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "f").write_text("x")
    _git(tmp, "add", "-A"); _git(tmp, "commit", "-m", "c0")


def test_init_and_check_merge_blocks_without_gate(tmp_path):
    _init_repo(tmp_path)
    assert _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x").returncode == 0
    r = _run(tmp_path, "check-merge")
    assert r.returncode == 1 and "dennis" in r.stderr


def test_record_gate_then_merge_ok(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    _run(tmp_path, "record-gate", "dennis", "--status", "green", "--commit", "HEAD")
    _run(tmp_path, "set-docs-current", "--roadmap", "true", "--shipped-log", "true")
    assert _run(tmp_path, "check-merge").returncode == 0


def test_new_commit_makes_gate_stale(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    _run(tmp_path, "record-gate", "dennis", "--status", "green", "--commit", "HEAD")
    _run(tmp_path, "set-docs-current", "--roadmap", "true", "--shipped-log", "true")
    (tmp_path / "f2").write_text("y"); _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-m", "c1")
    r = _run(tmp_path, "check-merge")
    assert r.returncode == 1 and "stale" in r.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_loop_state_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `main()` to `hooks/loop_state.py`**

```python
def _repo_root() -> Path:
    import os
    return Path(os.environ.get("LOOP_REPO_ROOT") or os.environ.get("PWD") or ".")


def main(argv: list[str] | None = None) -> int:
    import argparse, sys
    p = argparse.ArgumentParser(prog="loop_state")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--slice", required=True)
    pi.add_argument("--risk", required=True, choices=["R0", "R1", "R2", "R3"])
    pi.add_argument("--rationale", required=True)

    pg = sub.add_parser("record-gate")
    pg.add_argument("gate", choices=["plan_review", "dennis", "codex"])
    pg.add_argument("--status", required=True, choices=["green", "red"])
    pg.add_argument("--commit", default="HEAD")

    pd = sub.add_parser("set-docs-current")
    pd.add_argument("--roadmap", choices=["true", "false"], required=True)
    pd.add_argument("--shipped-log", choices=["true", "false"], required=True)

    pf = sub.add_parser("set-findings")
    pf.add_argument("n", type=int)

    sub.add_parser("check-merge")
    sub.add_parser("status")

    args = p.parse_args(argv)
    root = _repo_root()

    if args.cmd == "init":
        state = {
            "slice": args.slice, "risk": args.risk, "risk_rationale": args.rationale,
            "slice_base": current_head(root), "gates": {},
            "remediation": {"open_findings": 0},
            "docs_current": {"roadmap": False, "shipped_log": False},
        }
        save_state(root, state)
        return 0

    state = load_state(root)
    if state is None:
        print("no .loop-state.json; run 'init' first", file=sys.stderr)
        return 1

    if args.cmd == "record-gate":
        commit = current_head(root) if args.commit == "HEAD" else args.commit
        state.setdefault("gates", {})[args.gate] = {"status": args.status, "at_commit": commit}
        save_state(root, state); return 0
    if args.cmd == "set-docs-current":
        state["docs_current"] = {"roadmap": args.roadmap == "true", "shipped_log": args.shipped_log == "true"}
        save_state(root, state); return 0
    if args.cmd == "set-findings":
        state.setdefault("remediation", {})["open_findings"] = args.n
        save_state(root, state); return 0
    if args.cmd == "check-merge":
        ok, reasons = is_mergeable(state, current_head(root), load_config(root))
        if ok:
            return 0
        print("; ".join(reasons), file=sys.stderr); return 1
    if args.cmd == "status":
        print(json.dumps(state, indent=2)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_loop_state_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/loop_state.py tests/test_loop_state_cli.py
git commit -m "add loop_state CLI: init, record-gate, docs, check-merge

cam"
```

---

### Task 4: `gate_guard.py` PreToolUse hook

**Files:**
- Create: `hooks/gate_guard.py`
- Test: `tests/test_gate_guard.py`

**Interfaces:**
- Consumes: `loop_state.load_state`, `load_config`, `current_head`, `is_mergeable`.
- Produces: a hook entrypoint invoked as `python gate_guard.py` reading stdin JSON, exiting `0` (allow) or `2` (deny) with the PreToolUse JSON contract.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gate_guard.py
import json, subprocess, sys
from pathlib import Path
from hooks import gate_guard


def test_is_gated_detects_merge_to_main():
    assert gate_guard.is_gated_git_op("git merge feature", ["main"], "main")
    assert gate_guard.is_gated_git_op("git push origin main", ["main"], "somebranch")
    assert gate_guard.is_gated_git_op("git push --force origin main", ["main"], "x")
    assert gate_guard.is_gated_git_op("gh pr merge 12", ["main"], "x")


def test_is_gated_ignores_feature_push():
    assert not gate_guard.is_gated_git_op("git push origin feat/x", ["main"], "feat/x")
    assert not gate_guard.is_gated_git_op("git status", ["main"], "feat/x")


def test_non_bash_allows():
    out = _invoke({"tool_name": "Read", "tool_input": {}, "cwd": "/tmp"})
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_gated_op_missing_state_fails_closed(tmp_path):
    _init_repo(tmp_path)
    out = _invoke({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "cwd": str(tmp_path)})
    assert out.returncode == 2
    payload = json.loads(out.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no .loop-state.json" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_gated_op_allowed_when_green(tmp_path):
    _init_repo(tmp_path)
    _cli(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    _cli(tmp_path, "record-gate", "dennis", "--status", "green", "--commit", "HEAD")
    _cli(tmp_path, "set-docs-current", "--roadmap", "true", "--shipped-log", "true")
    out = _invoke({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "cwd": str(tmp_path)})
    assert out.returncode == 0
```

(Helpers `_invoke`, `_init_repo`, `_cli` are small subprocess wrappers; `_invoke` pipes the dict as JSON to `python hooks/gate_guard.py` and returns the CompletedProcess.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_gate_guard.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `hooks/gate_guard.py`**

```python
"""PreToolUse hook: block merge/push-to-main unless the loop state is green."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop_state as ls  # noqa: E402


def _allow() -> int:
    return 0


def _deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 2


def is_gated_git_op(command: str, gated_branches: list[str], current_branch: str) -> bool:
    c = command.strip()
    if re.search(r"\bgh\s+pr\s+merge\b", c):
        return True
    branch_alt = "|".join(re.escape(b) for b in gated_branches)
    # merge INTO a gated branch (running merge while on a gated branch)
    if re.search(r"\bgit\s+merge\b", c) and current_branch in gated_branches:
        return True
    # push to a gated branch (explicit ref) or force-push to one
    if re.search(rf"\bgit\s+push\b.*\b({branch_alt})\b", c):
        return True
    # push with no ref while on a gated branch
    if re.search(r"\bgit\s+push\b", c) and current_branch in gated_branches \
            and not re.search(r"\bpush\b.*\s\S+\s+\S+", c):
        return True
    return False


def _current_branch(repo_root: Path) -> str:
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=str(repo_root), capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return ""


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return _allow()  # cannot parse: not our concern, let normal flow handle it

    if event.get("tool_name") != "Bash":
        return _allow()
    command = event.get("tool_input", {}).get("command", "")
    repo_root = Path(event.get("cwd", "."))

    try:
        config = ls.load_config(repo_root)
        gated = config.get("gated_branches", ["main", "master"])
        if not is_gated_git_op(command, gated, _current_branch(repo_root)):
            return _allow()

        state = ls.load_state(repo_root)
        if state is None:
            return _deny("dev-loop: no .loop-state.json for this slice; run 'loop_state.py init' "
                         "and pass the gates before merging to a protected branch.")
        head = ls.current_head(repo_root)
        ok, reasons = ls.is_mergeable(state, head, config)
        if ok:
            return _allow()
        return _deny("dev-loop blocks this merge/push: " + "; ".join(reasons))
    except Exception as exc:
        if ls.load_config(repo_root).get("fail_closed", True):
            return _deny(f"dev-loop fail-closed (guard error): {exc}")
        return _allow()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_gate_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/gate_guard.py tests/test_gate_guard.py
git commit -m "add gate_guard PreToolUse hook: block ungated merges to protected branches

cam"
```

---

### Task 5: `done_claim_check.py` Stop hook

**Files:**
- Create: `hooks/done_claim_check.py`
- Test: `tests/test_done_claim_check.py`

**Interfaces:**
- Consumes: `loop_state.load_state`, `current_head`, `is_mergeable`, `load_config`.
- Produces: a Stop-hook entrypoint that exits `0` always, emitting a `systemMessage` warning when a done/merge claim is unbacked. Never blocks.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_done_claim_check.py
import json
from hooks import done_claim_check as dc


def test_detects_done_language():
    assert dc.claims_done("This is done and merge-ready.")
    assert dc.claims_done("Slice complete, ready to ship.")
    assert not dc.claims_done("Working on the next task now.")


def test_stop_hook_active_short_circuits(capsys):
    rc = dc.main_from_event({"stop_hook_active": True, "last_assistant_message": "done"})
    assert rc == 0 and capsys.readouterr().out.strip() == ""


def test_warns_on_done_claim_without_state(tmp_path, capsys):
    rc = dc.main_from_event({
        "last_assistant_message": "All done, merge-ready.",
        "cwd": str(tmp_path),
    })
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "systemMessage" in out["hookSpecificOutput"]
    assert "no .loop-state.json" in out["hookSpecificOutput"]["systemMessage"]


def test_silent_when_no_done_claim(tmp_path, capsys):
    rc = dc.main_from_event({"last_assistant_message": "next up: task 3", "cwd": str(tmp_path)})
    assert rc == 0 and capsys.readouterr().out.strip() == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_done_claim_check.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `hooks/done_claim_check.py`**

```python
"""Stop hook: warn (not block) when a done/merge claim lacks gate evidence."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop_state as ls  # noqa: E402

_DONE = re.compile(r"\b(done|complete|completed|merge[- ]?ready|ready to (ship|merge)|shipped)\b", re.I)


def claims_done(text: str) -> bool:
    return bool(_DONE.search(text or ""))


def _emit(message: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"systemMessage": message}}))
    return 0


def main_from_event(event: dict) -> int:
    if event.get("stop_hook_active") is True:
        return 0
    text = event.get("last_assistant_message", "")
    if not claims_done(text):
        return 0
    repo_root = Path(event.get("cwd", "."))
    try:
        state = ls.load_state(repo_root)
        if state is None:
            return _emit("dev-loop: a completion was claimed but there is no .loop-state.json "
                         "for this slice. If this is real work, initialize the slice and run the gates.")
        head = ls.current_head(repo_root)
        ok, reasons = ls.is_mergeable(state, head, ls.load_config(repo_root))
        if not ok:
            return _emit("dev-loop: completion claimed, but the slice is not gate-clean: "
                         + "; ".join(reasons))
    except Exception:
        return 0  # a warning hook must never disrupt the turn
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    return main_from_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_done_claim_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/done_claim_check.py tests/test_done_claim_check.py
git commit -m "add done_claim_check Stop hook: warn on unbacked completion claims

cam"
```

---

### Task 6: docs-current auto-check helper

**Files:**
- Modify: `hooks/loop_state.py` (add `docs_touched_since`)
- Test: `tests/test_docs_current.py`

**Interfaces:**
- Produces: `docs_touched_since(repo_root, slice_base, config) -> dict` returning `{"roadmap": bool, "shipped_log": bool}` by diffing `slice_base..HEAD` against configured paths. Used by an optional `refresh-docs-current` CLI subcommand and by the conductor.

- [ ] **Step 1: Write failing test**

```python
# tests/test_docs_current.py
import subprocess
from pathlib import Path
from hooks import loop_state as ls


def _git(tmp, *a): subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True, text=True)


def test_docs_touched_detects_roadmap_change(tmp_path):
    _git(tmp_path, "init"); _git(tmp_path, "config", "user.email", "t@t"); _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a").write_text("1"); _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-m", "base")
    base = ls.current_head(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP.md").write_text("x"); _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-m", "roadmap")
    cfg = {"roadmap_paths": ["docs/ROADMAP.md"], "shipped_log_paths": ["docs/backlog/RECENTLY-SHIPPED.md"]}
    result = ls.docs_touched_since(tmp_path, base, cfg)
    assert result == {"roadmap": True, "shipped_log": False}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_docs_current.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `docs_touched_since` in `hooks/loop_state.py`**

```python
def docs_touched_since(repo_root: Path, slice_base: str, config: dict) -> dict:
    import subprocess
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{slice_base}..HEAD"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    )
    changed = set(out.stdout.split())
    def _any(paths: list[str]) -> bool:
        return any(p in changed for p in paths)
    return {
        "roadmap": _any(config.get("roadmap_paths", [])),
        "shipped_log": _any(config.get("shipped_log_paths", [])),
    }
```

- [ ] **Step 4: Wire a `refresh-docs-current` CLI subcommand** that calls `docs_touched_since(root, state["slice_base"], load_config(root))` and writes the result into `state["docs_current"]`. Add one CLI test mirroring Task 3's style.

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_docs_current.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hooks/loop_state.py tests/test_docs_current.py
git commit -m "add docs-current auto-check via slice_base..HEAD diff

cam"
```

---

### Task 7: Register hooks (`hooks/hooks.json` + wrapper)

**Files:**
- Create: `hooks/hooks.json`, `hooks/run-hook.sh`

**Interfaces:**
- Consumes: the three hook scripts and the Task 0 SessionStart decision.
- Produces: the plugin's event registrations.

- [ ] **Step 1: Write `hooks/run-hook.sh`** (a thin dispatcher so `hooks.json` stays simple and `${CLAUDE_PLUGIN_ROOT}` resolves once)

```bash
#!/usr/bin/env bash
# Usage: run-hook.sh <script-basename>
set -euo pipefail
exec python3 "${CLAUDE_PLUGIN_ROOT}/hooks/$1.py"
```

Make it executable (`chmod +x`).

- [ ] **Step 2: Write `hooks/hooks.json`**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": ["${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh", "gate_guard"] }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": ["${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh", "done_claim_check"] }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Add the SessionStart entry per the Task 0 finding.** If SessionStart `command` stdout reaches context, add a SessionStart(`startup|clear|compact`) `command` hook emitting the conductor preamble (a short pointer to the `conducting-the-loop` skill). If not, add a SessionStart `prompt`-type hook whose prompt tells the model to load `conducting-the-loop`. Record which was used.

- [ ] **Step 4: Validate + smoke test**

Run: `claude plugin validate ./`
Then install locally and confirm in a scratch git repo that a `git push origin main` without state is denied and with green state is allowed.
Expected: validate passes; deny/allow behave as in Task 4.

- [ ] **Step 5: Commit**

```bash
git add hooks/hooks.json hooks/run-hook.sh
git commit -m "register PreToolUse/Stop/SessionStart hooks

cam"
```

---

### Task 8: Port dennis and barry agents into the plugin

**Files:**
- Create: `agents/dennis.md`, `agents/barry.md`

- [ ] **Step 1: Copy the existing definitions** from `~/.claude/agents/dennis.md` and `~/.claude/agents/barry.md`.

- [ ] **Step 2: Normalize frontmatter** to the plugin agent schema (`name`, `description`, `tools`, `model`). Keep dennis review-only (no Write/Edit) and barry docs-only, matching their current tool sets.

- [ ] **Step 3: Validate**

Run: `claude plugin validate ./`
Expected: agents discovered, no errors.

- [ ] **Step 4: Commit**

```bash
git add agents/
git commit -m "port dennis (gate) and barry (docs) agents into plugin

cam"
```

---

### Task 9: `role-matrix.md` delegation reference

**Files:**
- Create: `skills/conducting-the-loop/role-matrix.md`

- [ ] **Step 1: Write the complexity-tiered role matrix** exactly as in the spec's "Conductor and complexity-tiered delegation" section: the four complexity signals (risk, novelty, blast radius, ambiguity), the role table (researcher, planner, builder, reviewer, cross-model gate, documenter, operator), the agent per tier, and the reason per row. State the hard rule: Codex gate is always Codex; fallback is the highest available Claude model.

- [ ] **Step 2: Commit**

```bash
git add skills/conducting-the-loop/role-matrix.md
git commit -m "add role-matrix delegation reference

cam"
```

---

### Task 10: `conducting-the-loop` discipline skill (writing-skills TDD)

> This task does NOT pre-write the final SKILL.md. Per the writing-skills Iron Law, the skill is written only after a failing baseline. REQUIRED SUB-SKILL: superpowers:writing-skills.

**Files:**
- Create: `skills/conducting-the-loop/SKILL.md`
- Create: `tests/skill-scenarios/` (the pressure scenarios and recorded baselines)

**Interfaces:**
- Consumes: `role-matrix.md`, the bundled `rules/` (Task 11), the hook behavior confirmed in Task 0.

- [ ] **Step 1: RED - write pressure scenarios** for each of the four drift modes, with combined pressure (time + sunk cost + "main is green, just merge"). Save them under `tests/skill-scenarios/`.

- [ ] **Step 2: RED - run scenarios on a subagent WITHOUT the skill.** Record verbatim rationalizations for each drift mode. If a mode does not fail in the no-skill control, do not write guidance for it (note that in the scenario file).

- [ ] **Step 3: GREEN - write `SKILL.md`.** Frontmatter description states triggering conditions only, no workflow summary. Body: conductor identity as a positive recipe (output = decisions, delegation calls, synthesized results with next-step options; direct edits limited to edge cleanup + git ops); phase sequence referencing `rules/`; delegation referencing `role-matrix.md` requiring a cited tier reason; a rationalization table + red-flag list built from Step 2, with the risk-under-classification and self-certified-done rows carrying the most weight.

- [ ] **Step 4: GREEN - micro-test the wording** against the no-guidance control (5+ reps), reading every flagged match by hand, before the full scenarios.

- [ ] **Step 5: GREEN/REFACTOR - re-run the pressure scenarios WITH the skill.** Add counters for any new loophole; repeat until compliant. Record the final pass.

- [ ] **Step 6: Commit**

```bash
git add skills/conducting-the-loop/SKILL.md tests/skill-scenarios/
git commit -m "add conducting-the-loop discipline skill (TDD-verified)

cam"
```

---

### Task 11: Bundle the rulebooks

**Files:**
- Create: `rules/` (snapshot of `~/dev-rules/*.md`)

- [ ] **Step 1: Copy a snapshot** of the current `~/dev-rules/` markdown into `rules/` so the plugin is self-contained and versioned with the enforcement that cites it. Add a `rules/PROVENANCE.md` noting the source and snapshot date.

- [ ] **Step 2: Fix cross-references** in `SKILL.md` and `role-matrix.md` to point at `rules/<file>.md` (plugin-relative), not `/home/cam/dev-rules/`.

- [ ] **Step 3: Commit**

```bash
git add rules/
git commit -m "bundle dev-rules snapshot into plugin

cam"
```

---

### Task 12: End-to-end acceptance test and local install

**Files:**
- Create: `tests/test_e2e_enforcement.py`

**Acceptance (the whole enforcement, in one scripted scenario):**

- [ ] **Step 1: Write the e2e test** that, in a temp git repo with the plugin's hooks wired via direct script invocation:
  1. `init` a slice at R2, attempt a simulated `git push origin main` through `gate_guard` -> DENY (no gates).
  2. record `dennis` green + `set-docs-current true true`, attempt push -> still DENY (R2 needs codex).
  3. record `codex` green, attempt push -> ALLOW.
  4. make a new commit, attempt push -> DENY (gates now stale).
  5. re-record `dennis` + `codex` on new HEAD, push -> ALLOW.

- [ ] **Step 2: Run**

Run: `python -m pytest tests/test_e2e_enforcement.py -v`
Expected: PASS.

- [ ] **Step 3: Install on this machine and smoke-test live.**

```bash
claude plugin marketplace add /home/cam/dev-loop-plugin
claude plugin install dev-loop@cam-dev-loop
```

In a scratch repo, confirm a real `git push origin main` is denied without state and allowed with green state.

- [ ] **Step 4: Run the full suite + lint**

Run: `python -m pytest -q && ruff check hooks/ tests/`
Expected: all green.

- [ ] **Step 5: Commit + tag**

```bash
git add tests/test_e2e_enforcement.py
git commit -m "add end-to-end enforcement acceptance test

cam"
git tag v0.1.0
```

---

## Self-Review

**Spec coverage:** every spec section maps to a task. Plugin layout -> Tasks 1, 7, 8, 9, 11. Conductor/delegation -> Tasks 9, 10. Loop-state file -> Tasks 2, 3. Hooks + re-gate -> Tasks 4, 5, 6. docs-current -> Task 6. Discipline skill -> Task 10. Distribution -> Tasks 1, 12. The two spec uncertainties (SessionStart injection, Stop blocking) -> Task 0 + the Task 5 warning-only design.

**Placeholder scan:** the only intentionally-deferred content is Task 10's final `SKILL.md` text, which by the writing-skills Iron Law cannot be pre-written; its acceptance is the passing pressure scenarios. All code steps carry runnable code.

**Type consistency:** gate names (`plan_review`/`dennis`/`codex`), risk levels (`R0`-`R3`), state keys (`gates`/`remediation`/`docs_current`/`slice_base`), and function signatures (`is_mergeable`, `gate_status`, `docs_touched_since`) are used identically across Tasks 2-6 and 12.

## Execution Handoff

Gate this plan through Codex first (Cam's instruction: build only on Codex GO). On GO, subagent-driven execution is recommended: a fresh subagent per task, two-stage review between tasks, with the conductor sequencing and the dennis/Codex gates applied to the plugin's own work.
