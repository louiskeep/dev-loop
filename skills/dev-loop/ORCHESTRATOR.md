# Dev loop orchestrator

This is the standard procedure. One live Claude Code session is the orchestrator. Slice
runners are its subagents. Start it with `/loop` and this page (see `SKILL.md`, "start").
The project runs with `"mode": "live"` in `project.json`.

## Who does what

| Role | Who | Does | Never does |
|---|---|---|---|
| Orchestrator | The live session (Opus) | Frames rows, writes or approves plans, starts slice runners, reads each diff, runs Dennis on R2 code, dispatches the project's CI gate, runs the full suite on a trigger, merges, records state, posts to Slack, fixes nodes and the process | Takes an owner decision alone, spends money or creates VMs without the owner |
| Planner | Opus (the orchestrator or an Opus subagent) | Writes the plan: behavior, failure modes, acceptance tests, placement, size | Builds |
| Slice runner | Sonnet subagent, one worktree each | Builds one slice of an approved plan to merge-ready: build, T0, Codex self-review (up to 3 rounds), merge the base branch, near tests on the final head, push | Writes plans, edits the approved plan, merges, pushes the base branch, reviews with Dennis, posts to Slack |
| Dennis | Opus reviewer agent | Second review on R2 code and big or risky plans | Writes fixes |
| Codex | the project's `codex-review` and `codex-fix` commands | The runner's self-review on every slice; the plan fixer after 2 Dennis rounds | Merges |

## Heartbeat and events

Events drive the work. A slice runner's hand-back, a background shell and a Monitor each wake
the session when they finish. The `/loop` tick is a heartbeat, not a poll. End each tick with
a wake-up about 60 minutes out (never under 30). Arm a Monitor for each gate, suite or review
you wait on instead of a shorter wake-up.

## Each tick

1. Dashboard sync: `devloop-tick PROJECT`. In live mode it starts no model session and posts
   nothing. It only refreshes `STATUS.md` and the dashboard.
2. Nodes: check every test node. Give an idle node the queued work. An idle node next to a
   queued job is a bug in the tick.
3. Hand-backs: read each runner's report. Record what it says (see "Recording state").
4. Slices: move each slice one step. Slices in review go first.
5. Fill: start runners and planners up to the capacity in `project.json`, in the project's
   priority order. Check merged PRs and open slices first so no slice starts twice. Check
   the dashboard's overlap list before a runner starts on files another row is changing.
6. Signals: main red, a slice past 2 review rounds, a runner back without tests or docs, a
   node idle beside queued work. Fix the process, not only the slice.
7. Tick log: `devloop-state PROJECT ticklog "one plain line"`.

## How a slice moves

1. FRAME and PLAN: the orchestrator sets the risk (R0 to R2; R3 waits for the owner) and
   the rulebooks. An Opus planner writes the plan. R1 plans take one review. R2 plans take
   Dennis (at most 2 rounds), then `codex-fix` to green.
2. DEVELOP to VERIFY: one slice runner, started with the Agent tool in the background.
   Build its brief from the runner header (`runner-header.md`, copied and filled in for
   the project) plus the task: worktree, branch, plan section, everything the slices table
   lists for the slice, the test depth. The runner hands back merge-ready.
3. SELF-CHECK by the orchestrator: read the diff. Look for placement, a second copy of a
   central mechanism, a missing changelog entry or sentry, size against the plan.
4. REVIEW: R1 takes the runner's Codex GO on the exact head. R2 also takes Dennis on the
   exact head, run by the orchestrator. A docs-only slice (only `.md`, `.rst`, `.txt` or
   `docs/` changed) skips the test step and goes to review.
5. REMEDIATE: send the findings back to a slice runner with the same brief header. The
   runner fixes, runs T0, does a Codex delta review, merges the base branch, runs the near
   tests and pushes, in one session. Never split a fix round into separate build, test and
   review hops. Review ladder: Claude runners fix rounds 1 to 3, Codex fixes rounds 4 to 6,
   then the plan is redesigned. Review rounds never park a slice.
6. GATE: see "Merge standard".
7. DOCUMENT: close-out before the row's next slice opens: shipped log, board, graph refresh
   if the project keeps one, the plain-language Slack note.

## Merge standard

Merge only when all of these hold on the exact PR head:

- review GO on that head (R2: Dennis GO as well as Codex GO);
- GitHub CI green, including the project's gate dispatch when it has one;
- the branch contains the base branch;
- the near test selection green on that head, plus the full suite when a trigger from the
  testing rules applies (shared fixtures or test config, test tooling, schema or
  migration, dependency lock, auth, serialization or a frozen contract, a version pin,
  moved or deleted tests, a selection that cannot be computed). The full suite compares
  against the saved base-branch baseline.

Then post to Slack, run `gh pr ready N` if the PR is a draft, and merge with
`gh pr merge N --squash --match-head-commit HEAD_SHA` as a command by itself. Post to Slack
again after the merge. `devloop-merge` is not needed in live mode.

## Recording state

`devloop-state` records every move, so the dashboard stays true. Record facts after they
happen, one command per change:

| When | Run |
|---|---|
| A slice is framed | `devloop-state PROJECT slice new ROW SLICE --repo REPO --risk R1` |
| A runner starts | `devloop-state PROJECT slice set KEY --next-action "runner: building"` |
| A worktree, branch, head or PR exists | `devloop-state PROJECT slice set KEY --worktree DIR --branch B --head SHA --pr N` |
| The orchestrator's diff read passes | `devloop-state PROJECT selfcheck KEY GO --head SHA` |
| A slice changes phase | `devloop-state PROJECT slice move KEY PHASE --reason "plain reason"` |
| A review returns findings | `slice move KEY REMEDIATE --reason "..."` (each move counts one round) |
| A PR merges | `slice move KEY DOCUMENT --reason "merged PR #N"`, then `slice move KEY DONE --reason "close-out done"` |
| The last slice of a row is DONE | `devloop-state PROJECT row-close ROW --confirm-last` |
| The owner must decide | `devloop-state PROJECT ask KEY --question ... --option ... --recommended ...` |
| The loop itself breaks | `devloop-state PROJECT problem KEY --impact ... --fix ...` |

In live mode a move skips the headless evidence checks. Owner holds still apply: a HELD or
owner-parked slice resumes only after `devloop-state PROJECT answer`.

The merged-today, this-week and total counts in `STATUS.md` and on the dashboard come from
the merge record that only `devloop-merge` writes. A `gh pr merge` plus a slice move does
not add to them. Count merges from GitHub until that changes.

## Stop and ask

Ask the owner only for product decisions, new spend, creating or deleting VMs, and any R3
action. Take the recommended option on a technical question and say so afterwards.
