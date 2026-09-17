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


def validate_state(state: dict) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"unsupported schema_version: {state.get('schema_version')}")
    for key in ("slice", "risk", "risk_rationale", "slice_base", "gates", "remediation"):
        if key not in state:
            raise StateError(f"missing required field: {key}")
    if state["risk"] not in _RISKS:
        raise StateError(f"invalid risk: {state['risk']!r}")
    for name, g in state["gates"].items():
        if g.get("status") not in ("green", "red"):
            raise StateError(f"gate {name} has invalid status")
        if name in _ARTIFACT_GATES:
            for f in ("reviewer", "at_commit", "ts"):
                if f not in g:
                    raise StateError(f"gate {name} missing {f}")


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
  - `classify(command: str, protected: list[str], current_branch: str) -> tuple[str, str | None]` returning `("allow", None)`, `("deny", reason)`, or `("check", landing_ref)`.
  - a hook `main()` reading stdin JSON, exiting `0` (allow) or `2` (deny with the PreToolUse JSON contract).

**Classification contract (fail closed on doubt):**
- `("allow", None)` only when the command is NOT in the push / merge / `gh pr merge` family, OR is a push whose resolved target branch is not protected.
- `("check", ref)` for the supported protected-branch forms, with `ref` the source to resolve into the landing commit:
  - on a protected branch, `git push [remote] [protected]` or bare `git push` -> ref `HEAD`
  - `git push [remote] <src>:<protected>` -> ref `<src>`
  - `git push [remote] <protected>` from any branch -> ref `<protected>`
  - on a protected branch, `git merge --ff-only <ref>` -> ref `<ref>`
- `("deny", reason)` for everything else that touches the protected family: compound commands (`&&`, `;`, `|`, backticks, `$(`), indirection (`git -C`, leading `cd `), `--all`, `--mirror`, a non-`--ff-only` `git merge` while on a protected branch (may create a merge commit), `gh pr merge` (validates local checkout, not PR head), and any push/merge form it cannot parse into the supported set.

- [ ] **Step 1: Write failing tests** — a full matrix:
  - allow: `git status`; `git push origin feat/x` (on `feat/x`); `git commit -m x`.
  - check: `git push origin main` (on main); `git push origin HEAD:main`; `git push origin feat/x:main`; `git push --force origin main`; `git merge --ff-only feat/x` (on main).
  - deny: `git merge feat/x` (on main, no --ff-only); `git merge feat/x && git push origin main`; `git -C /other push origin main`; `cd /other && git push origin main`; `git push --all origin`; `git push --mirror`; `gh pr merge 12`; `git push origin main:feat/x` is allow (target feat/x, not protected).
  - integration: feed stdin JSON to `python hooks/gate_guard.py`; a `check` on an ungated repo -> exit 2 deny; on a green-gated repo where landing == gate commit -> exit 0; a `deny` classification -> exit 2 regardless of state; malformed state on a `check` op -> exit 2 (fail closed).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `hooks/gate_guard.py`**

```python
"""PreToolUse hook: gate a defined set of protected-branch git ops; fail closed."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop_state as ls  # noqa: E402

_COMPOUND = re.compile(r"&&|\|\||;|\||`|\$\(")
_UNSUPPORTED = re.compile(r"\bgit\s+-C\b|(^|\s)cd\s|--all\b|--mirror\b")


def _is_protected(branch: str, protected: list[str]) -> bool:
    return branch in protected


def classify(command: str, protected: list[str], current_branch: str) -> tuple[str, str | None]:
    c = command.strip()
    is_push = bool(re.search(r"\bgit\s+push\b", c))
    is_merge = bool(re.search(r"\bgit\s+merge\b", c))
    is_gh_merge = bool(re.search(r"\bgh\s+pr\s+merge\b", c))
    if not (is_push or is_merge or is_gh_merge):
        return ("allow", None)
    if is_gh_merge:
        return ("deny", "gh pr merge is not gated in-session (it validates the local checkout, "
                        "not the PR head). Merge via a gated push or use server-side protection.")
    if _COMPOUND.search(c) or _UNSUPPORTED.search(c):
        return ("deny", "compound or indirected git command touching the push/merge family; "
                        "run the gated steps explicitly so the exact landing commit can be checked.")
    if is_merge:
        # only a fast-forward merge INTO a protected branch is checkable
        if current_branch not in protected:
            return ("allow", None)  # merging into a feature branch
        if "--ff-only" not in c:
            return ("deny", "non-fast-forward merge into a protected branch may create an "
                            "unreviewed merge commit; use --ff-only or gate the merge commit.")
        m = re.search(r"\bgit\s+merge\s+--ff-only\s+(\S+)", c)
        return ("check", m.group(1)) if m else ("deny", "cannot parse merge source ref")
    # push forms
    m = re.search(r"\bgit\s+push\b[^\n]*?(\S+):(\S+)\s*$", c)  # src:dst
    if m:
        src, dst = m.group(1), m.group(2)
        return ("check", src) if _is_protected(dst, protected) else ("allow", None)
    # explicit trailing branch: git push [remote] <branch>
    m = re.search(r"\bgit\s+push\b(?:\s+--force\S*|\s+-f)?\s+\S+\s+(\S+)\s*$", c)
    if m:
        dst = m.group(1)
        return ("check", dst) if _is_protected(dst, protected) else ("allow", None)
    # bare push (no ref): gated only when currently on a protected branch
    if current_branch in protected:
        return ("check", "HEAD")
    return ("allow", None)


def _deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    return 2


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
        root = ls.repo_root(cwd)
        config = ls.load_config(root)
        protected = config.get("protected_branches", ["main", "master"])
        branch = _current_branch(root)
        action, ref = classify(command, protected, branch)
        if action == "allow":
            return 0
        if action == "deny":
            return _deny(f"dev-loop: {ref}")
        # action == "check": resolve landing commit and consult state
        landing = ls.rev_parse(root, ref)
        state = ls.load_state(root)
        if state is None:
            return _deny("dev-loop: no .loop-state.json for this slice; run 'loop_state.py init' "
                         "and pass the gates before touching a protected branch.")
        ok, reasons = ls.is_mergeable(state, landing, root, config)
        return 0 if ok else _deny("dev-loop blocks this operation: " + "; ".join(reasons))
    except (ls.StateError, ls.GitError) as exc:
        return _deny(f"dev-loop fail-closed (guard error): {exc}")
    except Exception as exc:
        return _deny(f"dev-loop fail-closed (unexpected guard error): {exc}")


def _current_branch(root: Path) -> str:
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
```

(Implementation note: replace the `ls.rev_parse.__self__ if False` placeholder with a direct `_current_branch(root)` call; it is written that way only to flag that branch resolution uses the helper below, not `cwd`.)

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

**Type consistency:** `schema_version`/`slice`/`risk`/`risk_rationale`/`slice_base`/`gates`/`remediation` and the signatures `repo_root`, `rev_parse`, `load_state`, `validate_state`, `gate_status(state,gate,landing_commit)`, `docs_current(root,slice_base,landing,config)`, `is_mergeable(state,landing,root,config)`, and `classify(command,protected,current_branch)` are used identically across Tasks 2-7 and 12. Config key is `protected_branches` throughout.

## Execution Handoff

Re-gate this plan through Codex (Cam's rule: build only on Codex GO). On GO, subagent-driven execution: fresh subagent per task, two-stage review between tasks, conductor sequencing, dennis/Codex gates on the plugin's own work.
