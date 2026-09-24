# dev-loop

dev-loop is a Claude Code plugin. It runs development work as a loop with a
conductor and hook-enforced checks. The main thread commands and directs the
work. Heavy work goes to an agent by role. The plugin makes four common
failure patterns hard to trigger in the session.

## What it enforces

The loop guards against four failure patterns:

1. A merge that skips an independent gate on the exact commit (a
   "self-certified done").
2. A fix that lands without a re-review on the new commit.
3. A risk level set too low, which skips required gates (R2 or R3 treated as
   R1).
4. A slice closed while the roadmap or shipped-log is stale (docs drift).

A PreToolUse hook, `gate_guard`, blocks a push or merge to a protected branch.
It blocks the push unless `.loop-state.json` shows green gates on the exact
landing commit. A Stop hook, `done_claim_check`, warns when a "done" claim
does not match a clean gate. A skill, `conducting-the-loop`, carries the
judgment steps that the hooks cannot decide. The dev-rules ship under
`rules/`.

A fifth failure pattern is test-weakening. Its own PreToolUse hook,
`test_guard`, watches edits to Edit, Write, and MultiEdit. A builder can
loosen an acceptance test to make the suite pass, then call the change
harmless. The hook is a heuristic. It counts direction, not meaning, over the
edit to a protected test file.

The hook finds four patterns:

- `check_metadata=True` removed, or flipped to `False`
- a `.equals(...)` parity assertion removed
- a strong assertion removed (`==`, `!=`, `.equals`, or `is`)
- a `skip`, `skipif`, or `xfail` marker added

The hook does not find these patterns:

- an assertion narrowed in place, for example `x == y` changed to
  `x.shape == y.shape`
- a wider `pytest.approx` tolerance
- a shorter `parametrize` case list

The hook counts raw text, so text inside comments and strings counts too.
This makes the count easy to trick: a commented-out `check_metadata=True`
can hide a real removal. The Edit tool shows only the changed part of the
file, so a moved assertion can look like a removed assertion. This is a
false positive in warn mode. These limits are why block mode is optional
per repo. Additive test writing never triggers the hook.

Each hit is recorded to `.loop-test-guard.log`, at the repo root. The log
includes the ID and type of the agent that made the edit, so a subagent's
shortcut stays visible.

The default mode is warn. In warn mode, the hook logs the hit and lets the
edit through. It gives no permission decision. An explicit allow suppresses
the user's own permission prompt on exactly those edits. Set
`test_guard_mode: "block"` in `config.json`, or in a repo's
`.loop-config.json`, to deny the edit instead. Set block mode only after the
log shows that the heuristic is accurate for your repo. `test_guard_subagent_only`
limits the hook to edits made by subagents.

This hook is a backstop for the workflow. The real defense against a hidden
divergence is still an independent, adversarial gate on the exact artifact.

## This is not a hard boundary

Read this section before you rely on the plugin. The in-session hooks give
best-effort workflow support. They are not a security boundary.

- A local command hook fires only when Claude Code calls the Bash tool. A
  hook that times out, fails to start, or gets bypassed (another terminal,
  another tool, CI) gates nothing.
- The guard does not chase deliberate obfuscation. Examples are an
  interpreter wrapper such as `bash -c '...'`, a temporary git alias such as
  `git -c alias.x=push x`, and an unusual `push.default` value. This kind of
  arms race has no end. It is also the wrong threat model for one trusted
  operator, whose real risk is skipping a step under pressure.
- Gate independence is a procedural record, not a cryptographic proof. The
  state file records who reviewed the work, when, and on which commit. It
  cannot prove that the review took place.

The real wall for a protected branch is server-side branch protection on the
remote, for example required status checks and required reviewers. You can
also use a remote pre-receive hook instead. If you need a boundary that a
determined or external actor cannot cross, set it up on the remote. The
plugin makes the four failure patterns hard to trigger by accident or under
pressure. It does not make them impossible.

## Skills

The conductor loads a skill on demand at each phase, so orchestration stays
lean:

- `conducting-the-loop`: the judgment steps and the phase order. Loaded at
  the start of the session.
- `orchestrating-parallel-agents`: for the DEVELOP phase. Use it to start
  independent and parallel work at the same time, hand work off safely, and
  balance the load. Its file `parallel-builds-with-worktrees.md` covers the
  setup for concurrent writers.
- `verifying-changed-units`: for the VERIFY phase. Measures coverage and
  mutation score on the changed units.
- `running-the-gate`: for the REVIEW, GATE, and REMEDIATE phases. Runs
  dennis, then Codex, on the exact commit. It requires root-cause
  remediation for each finding, then a re-gate on the fixed commit.
- `documenting-the-slice`: for the DOCUMENT phase. Closes out the roadmap
  and the shipped-log.

## Config

Plugin defaults live in `config.json`. A repo can override them with a
`.loop-config.json` file at its root.

| Key | Meaning |
|---|---|
| `protected_branches` | Branches that need a gate before an update. Default: `["main", "master"]`. |
| `roadmap_paths` / `shipped_log_paths` | Paths that the docs-current check requires the slice to touch. |
| `codex_required_risks` | Risk levels that require the Codex gate. Default: `["R2", "R3"]`. |
| `escape_hatch` | When true, a command with a leading `DEVLOOP_OVERRIDE=<reason>` runs once. The plugin logs it to `.loop-audit.log`. |

## Usage

The state file is set through `loop_state.py`. Do not edit
`.loop-state.json` by hand.

```bash
python hooks/loop_state.py init --slice <name> --risk R2 --rationale "<why>"
python hooks/loop_state.py record-gate dennis --status green --reviewer dennis --commit HEAD
python hooks/loop_state.py record-gate codex  --status green --reviewer codex  --commit HEAD
python hooks/loop_state.py check-merge --landing HEAD
```

The `init` command adds `.loop-state.json` to the target repo's
`.gitignore` file. The file is local to your machine.

## Install

```bash
claude plugin marketplace add <your-github-user>/dev-loop
claude plugin install dev-loop@cam-dev-loop
```

To try the plugin from a local checkout, run
`claude plugin marketplace add <path-to-a-local-clone>` instead.

## A note on compound commands

The guard checks one plain git command at a time. It denies a push or merge
that you combine with shell operators (`&&`, `;`, `|`, or command
substitution) or run through a wrapper, instead of parsing it. Run the
gated command on its own line.

For a push to a protected branch, run `git commit -m ...` on one line. Then
run `git push origin <branch>` on the next line. Do not combine them as
`git commit ... && git push ...`.

## Tests

```bash
uv run --python 3.11 --with pytest -- pytest -q
```
