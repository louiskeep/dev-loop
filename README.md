# dev-loop

A Claude Code plugin that runs development work as a **conductor + hook-enforced
loop**: the main thread commands and orchestrates, heavy work is delegated by
role, and the four ways the loop tends to drift are made hard to do in-session.

## What it enforces

The four drift modes:

1. Self-certified "done" (merge without an independent gate on the exact artifact).
2. Remediation that never gets re-reviewed (fix lands, gate not re-run).
3. Risk under-classification (R2/R3 treated as R1, collapsing required gates).
4. Docs/roadmap drift (slice closed with a stale roadmap or shipped-log).

A PreToolUse hook (`gate_guard`) blocks pushes/merges to a protected branch
unless `.loop-state.json` shows the required green gates on the exact landing
commit. A Stop hook (`done_claim_check`) warns when a "done" claim is not
gate-clean. A `conducting-the-loop` skill carries the judgment steps the hooks
cannot decide, and the dev-rules are bundled under `rules/`.

## This is not a hard boundary

Read this before relying on it. The in-session hooks are **best-effort workflow
enforcement, not a security boundary**:

- A local command hook only fires when Claude Code invokes the Bash tool. A hook
  that times out, fails to start, or is bypassed (another terminal, another tool,
  CI) gates nothing.
- The guard deliberately does not chase adversarial obfuscation (interpreter
  wrappers like `bash -c '...'`, transient config aliases like `git -c alias.x=push x`,
  exotic `push.default`). That is an unwinnable arms race and the wrong threat
  model for a single trusted operator whose risk is skipping under pressure.
- Gate independence is procedural, not cryptographic: the state records who
  reviewed, when, and which commit, but it cannot prove the review ran.

**The real wall for a protected branch is server-side branch protection** (or a
remote pre-receive hook) with required status checks and required reviewers. Set
that up on the remote if you need a boundary a determined or external actor
cannot cross. The plugin makes the drift modes hard to do by accident or under
pressure; it does not make them impossible.

## Skills

The conductor loads a skill on demand at each phase, so orchestration stays lean:

- `conducting-the-loop` — the judgment steps and the phase spine (loaded at session start).
- `orchestrating-parallel-agents` — DEVELOP: fire independent research and parallelizable work
  concurrently, hand off safely, and balance load. Its `parallel-builds-with-worktrees.md` covers the
  concrete concurrent-writer setup.
- `verifying-changed-units` — VERIFY: measure coverage and mutation on the changed units.
- `running-the-gate` — REVIEW / GATE / REMEDIATE: dennis then Codex on the exact commit, per-finding
  root-cause remediation, re-gate on the fixed commit.
- `documenting-the-slice` — DOCUMENT: roadmap and shipped-log close-out.

## Config

Plugin defaults live in `config.json`; a per-repo `.loop-config.json` at the
target repo root overrides them:

| Key | Meaning |
|---|---|
| `protected_branches` | Branches whose updates are gated (default `["main", "master"]`) |
| `roadmap_paths` / `shipped_log_paths` | Paths the docs-current check requires the slice to touch |
| `codex_required_risks` | Risk levels that require the Codex gate (default `["R2", "R3"]`) |
| `escape_hatch` | When true, a leading inline `DEVLOOP_OVERRIDE=<reason>` on a command allows it once and logs to `.loop-audit.log` |

## Usage

The state file is driven through `loop_state.py` (never edited by hand):

```bash
python hooks/loop_state.py init --slice <name> --risk R2 --rationale "<why>"
python hooks/loop_state.py record-gate dennis --status green --reviewer dennis --commit HEAD
python hooks/loop_state.py record-gate codex  --status green --reviewer codex  --commit HEAD
python hooks/loop_state.py check-merge --landing HEAD
```

`init` adds `.loop-state.json` to the target repo's `.gitignore`; it is
machine-local.

## Install

```bash
claude plugin marketplace add <your-github-user>/dev-loop
claude plugin install dev-loop@cam-dev-loop
```

(Or `claude plugin marketplace add <path-to-a-local-clone>` to try it from a checkout.)

## A note on compound commands

The guard evaluates one plain git command. A push or merge combined with shell
operators (`&&`, `;`, `|`, command substitution) or run through a wrapper is
denied rather than parsed, so run the gated step on its own line. For example use
`git commit -m ...` then `git push origin <branch>`, not `git commit ... && git push ...`,
when the push targets a protected branch.

## Tests

```bash
uv run --python 3.11 --with pytest -- pytest -q
```
