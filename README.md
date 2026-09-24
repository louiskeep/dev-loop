# dev-loop

dev-loop is a Claude Code plugin. It lets one orchestrator run development
work the way a small team does. Each phase has an owner: a planner, a
builder, a tester, a reviewer, and a documenter. Each phase also has a
defined exit condition. The main thread is the conductor. It commands and
directs the work. Heavy work goes to an agent by role. A hook-enforced loop
makes four common failure patterns hard to trigger in the session.

## The loop, phase by phase

```text
FRAME -> PLAN -> DEVELOP -> SELF-CHECK -> VERIFY -> REVIEW -- clean --> GATE -> DOCUMENT
                                                |
                                             findings
                                                v
                                           REMEDIATE
                                                |
                                                v
                                     SELF-CHECK -> VERIFY -> REVIEW
```

A finding from REVIEW returns through REMEDIATE, then SELF-CHECK and VERIFY
again, then back to REVIEW. GATE reads the evidence for the exact commit. It
does not repeat REVIEW.

### FRAME — state the problem

State the problem, the definition of done, the boundaries, the applicable
rulebooks, and the risk level. Check for reuse before you build. Exit
condition: an understood request and a recorded risk level, from R0 to R3.

### PLAN — write the approach

For R2 and R3 work, write the plan to a file. Get an independent plan
review before you build. Define the observable behavior, the failure
modes, and the acceptance tests before you build. A planner, a builder, or
another qualified contributor can write the tests. A later contributor
must not weaken an agreed acceptance test. Exit condition: the plan on
file, plus the required review evidence.

### DEVELOP — build the approved plan

Build the approved approach in small, recoverable slices. Keep the agreed
acceptance criteria. Record any approved change to a criterion. Send
independent or parallel work out at the same time, through the
`orchestrating-parallel-agents` skill, so the loop does not stall on one
thread. Exit condition: the intended behavior built, with the checks ready
to run.

### SELF-CHECK — the author's own pass

Read your own diff and the surrounding context before you hand the work
off. Run the checks that apply. Check the prose for truth and clarity, and
write down any known limits. Exit condition: evidence that an independent
reviewer can use.

### VERIFY — run and measure the tests

This is the testing step. Run the targeted tests and the project's
required tests. Then measure test strength on the changed units, through
the `verifying-changed-units` skill:

- line and branch coverage on the changed functions
- mutation testing on the costly changed units: security, money, and
  referential integrity

A green test suite is necessary evidence. It is not proof that every
property is tested. State what each check covers, and what it does not.
The `test_guard` hook watches this phase from the other side: it flags an
edit that loosens a protected test's assertion instead of fixing the code.
Exit condition: coverage numbers, mutation results, and a list of
failures, skips, and known limits.

### REVIEW — an independent reviewer reads the exact commit

An independent reviewer reads the exact commit, not the author. The
review checks the commit against the plan and the VERIFY evidence,
through the `running-the-gate` skill. dennis reviews first: a
fresh-context, adversarial review with findings by severity. P0 blocks
the merge. P1 needs a fix before merge. P2 is advisory. Each finding gets
a root-cause direction, not a ready-made patch. Exit condition: a verdict,
with its checked and unchecked scope stated.

### REMEDIATE — fix findings at the root

Fix each supported finding at its root cause, not at its symptom. Do not
weaken an agreed acceptance test to clear a finding. Change a test only
through a recorded, approved decision. Any change here returns to
SELF-CHECK, then VERIFY, then REVIEW again. The old gate is stale on the
new commit. Exit condition: findings resolved, accepted through a
recorded exception, or returned to the human for direction.

### GATE — sign off on the exact commit

For R2 and R3 work, add the Codex cross-model gate on the same commit
that REVIEW read. Codex reviews it. If Codex is down, the highest
available Claude model reviews instead, and the gate is never skipped.
GATE reads the exact commit, the VERIFY evidence, the review verdicts,
and the release or rollback readiness. It does not repeat REVIEW. It
consumes REVIEW's result. R3 work also needs explicit human authorization
right before the side effect takes place. The `gate_guard` hook enforces
this at the push or merge: it blocks the push unless `.loop-state.json`
shows a green gate on the exact landing commit. Exit condition: a clean
gate on the exact commit that is about to land.

### DOCUMENT — close the slice, update the roadmap

Update the docs and the records that the slice affects, through the
`documenting-the-slice` skill. Move the finished item off the roadmap.
Add it to the shipped log, dated, with the gate evidence. Update the
roadmap so it carries only the current work and the next-up work. Never
leave a finished item on the roadmap. Reconcile any plan whose status
line still trails the code. A slice is not done until its docs match
reality. The `done_claim_check` hook warns when a "done" claim skips this
step. Exit condition: current docs, or a recorded reason why none were
needed.

## Roles: the loop as a small team

The conductor is the one constant thread through every phase. It frames
the work, sets the risk level, and delegates each phase to the right tier
below. It also does the light work itself: small edits and the git
commands. It never builds and gates the same change.

| Role | Active phase | Who fills it |
|---|---|---|
| Researcher | Locating or understanding code or docs | The Explore agent, read-only |
| Planner | PLAN, for R2/R3 or new/unclear work | Top-tier model (Opus) |
| Builder | DEVELOP | Sonnet for routine work from an approved plan, and Opus for new or high-risk work |
| Reviewer | REVIEW | dennis: a fresh-context, adversarial reviewer (Opus) |
| Cross-model gate | GATE, for R2/R3 | Codex, or the highest available Claude model if Codex is down |
| Documenter | DOCUMENT | barry: mechanical sync on Haiku, judgment calls on Opus |
| Operator | Git and deploy commands | The conductor itself. An R3 side effect still needs human authorization first. |

Internal communication in the loop is a recorded handoff, not a chat
message. Each delegation names its tier reason. Each phase passes the
next one recorded evidence, not a verbal summary. dev-loop does not send
Slack messages or email itself. Status updates to a human are the
conductor's own job, in its own environment, on top of the loop that
dev-loop enforces underneath.

## What it enforces

The phases above are the intended path. This section covers the hooks
that catch a skipped step. The loop guards against four failure patterns:

1. A merge that skips an independent gate on the exact commit (a
   "self-certified done").
2. A fix that lands without a re-review on the new commit.
3. A risk level set too low, which skips required gates (R2 or R3 treated
   as R1).
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

## Skills by phase

The conductor loads a skill on demand at each phase, so orchestration stays
lean.

| Skill | Active phase | What it does |
|---|---|---|
| `conducting-the-loop` | All phases | Carries the phase order and the judgment steps. Loaded at the start of the session. |
| `orchestrating-parallel-agents` | DEVELOP | Starts independent or parallel work at the same time, and hands it off safely. Its file `parallel-builds-with-worktrees.md` covers the setup for concurrent writers. |
| `verifying-changed-units` | VERIFY | Measures coverage and mutation score on the changed units. |
| `running-the-gate` | REVIEW, GATE, REMEDIATE | Runs dennis, then Codex, on the exact commit, and directs root-cause remediation for each finding. |
| `documenting-the-slice` | DOCUMENT | Closes out the roadmap and the shipped log. |

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
