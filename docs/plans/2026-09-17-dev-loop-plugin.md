# dev-loop plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package Cam's development loop into a Claude Code plugin that makes self-certified merges hard to do in-session and guides the judgment steps, with the main thread acting as a conductor that delegates heavy work.

**Architecture:** A standalone git repo published as a plugin. A PreToolUse hook (`gate_guard.py`) classifies git commands and blocks a defined set of protected-branch operations unless a commit-keyed `.loop-state.json` shows the required green gates on the exact landing commit, failing closed on anything it cannot confidently classify. A Stop hook (`done_claim_check.py`) warns (via `additionalContext`) on an unbacked "done" claim. A discipline skill (`conducting-the-loop`) carries the judgment steps. All state is read/written only through `loop_state.py`, which validates a versioned schema.

**Tech Stack:** Python 3.11 (standard library only in hooks), pytest, Claude Code plugin format (contracts verified against docs v2.1.248+ and an empirical spike in Task 0).

**Spec:** `docs/specs/2026-09-17-dev-loop-plugin-design.md`

## Global Constraints

- This is strong in-session workflow enforcement, NOT a hard git boundary. A local command hook that times out, fails to start, or is bypassed (another terminal, another tool, CI) does not gate anything. The README states this and documents server-side branch protection as the real wall. No task may describe the hooks as a security boundary.
- Python 3.11+, standard library only for the hook/CLI modules (`json`, `sys`, `subprocess`, `re`, `pathlib`). No pip dependencies at runtime.
- Repo root is always resolved with `git rev-parse --show-toplevel` (run via `git -C <cwd>`), never assumed equal to `cwd`. State and config load from that root.
- PreToolUse block contract: exit code `2` with stdout `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"<text>"}}`. Allow = exit `0` with no `permissionDecision`.
- Stop hook contract: Stop hooks in this build CAN block (`decision:"block"`), but this plugin deliberately warns instead of blocking (text heuristics false-positive). Output `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"<text>"}}` at exit `0` so the model re-checks. Honor `stop_hook_active == true` by exiting `0` immediately with no output.
- hooks.json command form: a command STRING with the argument inline, matching the proven installed-plugin pattern: `{"type":"command","command":"\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh\" gate_guard","timeout":10}`. Always quote `${CLAUDE_PLUGIN_ROOT}`. Task 0 verifies hooks actually fire before relying on this.
- State file `.loop-state.json` at the target repo root: read/written ONLY through `loop_state.py`; `schema_version` is `1`; malformed, schema-invalid, or missing-required-field state fails the merge check CLOSED. `init` adds `.loop-state.json` to the target repo's `.gitignore`.
- `risk` is required and validated against `R0`/`R1`/`R2`/`R3`. A missing or unknown risk fails the merge check closed (it must not silently skip the Codex requirement). Codex-required risks: `R2`, `R3`.
- Gate names are exactly `plan_review`, `dennis`, `codex`. Each artifact gate record carries `status`, `reviewer`, `ts`, and `at_commit`.
- docs-current is COMPUTED LIVE at the merge check from `slice_base..<landing commit>`; it is never a stored settable boolean.
- Do not place `skills/`, `agents/`, `commands/`, or `hooks/` inside `.claude-plugin/`. Only `plugin.json` and `marketplace.json` live there.

---

### Task 0: Verify hook contracts empirically (spike)

**Why first:** The plan depends on three behaviors the docs and prior research describe inconsistently: does a SessionStart `command` hook's `additionalContext` reach the model, does a PreToolUse exit-2 deny actually block, and what does a Stop hook's output do. Confirm before building. Output is a recorded finding; the throwaway plugin is deleted.

**Files:**
- Create (throwaway): `/tmp/loop-spike/plugin/.claude-plugin/plugin.json`, `/tmp/loop-spike/plugin/hooks/hooks.json`, `/tmp/loop-spike/plugin/hooks/run-hook.sh`, three tiny Python hooks, and `/tmp/loop-spike/marketplace/.claude-plugin/marketplace.json` pointing at the plugin.

- [ ] **Step 1: Build a valid throwaway marketplace + plugin.** The plugin registers: SessionStart(`startup`) emitting `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"SPIKE_SENTINEL_9f3"}}`; PreToolUse(`Bash`) that exits 2 with a deny payload when the command contains `SPIKE_BLOCK`; Stop that emits `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"SPIKE_STOP_CTX"}}`.

- [ ] **Step 2: Install and observe.**

```bash
claude plugin marketplace add /tmp/loop-spike/marketplace
claude plugin install loop-spike@loop-spike-mkt
```
Start a fresh session. (a) Ask the model whether it sees `SPIKE_SENTINEL_9f3` (SessionStart additionalContext reached context?). (b) Run a Bash command containing `SPIKE_BLOCK`; confirm it is denied. (c) Finish a turn and observe whether `SPIKE_STOP_CTX` influences the next turn (Stop additionalContext reached the model?).

- [ ] **Step 3: Record findings** in the spec under a new "## Verified hook behavior" section: the three yes/no results and the exact working output shapes. Delete `/tmp/loop-spike`, remove the marketplace/plugin.

- [ ] **Step 4: Lock the mechanisms** from the findings. If SessionStart `additionalContext` reaches context, Task 7 uses the `session_start` command hook as the identity reinforcement; if not, Task 7 relies on the skill description trigger alone and records that. If Stop `additionalContext` does not reach the model, Task 5 falls back to top-level `systemMessage` (user-visible). Note both decisions in the spec.

---

### Task 1: Plugin scaffold and manifests

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `config.json`, `README.md`, `pyproject.toml`

**Interfaces:**
- Produces: an installable, valid plugin; `config.json` default keys consumed by `loop_state.py`.

- [ ] **Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "dev-loop",
  "displayName": "Dev Loop",
  "version": "0.1.0",
  "description": "Conductor + hook-enforced development loop: makes self-certified merges hard in-session, guides the judgment steps.",
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
  "protected_branches": ["main", "master"],
  "roadmap_paths": ["docs/ROADMAP.md"],
  "shipped_log_paths": ["docs/backlog/RECENTLY-SHIPPED.md"],
  "codex_required_risks": ["R2", "R3"],
  "escape_hatch": false
}
```

- [ ] **Step 4: Write `README.md`** stating plainly: the plugin is in-session workflow enforcement, not a hard git boundary; a hook that never runs cannot gate; use server-side branch protection (or a remote pre-receive hook) for a true wall; gate independence is procedural (the state records who/when/commit, it cannot prove a review ran). Include the `.loop-config.json` per-repo override keys.

- [ ] **Step 5: Write `pyproject.toml`** with `[tool.pytest.ini_options]` `testpaths = ["tests"]`. No build deps.

- [ ] **Step 6: Validate**

Run: `claude plugin validate ./`
Expected: passes.

- [ ] **Step 7: Commit** (`git add` the created files; message ends with a line `cam`).

---

### Task 2: `loop_state.py` core library

**Files:**
- Create: `hooks/loop_state.py`
- Test: `tests/test_loop_state.py`

**Interfaces:**
- Produces (consumed by Tasks 3-6, 12):
  - `STATE_FILENAME = ".loop-state.json"`, `REPO_CONFIG_FILENAME = ".loop-config.json"`, `SCHEMA_VERSION = 1`
  - `class GitError(Exception)`, `class StateError(Exception)`
  - `repo_root(start: Path) -> Path` (via `git -C start rev-parse --show-toplevel`; raises `GitError`)
  - `rev_parse(repo_root: Path, ref: str) -> str`
  - `load_config(repo_root: Path) -> dict`
  - `validate_state(state: dict) -> None` (raises `StateError` on missing/unknown fields, bad risk, bad schema_version)
  - `load_state(repo_root: Path) -> dict | None` (validates; malformed JSON or schema raises `StateError`)
  - `save_state(repo_root: Path, state: dict) -> None`
  - `gate_status(state: dict, gate: str, landing_commit: str) -> str` returns `"green" | "stale" | "red" | "missing"` (stale when `at_commit != landing_commit`)
  - `docs_current(repo_root: Path, slice_base: str, landing_commit: str, config: dict) -> dict` -> `{"roadmap": bool, "shipped_log": bool}`
  - `is_mergeable(state: dict, landing_commit: str, repo_root: Path, config: dict) -> tuple[bool, list[str]]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_loop_state.py
import subprocess
from pathlib import Path
import pytest
from hooks import loop_state as ls


def _git(t, *a): subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _repo(tmp):
    _git(tmp, "init"); _git(tmp, "config", "user.email", "t@t"); _git(tmp, "config", "user.name", "t")
    (tmp / "a").write_text("1"); _git(tmp, "add", "-A"); _git(tmp, "commit", "-m", "base")
    return ls.rev_parse(tmp, "HEAD")


def _state(head, **over):
    base = {
        "schema_version": 1, "slice": "s", "risk": "R1", "risk_rationale": "x",
        "slice_base": head,
        "gates": {"dennis": {"status": "green", "reviewer": "dennis", "at_commit": head, "ts": "t"}},
        "remediation": {"open_findings": 0},
    }
    base.update(over)
    return base


def test_gate_status_green_stale_missing_red(tmp_path):
    head = _repo(tmp_path)
    assert ls.gate_status(_state(head), "dennis", head) == "green"
    assert ls.gate_status(_state(head), "dennis", "OTHER") == "stale"
    assert ls.gate_status(_state(head, gates={}), "dennis", head) == "missing"
    red = _state(head); red["gates"]["dennis"]["status"] = "red"
    assert ls.gate_status(red, "dennis", head) == "red"


def test_validate_rejects_bad_risk(tmp_path):
    head = _repo(tmp_path)
    with pytest.raises(ls.StateError):
        ls.validate_state(_state(head, risk="R9"))


def test_is_mergeable_r1_needs_dennis_only(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head), head, tmp_path, {"codex_required_risks": ["R2", "R3"],
                                  "roadmap_paths": [], "shipped_log_paths": []})
    assert ok and reasons == []


def test_is_mergeable_r2_needs_codex(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head, risk="R2"), head, tmp_path,
                                  {"codex_required_risks": ["R2", "R3"], "roadmap_paths": [], "shipped_log_paths": []})
    assert not ok and any("codex" in r for r in reasons)


def test_is_mergeable_blocks_when_landing_differs(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head), "DIFFERENT", tmp_path,
                                  {"codex_required_risks": ["R2", "R3"], "roadmap_paths": [], "shipped_log_paths": []})
    assert not ok and any("stale" in r or "dennis" in r for r in reasons)


def test_is_mergeable_requires_docs_when_configured(tmp_path):
    head = _repo(tmp_path)
    ok, reasons = ls.is_mergeable(_state(head), head, tmp_path,
                                  {"codex_required_risks": ["R2", "R3"],
                                   "roadmap_paths": ["docs/ROADMAP.md"], "shipped_log_paths": ["docs/SHIPPED.md"]})
    assert not ok and any("roadmap" in r for r in reasons)


def test_missing_risk_fails_closed(tmp_path):
    head = _repo(tmp_path)
    s = _state(head); del s["risk"]
    with pytest.raises(ls.StateError):
        ls.validate_state(s)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_loop_state.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `hooks/loop_state.py`**

```python
"""State store and merge-gate logic for the dev-loop plugin.

State is validated on load. The merge decision compares each required gate's
attested commit to the commit an operation would land on a protected branch.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATE_FILENAME = ".loop-state.json"
REPO_CONFIG_FILENAME = ".loop-config.json"
SCHEMA_VERSION = 1
_RISKS = {"R0", "R1", "R2", "R3"}
_ARTIFACT_GATES = {"dennis", "codex"}


class GitError(Exception):
    pass


class StateError(Exception):
    pass


def repo_root(start: Path) -> Path:
    try:
        out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitError(f"not a git repo at {start}: {exc}") from exc
    return Path(out.stdout.strip())


def rev_parse(root: Path, ref: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", ref],
                             capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitError(f"cannot resolve ref {ref}: {exc}") from exc
    return out.stdout.strip()


def _plugin_root() -> Path:
    import os
    return Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)


def load_config(root: Path) -> dict:
    config: dict = {}
    plugin_cfg = _plugin_root() / "config.json"
    if plugin_cfg.exists():
        config.update(json.loads(plugin_cfg.read_text()))
    repo_cfg = root / REPO_CONFIG_FILENAME
    if repo_cfg.exists():
        config.update(json.loads(repo_cfg.read_text()))
    return config


_TOP_FIELDS = {"schema_version", "slice", "risk", "risk_rationale", "slice_base", "gates", "remediation"}
_GATE_NAMES = {"plan_review", "dennis", "codex"}


def _is_int(v) -> bool:
    return type(v) is int  # rejects bool (type(True) is bool, not int)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_state(state: dict) -> None:
    if not isinstance(state, dict):
        raise StateError("state is not an object")
    if not (_is_int(state.get("schema_version")) and state["schema_version"] == SCHEMA_VERSION):
        raise StateError(f"unsupported schema_version: {state.get('schema_version')!r}")
    unknown = set(state) - _TOP_FIELDS
    if unknown:
        raise StateError(f"unknown top-level field(s): {sorted(unknown)}")
    for key in _TOP_FIELDS:
        if key not in state:
            raise StateError(f"missing required field: {key}")
    for key in ("slice", "risk_rationale", "slice_base"):
        if not _nonempty_str(state[key]):
            raise StateError(f"field {key} must be a non-empty string")
    if not isinstance(state["risk"], str) or state["risk"] not in _RISKS:
        raise StateError(f"invalid risk: {state['risk']!r}")
    if not isinstance(state["gates"], dict):
        raise StateError("gates must be an object")
    for name, g in state["gates"].items():
        if name not in _GATE_NAMES:
            raise StateError(f"unknown gate: {name}")
        if not isinstance(g, dict):
            raise StateError(f"gate {name} is not an object")
        allowed = {"status", "reviewer", "ts"} | ({"at_commit"} if name in _ARTIFACT_GATES else set())
        extra = set(g) - allowed
        if extra:
            raise StateError(f"gate {name} has unknown field(s): {sorted(extra)}")
        missing = allowed - set(g)
        if missing:
            raise StateError(f"gate {name} missing field(s): {sorted(missing)}")
        if g["status"] not in ("green", "red"):
            raise StateError(f"gate {name} has invalid status")
        if not _nonempty_str(g["reviewer"]) or not _nonempty_str(g["ts"]):
            raise StateError(f"gate {name} has empty reviewer/ts")
        if name in _ARTIFACT_GATES and not _nonempty_str(g["at_commit"]):
            raise StateError(f"gate {name} missing at_commit")
    rem = state["remediation"]
    if not isinstance(rem, dict) or set(rem) != {"open_findings"}:
        raise StateError("remediation must be exactly {open_findings}")
    if not _is_int(rem["open_findings"]) or rem["open_findings"] < 0:
        raise StateError("remediation.open_findings must be a non-negative integer")


def load_state(root: Path) -> dict | None:
    path = root / STATE_FILENAME
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise StateError(f"corrupt state file: {exc}") from exc
    validate_state(state)
    return state


def save_state(root: Path, state: dict) -> None:
    (root / STATE_FILENAME).write_text(json.dumps(state, indent=2) + "\n")


def gate_status(state: dict, gate: str, landing_commit: str) -> str:
    g = state.get("gates", {}).get(gate)
    if not g:
        return "missing"
    if g.get("status") != "green":
        return "red"
    if g.get("at_commit") != landing_commit:
        return "stale"
    return "green"


def docs_current(root: Path, slice_base: str, landing_commit: str, config: dict) -> dict:
    out = subprocess.run(["git", "-C", str(root), "diff", "--name-only", f"{slice_base}..{landing_commit}"],
                         capture_output=True, text=True, check=True)
    changed = set(out.stdout.split())
    def _any(paths): return any(p in changed for p in paths)
    return {"roadmap": _any(config.get("roadmap_paths", [])),
            "shipped_log": _any(config.get("shipped_log_paths", []))}


def is_mergeable(state: dict, landing_commit: str, root: Path, config: dict) -> tuple[bool, list[str]]:
    validate_state(state)  # missing/unknown risk fails closed here
    reasons: list[str] = []
    required = ["dennis"]
    if state["risk"] in config.get("codex_required_risks", ["R2", "R3"]):
        required.append("codex")
    for gate in required:
        status = gate_status(state, gate, landing_commit)
        if status != "green":
            reasons.append(f"{gate} gate is {status} for landing commit {landing_commit[:8]}")
    docs = docs_current(root, state["slice_base"], landing_commit, config)
    if config.get("roadmap_paths") and not docs["roadmap"]:
        reasons.append("roadmap not touched in this slice")
    if config.get("shipped_log_paths") and not docs["shipped_log"]:
        reasons.append("shipped_log not touched in this slice")
    if state.get("remediation", {}).get("open_findings", 0):
        reasons.append(f"{state['remediation']['open_findings']} open remediation finding(s)")
    return (not reasons, reasons)
```

- [ ] **Step 4: Run tests to verify pass** — `python -m pytest tests/test_loop_state.py -v` → PASS.

- [ ] **Step 5: Commit.**

---

### Task 3: `loop_state.py` CLI

**Files:**
- Modify: `hooks/loop_state.py` (add `main()` + argparse)
- Test: `tests/test_loop_state_cli.py`

**Interfaces:**
- Consumes: Task 2.
- Produces: subcommands run as `python hooks/loop_state.py <cmd>` with the target repo resolved from `--repo` (default: cwd, then `repo_root`):
  - `init --slice NAME --risk R2 --rationale TEXT` (sets `slice_base` and `schema_version`, empty gates, zero findings; appends `.loop-state.json` to the repo `.gitignore` if absent)
  - `record-gate GATE --status green|red --reviewer NAME [--commit REF]` (`REF` defaults to `HEAD`; resolves to a SHA; stamps `ts`)
  - `set-findings N`
  - `check-merge --landing REF` (exit 0 mergeable, exit 1 with reasons on stderr)
  - `status`
  There is NO `set-docs-current`; docs status is computed, never set.

- [ ] **Step 1: Write failing tests** covering: `init` writes valid state and gitignores the state file; `record-gate` without `--reviewer` errors; a green dennis on HEAD makes an R1 slice mergeable against `--landing HEAD` only when the configured docs are absent (use an empty-docs `.loop-config.json` in the temp repo); a new commit makes `check-merge --landing HEAD` fail with "stale"; R2 without codex fails.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Add `main()` to `hooks/loop_state.py`**

```python
def _resolve_root(repo_arg: str | None) -> Path:
    import os
    start = Path(repo_arg or os.environ.get("PWD") or ".")
    return repo_root(start)


def main(argv: list[str] | None = None) -> int:
    import argparse, sys, datetime
    p = argparse.ArgumentParser(prog="loop_state")
    p.add_argument("--repo", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("init")
    pi.add_argument("--slice", required=True)
    pi.add_argument("--risk", required=True, choices=sorted(_RISKS))
    pi.add_argument("--rationale", required=True)
    pg = sub.add_parser("record-gate")
    pg.add_argument("gate", choices=["plan_review", "dennis", "codex"])
    pg.add_argument("--status", required=True, choices=["green", "red"])
    pg.add_argument("--reviewer", required=True)
    pg.add_argument("--commit", default="HEAD")
    pf = sub.add_parser("set-findings"); pf.add_argument("n", type=int)
    pc = sub.add_parser("check-merge"); pc.add_argument("--landing", default="HEAD")
    sub.add_parser("status")
    args = p.parse_args(argv)
    root = _resolve_root(args.repo)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if args.cmd == "init":
        state = {"schema_version": SCHEMA_VERSION, "slice": args.slice, "risk": args.risk,
                 "risk_rationale": args.rationale, "slice_base": rev_parse(root, "HEAD"),
                 "gates": {}, "remediation": {"open_findings": 0}}
        save_state(root, state)
        gi = root / ".gitignore"
        lines = gi.read_text().splitlines() if gi.exists() else []
        if STATE_FILENAME not in lines:
            gi.write_text(("\n".join(lines + [STATE_FILENAME])).strip() + "\n")
        return 0

    state = load_state(root)
    if state is None:
        print("no .loop-state.json; run 'init' first", file=sys.stderr); return 1

    if args.cmd == "record-gate":
        entry = {"status": args.status, "reviewer": args.reviewer, "ts": now}
        if args.gate in _ARTIFACT_GATES:
            entry["at_commit"] = rev_parse(root, args.commit)
        state.setdefault("gates", {})[args.gate] = entry
        save_state(root, state); return 0
    if args.cmd == "set-findings":
        state.setdefault("remediation", {})["open_findings"] = args.n
        save_state(root, state); return 0
    if args.cmd == "check-merge":
        landing = rev_parse(root, args.landing)
        ok, reasons = is_mergeable(state, landing, root, load_config(root))
        if ok:
            return 0
        print("; ".join(reasons), file=sys.stderr); return 1
    if args.cmd == "status":
        print(json.dumps(state, indent=2)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit.**

---

### Task 4: `gate_guard.py` PreToolUse hook

**Files:**
- Create: `hooks/gate_guard.py`
- Test: `tests/test_gate_guard.py`

**Interfaces:**
- Consumes: `loop_state` (`repo_root`, `rev_parse`, `load_state`, `load_config`, `is_mergeable`, `StateError`, `GitError`).
- Produces:
  - `classify(tokens: list[str], protected: list[str], current_branch: str) -> tuple[str, str | None]` returning `("allow", None)`, `("deny", reason)`, `("check", ref)`, `("check_bare_push", remote|None)`, or `("maybe_alias", sub)`. It takes shlex-tokenized argv and does pure argv parsing (no git calls).
  - a hook `main()` that tokenizes with `shlex.split`, strips a leading `DEVLOOP_OVERRIDE=` assignment, rejects per-token shell metacharacters, resolves git-dependent actions (alias lookup, bare-push via `push.default`/`remote.push`, landing `ref^{commit}`), consults state, and exits `0`/`2`.

**Classification contract (strict argv parser, allow only the canonical safe set, deny on any doubt):**
- Not `git`/`gh` as argv[0] (env prefix, wrapper): DENY if the argv mentions push/merge/pull, else ALLOW.
- `gh pr merge`: DENY; other `gh`: ALLOW.
- `git` global options (`-C`, `--git-dir`, `--work-tree`, `-c`, flags) are skipped to find the subcommand; a repo-retargeting option (`-C`/`--git-dir`/`--work-tree`) plus a push/merge/pull/pr subcommand DENIES; a retargeting option with a safe subcommand (`git -C repo status`) ALLOWS.
- `git pull`: DENY on a protected branch (it merges into it), else ALLOW.
- `git merge`: only on a protected branch; options exactly `["--ff-only"]` and exactly one positional, else DENY; returns `("check", ref)` (main resolves `ref^{commit}` to deref tags).
- `git push`: only `--force`/`-f`/`--force-with-lease[=..]` options allowed, else DENY. Positionals after option removal: 0 or 1 -> `("check_bare_push", remote|None)` (main: `push.default=matching` or a configured `remote.<remote>.push` -> DENY; otherwise it pushes the current branch, so protected -> check HEAD, else ALLOW); exactly 2 -> single refspec, strip `refs/heads/`, DENY on delete/empty (`:x`, `x:`, empty src/dst), wildcard, or a `/` in the destination; protected dst -> `("check", src)`, else ALLOW; >2 -> DENY.
- Any other `git` subcommand -> `("maybe_alias", sub)`; main DENIES if `git config alias.<sub>` exists, else ALLOWS (normal builtin).
- Any token carrying a shell metacharacter/expansion (`; & | < > $ ( ) { } \``, newline): DENY if the argv touches push/merge/pull, else ALLOW. Unbalanced quotes (`shlex.split` raises): DENY.
- Escape hatch: on a DENY, if `config.escape_hatch` is true AND a leading inline `DEVLOOP_OVERRIDE=<reason>` assignment is present in the command, ALLOW and append `{ts, command, reason, landing_commit}` to `.loop-audit.log`. Inline only, never the ambient env (a session-wide env var would silently allow everything).

- [ ] **Step 1: Write failing tests** — a full matrix (call `classify` on tokenized argv, and drive `main()` via stdin JSON):
  - allow: `git status`; `git push origin feat/x` (on `feat/x`); `git commit -m x`; `git push origin main:feat/x` (dst feat/x); `git -C repo status`; `git rebase main` (not an alias); bare `git push` on a feature branch (`push.default=simple`, no `remote.origin.push`).
  - check: `git push origin main` (on main); `git push origin HEAD:main`; `git push origin HEAD:refs/heads/main`; `git push origin feat/x:main`; `git push --force origin main`; `git push --force-with-lease origin main`; `git push origin --force-with-lease main`; `git merge --ff-only <annotated-tag>` (on main -> tagged commit); bare `git push` on main (`push.default=simple`).
  - deny: `git merge feat/x` (on main, no --ff-only); `git pull` (on main); `git merge feat/x && git push origin main`; `git push origin main;`; `git push origin main&&x`; `git push origin $BRANCH`; `git push origin main>out`; `git -C /other push origin main`; `cd /other && git push origin main`; `git push --all origin`; `git push --mirror`; `git push origin :main` (delete); `git push origin main:` (empty dst); `git push origin main feat/x` (multiple refspecs); `GIT_SSH=x git push origin main` (env prefix); `git p` where `alias.p` is set; bare `git push` with `push.default=matching`; `gh pr merge 12`; `git push "origin" 'main` (unbalanced quote).
  - integration: `check` on an ungated repo -> exit 2; green-gated repo with landing == gate commit -> exit 0; a `deny` classification -> exit 2 regardless of state; malformed state on a `check` op -> exit 2 (fail closed); annotated-tag source resolves to its commit; `DEVLOOP_OVERRIDE=hotfix git push origin main` with `escape_hatch:true` -> exit 0 and a `.loop-audit.log` line naming reason `hotfix`; with `escape_hatch:false` the override is ignored (exit 2); an ambient `DEVLOOP_OVERRIDE` env var (no inline assignment) does NOT override (exit 2); a `git push origin main` outside any git repo -> exit 0 (nothing to protect).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `hooks/gate_guard.py`**

```python
"""PreToolUse hook: gate protected-branch git ops via a tiny allowlist; fail closed.

classify() is a strict argv parser over shlex tokens. It ALLOWS only a small
canonical set and DENIES anything else in (or possibly in) the push/merge/pull
family, so unrecognized, aliased, or obfuscated forms cannot slip through.
"""
from __future__ import annotations

import datetime
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop_state as ls  # noqa: E402

_PUSH_OPTS = {"--force", "-f", "--force-with-lease"}
_METACHAR = re.compile(r"[;&|<>$(){}`\n]")
_RETARGET_OPTS = {"-C", "--git-dir", "--work-tree"}
_OVERRIDE_RE = re.compile(r"^DEVLOOP_OVERRIDE=(.*)$")
_FAMILY = {"push", "merge", "pull"}


def _strip_heads(ref: str) -> str:
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def _mentions_family(tokens: list[str]) -> bool:
    return bool(_FAMILY & set(tokens)) or ("pr" in tokens and "merge" in tokens)


def _skip_global(tokens: list[str]) -> tuple[bool, int]:
    """Skip git global options; return (repo_retargeted, index_of_subcommand)."""
    i, retarget = 1, False
    while i < len(tokens):
        t = tokens[i]
        if t in _RETARGET_OPTS:
            retarget = True; i += 2; continue
        if t.startswith("--git-dir=") or t.startswith("--work-tree=") or t.startswith("-C"):
            retarget = True; i += 1; continue
        if t == "-c":
            i += 2; continue
        if t.startswith("-"):
            i += 1; continue
        break
    return retarget, i


def classify(tokens: list[str], protected: list[str], current_branch: str) -> tuple[str, str | None]:
    if not tokens:
        return ("allow", None)
    head = tokens[0]
    if head not in ("git", "gh"):
        return ("deny", "unrecognized wrapper/prefix around a push/merge/pull; run plain git") \
            if _mentions_family(tokens) else ("allow", None)
    if head == "gh":
        if "pr" in tokens and "merge" in tokens:  # catches global opts before 'pr merge'
            return ("deny", "gh pr merge is not gated in-session (validates the local checkout, "
                            "not the PR head); use a gated push or server-side protection.")
        return ("allow", None)
    retarget, idx = _skip_global(tokens)
    if idx >= len(tokens):
        return ("allow", None)
    sub, args = tokens[idx], tokens[idx + 1:]
    if retarget and (sub in _FAMILY or sub == "pr"):
        return ("deny", "git -C/--git-dir/--work-tree with a push/merge/pull retargets the repo; "
                        "run it inside that repo without indirection.")
    if sub == "pull":
        return ("deny", "git pull merges into the current branch; while on a protected branch use "
                        "an explicit gated 'git merge --ff-only'.") if current_branch in protected \
            else ("allow", None)
    if sub == "merge":
        if current_branch not in protected:
            return ("allow", None)
        opts = [a for a in args if a.startswith("-")]
        pos = [a for a in args if not a.startswith("-")]
        if opts != ["--ff-only"] or len(pos) != 1:
            return ("deny", "only 'git merge --ff-only <one-ref>' into a protected branch is supported")
        return ("check", pos[0])
    if sub == "push":
        opts = [a for a in args if a.startswith("-")]
        pos = [a for a in args if not a.startswith("-")]
        for o in opts:
            if o.split("=", 1)[0] not in _PUSH_OPTS:
                return ("deny", f"unsupported push option {o}")
        if len(pos) <= 1:
            return ("check_bare_push", pos[0] if pos else None)  # bare or remote-only
        if len(pos) > 2:
            return ("deny", "multiple refspecs are not supported here")
        refspec = pos[1]
        if "*" in refspec:
            return ("deny", "wildcard refspec is not supported here")
        if refspec.count(":") > 1:
            return ("deny", "malformed refspec (multiple colons)")
        src, dst = refspec.split(":", 1) if ":" in refspec else (refspec, refspec)
        if not src or not dst:
            return ("deny", "delete/empty refspec is not supported here")
        dst = _strip_heads(dst)
        if dst.startswith("refs/"):  # non-branch namespace (tags/remotes/...) after stripping heads
            return ("deny", f"unsupported push destination {dst}")
        return ("check", src) if dst in protected else ("allow", None)
    # any other subcommand could be a user alias that expands to a push/merge
    return ("maybe_alias", sub)


def _deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    return 2


def _current_branch(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return ""


def _git_config(root: Path, key: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "config", "--get", key],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return ""


def _bare_push_target(root: Path, remote: str | None, branch: str, protected: list[str]) -> tuple[str, str | None]:
    """Decide a bare/remote-only push: allow (feature), deny (ambiguous), or check HEAD.

    Only push.default in {simple, current} is safe to reason about locally: both push
    the current branch to a same-named remote branch, so a feature branch cannot reach
    a protected one. upstream/tracking can push to a differently-named branch, so deny.
    """
    remote = remote or _git_config(root, f"branch.{branch}.pushRemote") \
        or _git_config(root, "remote.pushDefault") or "origin"
    if _git_config(root, f"remote.{remote}.push"):
        return ("deny", "a configured remote.push refspec makes this push ambiguous; "
                        "push an explicit single refspec.")
    push_default = _git_config(root, "push.default") or "simple"
    if push_default not in ("simple", "current"):
        return ("deny", f"push.default={push_default} may push a differently-named or protected "
                        "branch; push an explicit single refspec.")
    return ("check", "HEAD") if branch in protected else ("allow", None)


def _escape(root: Path, config: dict, reason: str | None, command: str, landing: str) -> bool:
    if not (config.get("escape_hatch") and reason):
        return False
    line = json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "command": command, "reason": reason, "landing_commit": landing})
    with (root / ".loop-audit.log").open("a") as fh:
        fh.write(line + "\n")
    return True


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = event.get("tool_input", {}).get("command", "")
    cwd = Path(event.get("cwd", "."))
    try:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = None
        # inline escape-hatch prefix: DEVLOOP_OVERRIDE=<reason> <git ...> (inline only,
        # never the ambient env: the hook process would otherwise inherit a session-wide
        # override and silently allow everything).
        override = None
        if tokens and (m := _OVERRIDE_RE.match(tokens[0])):
            override, tokens = m.group(1), tokens[1:]
        try:
            root = ls.repo_root(cwd)
        except ls.GitError:
            return 0  # not a git repo: nothing to protect
        config = ls.load_config(root)
        protected = config.get("protected_branches", ["main", "master"])
        branch = _current_branch(root)
        if tokens is None:
            return 0 if _escape(root, config, override, command, "n/a") else \
                _deny("dev-loop: unparseable command (unbalanced quotes)")
        # reject shell metacharacters/expansion carried on any token (only for the family)
        if _mentions_family(tokens) and any(_METACHAR.search(t) for t in tokens):
            return 0 if _escape(root, config, override, command, "n/a") else \
                _deny("dev-loop: shell metacharacter/expansion in a push/merge command; run the gated step alone")

        action, payload = classify(tokens, protected, branch)
        if action == "allow":
            return 0
        if action == "deny":
            return 0 if _escape(root, config, override, command, "n/a") else _deny(f"dev-loop: {payload}")
        if action == "maybe_alias":
            if _git_config(root, f"alias.{payload}"):
                return 0 if _escape(root, config, override, command, "n/a") else \
                    _deny(f"dev-loop: 'git {payload}' is a configured alias; run the explicit command it expands to")
            return 0  # normal builtin (status, rebase, reset, ...); does not push a protected remote branch
        if action == "check_bare_push":
            act, ref = _bare_push_target(root, payload, branch, protected)
            if act == "allow":
                return 0
            if act == "deny":
                return 0 if _escape(root, config, override, command, "n/a") else _deny(f"dev-loop: {ref}")
            landing = ls.rev_parse(root, "HEAD")
        else:  # "check"
            landing = ls.rev_parse(root, payload + "^{commit}")

        state = ls.load_state(root)
        if state is None:
            reason = ("dev-loop: no .loop-state.json for this slice; run 'loop_state.py init' "
                      "and pass the gates before touching a protected branch.")
        else:
            ok, reasons = ls.is_mergeable(state, landing, root, config)
            if ok:
                return 0
            reason = "dev-loop blocks this operation: " + "; ".join(reasons)
        return 0 if _escape(root, config, override, command, landing) else _deny(reason)
    except (ls.StateError, ls.GitError) as exc:
        return _deny(f"dev-loop fail-closed (guard error): {exc}")
    except Exception as exc:
        return _deny(f"dev-loop fail-closed (unexpected guard error): {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass** (including the full classification matrix). Confirm `_current_branch(root)` is used for branch resolution (never `cwd`).
- [ ] **Step 5: Commit.**

---

### Task 5: `done_claim_check.py` Stop hook

**Files:**
- Create: `hooks/done_claim_check.py`
- Test: `tests/test_done_claim_check.py`

**Interfaces:**
- Consumes: `loop_state` (`repo_root`, `rev_parse`, `load_state`, `load_config`, `is_mergeable`).
- Produces: a Stop entrypoint that always exits `0`, emitting `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"<warning>"}}` when a done/merge claim is unbacked. Never blocks. (If Task 0 found `additionalContext` does not reach the model, switch to top-level `{"systemMessage": ...}`.)

- [ ] **Step 1: Write failing tests** — `claims_done` detects done/merge-ready/complete/shipped and ignores in-progress text; `stop_hook_active: true` short-circuits (no output); a done claim with no state emits an `additionalContext` warning; a done claim with a mergeable state is silent; non-done text is silent.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `hooks/done_claim_check.py`**

```python
"""Stop hook: warn (never block) when a done/merge claim lacks gate evidence."""
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
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": message}}))
    return 0


def main_from_event(event: dict) -> int:
    if event.get("stop_hook_active") is True:
        return 0
    if not claims_done(event.get("last_assistant_message", "")):
        return 0
    cwd = Path(event.get("cwd", "."))
    try:
        root = ls.repo_root(cwd)
        state = ls.load_state(root)
        if state is None:
            return _emit("dev-loop: a completion was claimed but there is no .loop-state.json for "
                         "this slice. If this is real work, initialize the slice and run the gates.")
        landing = ls.rev_parse(root, "HEAD")
        ok, reasons = ls.is_mergeable(state, landing, root, ls.load_config(root))
        if not ok:
            return _emit("dev-loop: completion claimed but the slice is not gate-clean: " + "; ".join(reasons))
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

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit.**

---

### Task 6: `session_start.py` conductor preamble emitter

**Files:**
- Create: `hooks/session_start.py`, `skills/conducting-the-loop/PREAMBLE.md`
- Test: `tests/test_session_start.py`

**Interfaces:**
- Produces: a SessionStart entrypoint emitting `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<preamble>"}}` at exit 0. Gated on the Task 0 finding: if SessionStart `additionalContext` does not reach context, this task instead ships an empty no-op and the skill description trigger is the sole load path (record that in the spec).

- [ ] **Step 1: Write `PREAMBLE.md`** — a short conductor identity pointer: "You are the conductor. Load the `conducting-the-loop` skill. Delegate substantial coding and research; keep decisions and synthesis here."
- [ ] **Step 2: Write failing test** — `session_start.py` prints valid JSON containing the preamble text and the correct `hookEventName`.
- [ ] **Step 3: Implement** `session_start.py` to read `PREAMBLE.md` (relative to `${CLAUDE_PLUGIN_ROOT}`) and print the additionalContext JSON.
- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit.**

---

### Task 7: Register hooks (`hooks/hooks.json` + wrapper)

**Files:**
- Create: `hooks/hooks.json`, `hooks/run-hook.sh`

- [ ] **Step 1: Write `hooks/run-hook.sh`** (dispatches to a Python module by basename)

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python3 "${CLAUDE_PLUGIN_ROOT}/hooks/$1.py"
```

Make it executable.

- [ ] **Step 2: Write `hooks/hooks.json`** using the proven command-string form

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh\" gate_guard", "timeout": 10 }
      ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh\" done_claim_check", "timeout": 10 }
      ] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume|clear|compact|fork", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh\" session_start", "timeout": 10 }
      ] }
    ]
  }
}
```

(If Task 0 found the array `command` form is required instead, use `"command": "...run-hook.sh", "args": ["gate_guard"]` per the finding. Use whichever Task 0 proved fires.)

- [ ] **Step 3: Validate + live smoke test.** `claude plugin validate ./`, then install locally and confirm in a scratch repo that a real `git push origin main` is denied without state and allowed with a green-gated landing commit, and that the SessionStart preamble behaves as Task 0 found.

- [ ] **Step 4: Commit.**

---

### Task 8: Port dennis and barry agents into the plugin

**Files:** Create `agents/dennis.md`, `agents/barry.md`.

- [ ] **Step 1: Copy** `~/.claude/agents/dennis.md` and `~/.claude/agents/barry.md`.
- [ ] **Step 2: Normalize frontmatter** to the plugin agent schema (`name`, `description`, `tools`, `model`); keep dennis review-only (no Write/Edit), barry docs-only.
- [ ] **Step 3: Validate** (`claude plugin validate ./`).
- [ ] **Step 4: Commit.**

---

### Task 9: `role-matrix.md` delegation reference

**Files:** Create `skills/conducting-the-loop/role-matrix.md`.

- [ ] **Step 1: Write** the complexity-tiered role matrix from the spec: the four signals (risk, novelty, blast radius, ambiguity), the role table, agent per tier and reason, and the hard rule (Codex gate always Codex; fallback highest available Claude).
- [ ] **Step 2: Commit.**

---

### Task 10: `conducting-the-loop` discipline skill (writing-skills TDD)

> Does NOT pre-write the final SKILL.md. REQUIRED SUB-SKILL: superpowers:writing-skills. The skill is written only after a failing baseline.

**Files:** Create `skills/conducting-the-loop/SKILL.md`, `tests/skill-scenarios/`.

- [ ] **Step 1: RED** — write pressure scenarios for each of the four drift modes with combined pressure; save under `tests/skill-scenarios/`.
- [ ] **Step 2: RED** — run each on a subagent WITHOUT the skill; record verbatim rationalizations. Drop any mode the no-skill control does not fail.
- [ ] **Step 3: GREEN** — write `SKILL.md`: description = triggering conditions only (no workflow summary); body = conductor identity as a positive recipe, phase sequence referencing `rules/`, delegation referencing `role-matrix.md` (cite the tier reason), and a rationalization table + red-flag list from Step 2, weighted toward risk-under-classification and self-certified-done.
- [ ] **Step 4: GREEN** — micro-test the wording against the no-guidance control (5+ reps, read every flagged match).
- [ ] **Step 5: REFACTOR** — re-run under max pressure; add counters until compliant; record the pass.
- [ ] **Step 6: Commit.**

---

### Task 11: Bundle the rulebooks

**Files:** Create `rules/` (snapshot of `~/dev-rules/*.md`) + `rules/PROVENANCE.md`.

- [ ] **Step 1: Copy** the current `~/dev-rules/` markdown into `rules/`; note source + snapshot date in `PROVENANCE.md`.
- [ ] **Step 2: Fix cross-references** in `SKILL.md`, `PREAMBLE.md`, and `role-matrix.md` to `rules/<file>.md`, not `/home/cam/dev-rules/`.
- [ ] **Step 3: Commit.**

---

### Task 12: End-to-end acceptance test and local install

**Files:** Create `tests/test_e2e_enforcement.py`.

- [ ] **Step 1: Write the e2e test** in a temp git repo (with an empty-docs `.loop-config.json` unless testing docs), driving `gate_guard.main()` with stdin JSON for a `git push origin main` on `main`:
  1. `init` at R2 -> push -> DENY (no gates).
  2. record `dennis` green (reviewer=dennis) on HEAD + touch the configured roadmap/shipped-log or use empty docs config -> push -> still DENY (R2 needs codex).
  3. record `codex` green (reviewer=codex) on HEAD -> push -> ALLOW.
  4. add a new commit -> push -> DENY (gates now attest to the old commit; landing differs).
  5. re-record dennis + codex on the new HEAD -> push -> ALLOW.
  6. a compound `git merge x && git push origin main` -> DENY regardless of state.
- [ ] **Step 2: Run** — `python -m pytest tests/test_e2e_enforcement.py -v` → PASS.
- [ ] **Step 3: Install live** (`claude plugin marketplace add /home/cam/dev-loop-plugin`, `claude plugin install dev-loop@cam-dev-loop`) and smoke-test a real protected-branch push deny/allow.
- [ ] **Step 4: Full suite + lint** — `python -m pytest -q && ruff check hooks/ tests/` → green.
- [ ] **Step 5: Commit + tag `v0.1.0`.**

---

## Self-Review

**Spec coverage:** enforcement model + limits -> Task 1 README, Task 4, Global Constraints. Loop-state schema + validation -> Task 2. CLI (no docs-set) -> Task 3. gate_guard classification + fail-closed + landing-commit -> Task 4. Stop warning -> Task 5. SessionStart mechanism -> Tasks 0, 6, 7. docs-current live -> Task 2 (`docs_current`) consumed by `is_mergeable`. Conductor/delegation -> Tasks 9, 10. rules bundle -> Task 11. Distribution -> Tasks 1, 12. The three contract uncertainties -> Task 0.

**Placeholder scan:** the only deferred content is Task 10's final `SKILL.md` (Iron Law: cannot be pre-written; acceptance = passing scenarios) and the two Task-0-gated mechanism choices (SessionStart form, Stop output field), each with a stated default and fallback. No stub code remains in the module tasks.

**Type consistency:** `schema_version`/`slice`/`risk`/`risk_rationale`/`slice_base`/`gates`/`remediation` and the signatures `repo_root`, `rev_parse`, `load_state`, `validate_state`, `gate_status(state,gate,landing_commit)`, `docs_current(root,slice_base,landing,config)`, `is_mergeable(state,landing,root,config)`, and `classify(tokens,protected,current_branch)` (argv, not a raw string) are used identically across Tasks 2-7 and 12. Config keys are `protected_branches`, `codex_required_risks`, `roadmap_paths`, `shipped_log_paths`, `escape_hatch` throughout; `escape_hatch` is consulted in `gate_guard` (env `DEVLOOP_OVERRIDE` + `.loop-audit.log`).

## Execution Handoff

Re-gate this plan through Codex (Cam's rule: build only on Codex GO). On GO, subagent-driven execution: fresh subagent per task, two-stage review between tasks, conductor sequencing, dennis/Codex gates on the plugin's own work.
