---
Status: current
updated: 2026-11-26
---

# Headless dev loop: setup reference for a remote worker

This page tells a worker on another machine how the headless dev loop is set up, so it
can run the same loop for its own project. Read it, then follow `skills/dev-loop/SKILL.md`
for the setup steps.

## Which branch

Use `feat/dash-mission-control`. It holds the whole loop: the cron tick, the state
commands, the review and merge rules, evidence carry, the overlap check and the Mission
Control dashboard. `feat/headless-loop` has the loop without the newest dashboard work.
Neither branch is on `main` yet. Both still need a final review before they merge.

## What runs

| Part | What it does |
|---|---|
| `devloop-tick PROJECT` | One tick. Cron starts it, by default every 15 minutes. It reads the state, decides the next step for each slice and starts workers. It starts fresh each time, with no memory of the last tick. |
| `devloop-state PROJECT ...` | The only way to change slice state, record evidence, ask the owner a question or pause the loop. It refuses moves that the rules do not allow. |
| `devloop-dispatch PROJECT KIND KEY --brief FILE` | Starts a worker (builder, reviewer, tester, planner, docs) detached, under `devloop-wrap`. |
| `devloop-merge PROJECT KEY` | Merges a slice when every gate item is on the exact head. |
| `devloop-cron add/remove PROJECT` | Adds or removes the cron line. `--interval 15` sets the minutes. |
| `devloop-dash PROJECT --detach` | Starts the dashboard. It prints the Network URL. |

Install all of them with `bin/devloop-install BIN_DIR` from the checkout. Run the tests
with `pytest tests/devloop`.

## Where things are

- Plugin code: `devloop/`. Dashboard: `devloop/dash/`.
- Tick prompt and playbook (shared by every project): `skills/dev-loop/TICK.md` and
  `skills/dev-loop/PLAYBOOK.md`.
- Project settings: `~/.local/state/devloop/PROJECT/project.json`. Start from
  `skills/dev-loop/project.example.json`.
- Project state: the same folder. It holds the slice files, the run ledger (`runs/`),
  `events.jsonl`, `STATUS.md` and `cron.log`.

## The project layer

The plugin holds only general rules. Each project adds its own layer through
`project.json`:

- `tick_doc` and `playbook_doc`: the project's own tick notes and playbook. They add the
  project's goal, scope, test levels, review roster and merge rules to the shared ones.
  Keep them outside this repo if they hold private detail.
- `questions.path`: the owner questions page. The owner answers there or on the dashboard.
  You can also run `devloop-state PROJECT answer KEY OPTION`.
- `roadmap`: the roadmap file and the section whose rows count.
- `repos`: each repo's path, base branch and GitHub slug.
- `capacity`: how many builders, testers, reviewers and planners can run at once.
- `models`: the model for each role.
- `tests`: the commands for each test level (for example `t1` and `full`).
- `notify`: the command that posts updates (for example to Slack).
- `nodes`: the hosts that run tests.

## Rules the loop follows

- Only the orchestrator (the tick) pushes `main` or merges. Workers may push their own
  branches.
- Only one writer works in a worktree at a time.
- Technical questions take the recommended answer. Product questions wait for the owner.
- The review ladder: Claude fixes rounds 1 to 3, Codex fixes rounds 4 to 6, and after
  that the slice goes back to redesign.
- A test run that hangs or times out counts as an infra problem, not a test failure.
- Waiting for a free tester, builder or reviewer slot is not a hold. The slice stays in
  its phase.
- Evidence carry: if the head changes only because main was merged in cleanly, reviews of
  the slice's own diff carry over. A test GO carries only when main brought in
  documentation only. Any other change needs a new test run.
- The overlap check is advice, never a block. Before the tick starts a builder, it reads
  the snapshot's Overlap section. That section lists open slices in different rows that
  change the same files. The tick decides the order and says why in `next_action`.

## Test nodes

The loop runs tests through the commands named in `tests`. On the original setup these
are local wrapper scripts kept outside this repo. They pick a test machine in this order:
Mac first if the run can use it, then the Linux box, then one cloud VM for spillover. Each
script prints its verdict and ends with an `EXIT n` line. To set up a remote worker, point
`tests` at your own runner. The loop reads only its exit code: 0 is a pass, and a code in
that level's `infra_codes` (for example 124 for a stall) is an infra problem. Any other
code is a test failure.

## Threat model

Local and single user. The guards stop accidents, not attacks.
