---
Status: proposal
updated: 2026-11-25
---

# Headless dev loop (design)

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
autonomous-operation, delegation, reuse-first, proportionality, observability-and-resilience,
testing, workspace-and-vcs.

Risk: **R2**. This is gate and dispatch machinery for unattended runs, with concurrency
concerns. Plan review is Dennis then Codex, and code review is Dennis then Codex.

## Why

The Decoy orchestrator runs as one long `/loop` session. Each tick and each subagent
hand-back replays the whole conversation, and about 58% of its usage goes to re-reading old
history. The owner wants the same dev loop, the same gates and the same capacity, run by a
fresh headless session on a cron. All state lives in files. No conversation holds anything
that matters.

## What the owner sees

- **Start:** in any terminal, "invoke the dev loop skill and start the dev loop cron". The
  skill installs the cron, runs the first tick, and confirms on Slack.
- **Run:** every 30 minutes, and right after any worker finishes, a fresh `claude -p`
  orchestrator reads the state. It moves each row one step forward, fills free slots,
  merges on a double GO, writes the new state and exits.
- **Status:** in any ordinary session, "check the decoy dev loop state". Claude reads the
  state and reports what shipped, what is in flight, progress per row, rows closed, PRs
  merged, what is left on the roadmap, and the open questions with the defaults it applied.
- **Stop:** "stop the dev loop". The skill writes PAUSE and removes the cron line.

## Goals

- Replace the live `/loop` orchestrator completely, with the same capacity (8 builders,
  4 testers, 2 reviewers, 2 to 3 planners) and the same merge authority.
- Keep the dev loop unchanged: FRAME, PLAN, DEVELOP, SELF-CHECK, VERIFY, REVIEW, REMEDIATE,
  GATE, DOCUMENT. Keep the risk tiers, the Dennis-then-Codex review, the T0 to T4 test depth,
  node routing, the V1 bar and the 3-round stop. The phase table in
  `obsidian-vault/10_Projects/decoy/orchestrator-loop.md` stays the authority.
- No row gets lost or stale. Every slice is always in a named phase with a named next action,
  a live worker, or a named blocker.
- Owner questions do not block work. The tick applies the recommended option, records it, and
  tells the owner, who can overrule it later.
- The skill and scripts are generic and shareable on GitHub (in the public `louiskeep/dev-loop`
  plugin). The repo holds no private paths, hostnames or Decoy names. Everything
  project-specific is filled in when the loop is started for a project and stored outside
  the repo. Decoy, run locally on this devbox against its roadmap, is the first project.
- Every review, test and cross-model step the current process has stays. Efficiency changes
  come later, one at a time, once this runs.

## Non-goals

- No change to the dev-rules phases, gates or review tiers.
- No new board. `decoy-rows` (`~/.local/state/decoy-ci/rows.json`) stays the row board and
  keeps its hourly Slack cadence.
- No replacement for node-watch, decoy-capacity, codex-review, codex-fix, vm-run,
  decoy-vm-t3 or slack-notify.sh. The tick and workers call them.
- No database, queue server or memory service in v1 (see Hindsight below).

## Anti-goals (failure even if it runs)

- A slice sits for hours with no worker and no recorded reason.
- Two writers in one worktree, or a merge of a head other than the one reviewed and tested.
- The tick edits state JSON by hand and corrupts it.
- Machinery grows past what is needed. The 2026-11-23 revision-4 plan (21 acceptance tests,
  shadow mode, wake tokens, PID fencing) stalled at build for this reason.

## Approaches considered

1. **Lean rebuild (chosen by the owner, 2026-11-25).** A skill, a small state directory, a
   tick script and a dispatch wrapper. It keeps the safety rules that prevent real damage and
   drops the rest.
2. **Finish revision 4** (`headless-orchestrator-plan-2026-11-23.md`). It passed the Codex
   plan gate and covers every edge case, but the build stalled after one session (no commits,
   no tests). Its build would take a week or more.
3. **Claude scheduled tasks** (cloud, or a session-bound cron). Rejected: cloud agents cannot
   reach the devbox worktrees, the Dell or the Mac, and a session cron needs an open session.

## Design

### Project setup (filled in at start)

The first `start` for a project asks these questions and writes the answers to
`~/.local/state/devloop/<project>/project.json`. Later starts reuse the file and show it for
confirmation. Each answer has a generic default, so a shared user can run the loop with only
`claude`, `git` and `gh`.

| Setting | What it is | Generic default | Decoy answer |
|---|---|---|---|
| `intent` | One paragraph: the goal and what "done" means | asked, no default | CLI and engine V1 built by 2026-12-15, closed with QA by 2027-01-01; progress counted in rows closed and net burn |
| `roadmap` | Source of truth for the task list: file path, the section, and how a row is identified | asked, no default | `docs/ROADMAP.md` section 5 on the CLI repo's main, rows as `decoy-rows roadmap` enumerates them |
| `repos` | Repos and their local clones | the current repo | CLI and engine clones |
| `process_doc` | Project rules the tick reads on top of the generic loop: priority order, phase table owners, duties | none | `obsidian-vault/10_Projects/decoy/orchestrator-loop.md` |
| `rules_dir` | The dev-rules set | the plugin's bundled `rules/` | `/home/cam/dev-rules` |
| `capacity` | Slot caps by class | builder 2, tester 1, reviewer 1, planner 1 | builder 8, tester 4, reviewer 2, planner 3 |
| `models` | Model per role | Opus for tick and planner, Sonnet for builder | same |
| `reviews` | Reviewer commands per risk tier, for plans and for code | R1 plan and code: one independent Claude reviewer; R2: two in sequence | see "Review and test steps" |
| `tests` | Commands for T0/T1, the T2 gate set, the full suite and its triggers | the repo's test command | vm-run, `gates.sh`, decoy-vm-t3 with the saved main baseline |
| `board` | Optional board command fed on phase changes | none (the events journal is the board) | `decoy-rows` |
| `nodes` | Optional node status and capacity commands | none | node-watch, decoy-capacity |
| `merge` | Merge authority and command | mark ready, do not merge | merge after the gate, `gh pr merge --squash --match-head-commit` |
| `notify` | Alert command | none | `slack-notify.sh manual` |
| `status_copy` | Where to copy STATUS.md | none | `obsidian-vault/10_Projects/decoy/devloop-status.md` |

The tick and dispatch read these commands from `project.json`. Decoy's tools stay where they
are (`~/bin`) and are named only in Decoy's `project.json`.

### Review and test steps (unchanged from today)

The state helper enforces these steps mechanically. A slice cannot move to the next phase
until the required evidence is recorded for its current head. A new head clears earlier
review and test evidence. The tick cannot skip a step by editing files, because only
`devloop-state` writes slice files and it checks each transition.

| Phase | Required before the slice moves on | Decoy binding |
|---|---|---|
| PLAN | Plan file on disk with "Rules consulted:". R1: one review GO. R2: Dennis (at most 2 rounds), then Codex to green through codex-fix. Plan authored by Opus, never Sonnet. | Opus planner, `claude -p --agent dennis`, `codex-review` (`review-low` for R1 and re-checks), `codex-fix` |
| DEVELOP | Branch pushed by the one builder in that worktree. T0 per edit, T1 before the push. | Sonnet builder, vm-run |
| SELF-CHECK | Builder lint and recurring-defects check, then the orchestrator reads the diff (placement, duplicate mechanism, CHANGELOG, sentry). The tick records its read. | ruff, mypy on the node; `recurring-defects.md` |
| VERIFY | T1 green on the exact head, mutations on core logic, uncovered parts stated. | vm-run |
| REVIEW | R1: one independent reviewer GO. R2: first reviewer GO, then the cross-model reviewer GO, both on the exact head. Never the author. | R1 Codex; R2 Dennis then Codex (medium for the first R2 pass) |
| REMEDIATE | Root-cause fix with a regression test. Any `src/` change returns to review. A test-only fix under about 50 lines is diff-checked by the orchestrator. Round 3 on one artifact parks the slice for a plan fix or an owner decision. | builder; `codex-fix` for the plan |
| GATE | All required GOs on the exact head, CI green, T1 plus the T2 gate set on that head, the full suite when a testing.md trigger applies, CHANGELOG current, rebased on main. Notify before the merge. | engine full gate dispatch; decoy-vm-t3 against the saved baseline; Slack ping |
| DOCUMENT | The close-out from the process doc, done before the row's next slice opens. | gate-ledger line, `decoy-rows merged`, graph `--update` for engine merges, row-closed Slack note, daily roadmap PR |

### Who records evidence

The tick model cannot record a review GO or a green test by hand. Review verdicts and test
results reach a slice only when a run is applied. The wrapper builds `result.md` for Codex,
Dennis and test runs from the tool's own output. `devloop-state applied RUN` copies the
verdict into the slice only if the run's kind, slice and head match the slice's current head.
The tick records its own evidence directly for one step only, its SELF-CHECK diff read, with
the head it read.

### Review rules come from config

Per-tier review steps (plan and code), the R0 and R1 paths, and owner exceptions are in
`project.json` `reviews`. `devloop-state` enforces whatever is configured. Decoy's config at
cutover follows the standing roster (memory "gate-roster", 2026-11-04, sized by risk
2026-11-10):
- R1 code and low-risk plans: Codex only.
- R2 plans: Dennis (at most 2 rounds), then Codex.
- R2 code: Dennis, then Codex, on the exact head.

The "R2 on Codex only until usage resets" note on `orchestrator-loop.md` (2026-11-11) is
expired. If the owner restores it, it becomes a config change, not a code change.

### Slots per kind

| Kind | Slot |
|---|---|
| planner, codex-fix (plan repair) | planner |
| builder, slice-runner | builder |
| dennis, codex-review | reviewer |
| test (vm-run, decoy-vm-t3, Actions run) | tester, held until the remote result is read |
| docs | none; the Codex docs lane keeps running on its own, outside the loop |

Remote workers do not count. At cutover, the old loop's running workers are entered as
adopted runs (they count against slots and are watched by PID or remote handle) until they
finish.

### Board mapping

`devloop-state` calls the configured `board` command on each phase change, so the model
never has to remember it. Decoy's map onto `decoy-rows` states:

| Loop phase | decoy-rows |
|---|---|
| FRAME, PLAN | planning |
| DEVELOP, SELF-CHECK, VERIFY | building |
| REVIEW | review |
| REMEDIATE | fixing |
| GATE | gate |
| merged | `decoy-rows merged ROW SLICE PR WHAT` |
| parked, held | parked, held |

The `decoy-rows` CLI and rows.json format do not change.

### Permissions and global hooks in headless runs

The tick and every worker run with `--permission-mode auto`, the same mode the live loop uses
(HO-2 default). A worker that gets no output for 20 minutes and hits no tool is treated as
hung: the wrapper stops it and records FAILED, so it does not hold a slot until its deadline.

These hooks in `~/.claude/settings.json` also fire in headless sessions:
- **`rules_gate`:** kept. Planners and builders must still read the rules before writing a
  plan or code.
- **`check-push-gate.py` and `decoy-board hook`:** kept. They are push and merge guards, and
  the tick's merge must pass them.
- **`loop-protocol-reminder.py`:** skipped under `DEVLOOP_HEADLESS=1`, because TICK.md
  already carries the loop protocol and the reminder adds node-watch output to every prompt.
- **`slack-notify.sh` stop and notification:** skipped under `DEVLOOP_HEADLESS=1`.

The plugin's own `gate_guard` and `.loop-state.json` are not installed on this box, and
`devloop-state` does not use them. A shared user who installs the plugin gets both. The
design leaves them separate in v1. Merging the two stores is a later change.

### Memory across ticks

Each tick starts fresh, so the loop keeps three short files that the snapshot always
includes. All three are written through `devloop-state` (atomic, deduplicated by key), so
repeated entries raise a count instead of adding lines.

| File | What goes in it | Written when | Read when |
|---|---|---|---|
| `tick-log.md` | One short entry per model tick: time, what it saw (counts), what it did (dispatches, phase moves, merges, defaults applied), and why for anything unusual. The newest 200 entries are kept, and older ones move to `tick-log-<month>.md`. | The tick's last step, `devloop-state ticklog` | The snapshot includes the last 5 entries. The tick reads further back when a slice's history is unclear. |
| `known-problems.md` | Problems with the loop itself: a stuck dispatch, a tool that keeps timing out, a rule that conflicts with another, a step that keeps wasting a round, a hook that blocks headless work. Each entry has a key, first and last seen, a count, the impact, and a suggested fix. | Whenever the tick or a wrapper hits one, with `devloop-state problem KEY ...` | The snapshot lists open entries. `status` shows them. The owner picks fixes from here for later efficiency changes. |
| `lessons.md` | Recurring problems in the application being built: a review finding class that keeps coming back, a test that flakes, an engine path that breaks a common way, a choice that keeps getting NO-GO. Each entry has a key, area (module or row group), count, what goes wrong, and "do instead". | When a run's result or a review finding matches a known class, or a new class shows up a second time, with `devloop-state lesson KEY ...` | The snapshot lists entries for the areas of slices in flight. Dispatch appends the lessons for a slice's area to every planner, builder and reviewer brief. |

A lesson seen 3 or more times is flagged in STATUS.md as a candidate for the dev-rules
`catalogs/recurring-defects.md`. The owner or a normal docs change promotes it. The loop
does not edit dev-rules itself.

### Dev rules and best practices

The loop runs on the dev-rules set named by `rules_dir` (`/home/cam/dev-rules` for Decoy; the
plugin's bundled `rules/` for shared users).

- **Tick.** TICK.md tells the tick to read `README.md`, `00-universal.md`,
  `risk-and-exceptions.md`, `development-loop.md`, `autonomous-operation.md` and the local
  operations profile. It records the risk level and the routed rulebooks at FRAME in the
  slice file. A slice cannot leave FRAME without both.
- **Briefs.** Every brief from `devloop-dispatch` carries:
  - the slice's risk level and routed rulebooks, with paths, to read first;
  - the `catalogs/recurring-defects.md` check;
  - the commit-message and test-depth standing rules;
  - the writing rules for human-facing text (no em-dashes, simple-english and claudisms for
    docs);
  - the area's lessons;
  - the hand-back contract.

  The brief text lives in one template in the skill, with the project additions in
  `project.json`. It is not rewritten per tick.
- **Enforced, not only asked:**
  - `rules_gate` blocks plans and code written before the rules are read;
  - plans need "Rules consulted:", and plan-lint runs on Decoy plans;
  - the review steps and evidence rules above;
  - Dennis reviews with the clean-architecture and clean-code lenses (standing owner rule);
  - merges only on the exact reviewed and tested head.
- **Lint own edits.** Any direct edit the tick makes (CHANGELOG lines, ledger lines, small
  test-only fixes) runs ruff check, ruff format and mypy before commit, as today.

### State directory

`~/.local/state/devloop/<project>/`, mode 0700. Only the scripts below write to it. The tick
model changes state through their commands, never by editing files.

| Path | Holds |
|---|---|
| `project.json` | Repos, roadmap source, caps, tick interval, models, Slack target, vault status page path. |
| `slices/<ROW>--<SLICE>.json` | One file per in-flight slice: row, slice, phase, risk, worktree, branch, PR, head, review round, review verdicts by head, current run ID, waiting_on, next_action, last_moved_at, history list. |
| `runs/<run-id>/` | `request.json` (kind, row, slice, argv, worktree, deadline), `brief.md`, `result.md` (written by the worker), `exit` (written by the wrapper), `log`, `applied` (written by the tick). |
| `events.jsonl` | Append-only journal: phase moves, dispatches, results, merges, row closes, questions, defaults applied, alerts. Counts and history come from here. |
| `questions.md` | Open questions, each with options, the recommended one, and whether the default was applied. |
| `STATUS.md` | Rendered at the end of every tick. Also copied to the vault (`10_Projects/decoy/devloop-status.md`) for reading on `.116`. |
| `PAUSE`, `cap-until` | Holds. PAUSE is set by the owner or the skill. `cap-until` is set when a usage cap is detected. |
| `tick.lock`, `tick.requested` | Serialization (below). |

All writes are atomic (temp file in the same directory, then rename) under a short per-file
`flock`. Slice and question writes go through one helper, `devloop-state`, with subcommands
`slice set`, `slice new`, `slice close`, `ask`, `answer`, `applied` and `event`. Each checks
the phase name and the allowed transition, then refuses bad input and leaves the file
unchanged.

`decoy-rows` stays the row board. The tick mirrors slice phase changes to it through its own
CLI (`decoy-rows slice`, `merged`, `close`), so the hourly Slack posts keep working.

### Tick (`devloop-tick <project>`)

Cron runs it every 30 minutes. Each worker wrapper also runs it on exit.

1. `flock -n tick.lock`. If the lock is held, touch `tick.requested` and exit. After a tick
   finishes, if `tick.requested` exists, remove it and run again (at most 3 runs back to back,
   then the next cron run covers the rest). No tick is ever lost, and two never overlap.
2. If `PAUSE` exists or `cap-until` is in the future, collect finished runs (step 3) and exit.
3. **Collect.** For each run with an `exit` file and no `applied` marker, read `result.md`.
   For each run with no `exit`, check whether the wrapper PID is alive (PID plus process start
   time from `/proc`, so a reused PID is not mistaken for the worker). A dead run with no
   `exit` is marked FAILED.
4. **Snapshot.** A plain script builds a compact Markdown view, capped at about 15K tokens:
   - stale and failed items first;
   - finished runs not yet applied;
   - every in-flight slice with phase, head, next action and age;
   - main CI for both repos;
   - PR heads and checks;
   - node-watch and decoy-capacity output;
   - free slots by class, and free memory;
   - open questions and owner notes from `OWNER-QUESTIONS.md`;
   - roadmap candidates in priority order (`decoy-rows`, the closing set, critical path);
   - duties due (morning repin, evening scorecard, daily roadmap PR).
5. **Skip check.** If the snapshot (ignoring clocks and ages) matches the last one and the last
   model tick ran less than 2 hours ago, exit without calling Claude.
6. **Model.** `timeout 30m claude -p --model claude-opus-5-5 --permission-mode auto
   --no-session-persistence` with `TICK.md` plus the snapshot on stdin. The model works
   through `TICK.md`:
   - apply each finished run (advance or return the slice);
   - act on every node line;
   - move every slice one phase if its exit condition is met;
   - gate and merge;
   - fill free slots in priority order;
   - answer due duties;
   - apply defaults to new questions.
   It acts only through the existing tools, `devloop-dispatch` and `devloop-state`. It marks
   each applied run with `devloop-state applied RUN`.
7. **After.** Render `STATUS.md` and copy it to the vault. Check the model output for a usage
   cap (timeout exit 124, then the `rate_limit` event in stream JSON, then a text match) and
   set `cap-until` to the stated reset, or one hour if none is stated. On a failed or
   timed-out tick, post one Slack alert per distinct reason. The Stop and Notification Slack
   hooks stay silent under `DEVLOOP_HEADLESS=1`, so every tick does not post.

### Dispatch (`devloop-dispatch <project> KIND ROW SLICE --brief FILE [--worktree DIR] [-- ARGS]`)

- **Kinds:**

  | Kind | Runs | Default deadline |
  |---|---|---|
  | `planner` | Opus `claude -p` | 2h |
  | `builder`, `slice-runner` | Sonnet `claude -p` | 4h |
  | `dennis` | `claude -p --agent dennis` | 90m |
  | `codex-review` | codex-review | 1h |
  | `codex-fix` | codex-fix | 1h |
  | `test` | vm-run or decoy-vm-t3 | 2h, or the T3 stall guard |
  | `docs` | codex-docs | 1h |

- **Admission** under one short lock:
  - slot caps by class: builder 8, tester 4, reviewer 2, planner 3;
  - one writer per worktree and branch;
  - free memory of at least 2.5 GB, the existing floor;
  - refuse while PAUSE or a cap is active.

  A refusal prints the reason, and the tick records it as the slot's idle reason.
- **Launch:** create the run directory, then start the wrapper with `setsid nohup`, with stdin
  from the brief and the tick lock closed in the child. It returns at once with the run ID.
- **Wrapper:** runs the payload under `timeout` and writes `exit` (code and end time). For
  Codex and test kinds, the wrapper builds `result.md` from the tool output: the verdict line
  for this head, or the T3 `EXIT n` line and the failing test IDs. A zero exit with no verdict
  counts as FAILED. Then it runs `devloop-tick <project>` in the background.
- **Hand-back contract** for Claude workers, appended to every brief: write `result.md`
  atomically with status (DONE, BLOCKED or FAILED), a short summary, PR, head SHA, tests run
  and their result, and the next action. The tick checks the status against the exit code and
  the head. A contradiction counts as FAILED.
- **Retry:** a FAILED run gets one automatic retry on the same head. A second failure parks the
  slice with the reason and posts one Slack alert (autonomous-operation: stop after repeated
  failure).

### Stale guard

A slice is stale when it has no live run and its `last_moved_at` is more than 2 hours old.
Stale slices head the snapshot. `TICK.md` requires the tick to act on each one: dispatch the
next step, or park it with a named blocker. A slice parked more than 24 hours goes in the
evening scorecard.

### Merges

GATE and merge follow the orchestrator-loop phase table. The slice file must show both review
GOs and green T1, plus T2 or the full suite where testing.md requires it, all on the current
head. Then comes the Slack ping before the merge, then
`gh pr merge --squash --match-head-commit HEAD`. The tick then checks GitHub's merged state
before it runs `decoy-rows merged`. If GitHub's state is unknown, there is no retry; the slice
stays at GATE with a note. Merge authority is the owner's standing grant (memory
"authority-commits-pushes-merges").

### Questions and defaults

When the loop needs an owner decision, the tick runs `devloop-state ask`, which records the
options and the recommended one. The default is applied at once and work goes on. One Slack
message names the question and the applied default. The owner overrules by editing
`questions.md` or `OWNER-QUESTIONS.md`, and the next tick reads the change.

These questions still block and wait for the owner, because 00-universal and
risk-and-exceptions do not let them be waived: R3 side effects (new spend, VM create or
delete, force operations, deletion of user work) and anything the current loop prompt already
reserves for the owner.

### TICK.md

The tick prompt has two parts:

- **Generic, in the skill** (`skills/dev-loop/TICK.md`, about 100 lines): the loop phases
  and the required steps above, the order of work in a tick, the stale rule, the
  fill-free-slots rule, the merge rule, the hand-back contract, and the limits. The limits
  are: change state only through the commands, and end the tick inside 30 minutes by
  dispatching long work instead of doing it.
- **Project, from `project.json`:** the `intent` and `roadmap` text, then the `process_doc`
  path to read for priority order, duties and owner rules. For Decoy this is the current
  `/loop` prompt and phase table in `orchestrator-loop.md`, unchanged. A short
  `TICK-decoy.md` next to it in the vault keeps the parts a fresh tick needs every time: the
  priority order, the closing set, the plan buffer, the duties, and the commit and test-depth
  rules for briefs. It points at the long page for detail.

### The skill (`dev-loop`)

Source in `~/dev-loop-plugin/skills/dev-loop/`, linked into `~/.claude/skills/dev-loop`.
Scripts live in `~/dev-loop-plugin/bin/` and are installed to `~/bin` by same-directory copy
and rename (memory "edit-live-scripts-atomically"). Modes:

- **start [project]:** on first use, ask the project setup questions and write
  `project.json`. Then check the tools it names, the roadmap path and the state directory.
  Merge the
  cron line into a fresh `crontab -l` and check that the old lines are still there. Remove
  PAUSE, run the first tick in the foreground, and report.
- **stop:** write PAUSE and remove the cron line. Running workers finish and their results
  wait for the next start.
- **status:** read `STATUS.md`, `events.jsonl`, the slice files, `decoy-rows show` and the
  roadmap. Answer in plain language:
  - shipped;
  - in flight, by row;
  - rows closed and net burn;
  - PRs merged (today, this week, all);
  - progress and what is left per row;
  - open questions and the defaults applied;
  - stale or parked items;
  - open known problems and lessons flagged for the recurring-defects catalog;
  - last tick time and the next one.

### Hindsight

Not in v1. The tick needs exact state, and Hindsight's LLM extraction and ranked recall are
the wrong tool for that. It also adds Postgres and a second LLM path. It could come later as
a read-only lessons source, for example recurring review findings by area. In v1, slice
history, `recurring-defects.md` and the vault cover that.

### Cutover

1. Build, review and test (dev loop R2).
2. At a quiet point, stop the live `/loop` from dispatching. Let running subagents finish, or
   record their worktrees and PRs.
3. Backfill one slice file per in-flight slice from `decoy-rows`, open PRs, the newest
   handoff memory and the worktrees. Check the list against the board.
4. "Start the dev loop cron." Watch the first two ticks. Check one completion, one dispatch
   and one gated merge by hand.
5. Rollback: "stop the dev loop", then restart the old `/loop` with STATUS.md as its handoff.

Retire revision 4 (`Status: record`, superseded by this doc). Drop `~/scratch/orch-build`
unless its `decoy-rows note`/`roadmap` work is reused (builder's call).

### Defect classes

| # | Class | Applies here | Guard |
|---|---|---|---|
| 1 | value-free output | Yes: STATUS.md, Slack and the vault copy must not carry data values, secrets or raw tool errors | Render from checked fields and fixed reason codes; planted-secret test |
| 2 | cleanup on every exit path | Yes: wrapper exit, tick timeout, killed worker | `exit` written by the wrapper on all paths; dead run with no `exit` becomes FAILED |
| 3 | state consistency | Yes: slice files, events journal, board mirror | One writer (`devloop-state`), atomic replace under flock, transition checks |
| 4 | route completeness | Yes: every run kind needs a result reader | Dispatch table; an unknown kind is refused |
| 5 | referential integrity | Yes: run to slice, slice to PR and head | Run ID and head checked when a result is applied |
| 6 | untrusted input | Yes: worker `result.md` and model output | Parsed against the contract; a contradiction is FAILED, never DONE |
| 7 | TOCTOU (check then use) | Yes: slot admission, merge head | Admission under one lock; `--match-head-commit` |
| 8 | weak tests | Yes | Each listed test fails when its guard is removed (mutation check at the gate) |
| 9 | refactor drift | N/A: new tools; existing tools are called, not changed | |
| 10 | frozen contracts | Yes: `decoy-rows` CLI and rows.json used by cron and the web board | Called through its CLI only; no format change |

### Tests

About 12 focused tests with fakes for `claude`, `gh`, `codex-review` and `vm-run` on PATH:
- lock collision sets `tick.requested` and the rerun happens;
- admission refuses the ninth builder, the fifth tester and a second writer in a worktree;
- a dead wrapper with no `exit` becomes FAILED, and a reused PID does not;
- `result.md` status contradicting the exit code is FAILED;
- a zero-exit Codex run with no verdict is FAILED;
- a merge is refused when the head differs from the reviewed head;
- cap detection sets `cap-until` and blocks the next model call;
- the skip check skips an unchanged snapshot and runs once the 2h mark passes;
- `devloop-state` rejects a bad phase or transition without writing;
- `STATUS.md` renders the counts from a fixture journal;
- `devloop-state` refuses REVIEW to GATE when the cross-model GO is missing for the current
  head, and a new head clears earlier GOs;
- the repo contains no private path or hostname (a grep test over the shipped files);
- a repeated `problem` or `lesson` key raises the count and adds no line, and `tick-log.md`
  rolls over past 200 entries;
- a brief from dispatch contains the risk level, rulebook paths, recurring-defects check,
  standing rules and the area's lessons;
- `devloop-state` refuses to move a slice out of FRAME without a risk level and rulebooks.

### Owner additions (2026-11-25)

- **Node directory.** Config key `nodes.directory` names one current page per project that
  says, for each node, what it is, where it lives, how to reach and check it, what to use it
  for and not, and which tools target it. The snapshot carries its summary; builder and test
  briefs name it. For Decoy it is a live-checked vault page, and the `dev-nodes` skill points
  to it.
- **Rules parity.** Every standing rule the live loop follows today maps to where the
  headless loop enforces it (code), carries it (TICK or brief) or configures it
  (`project.json`). The table lives in the project's tick doc and is a gate item.
- **Playbook.** A generic `PLAYBOOK.md` plus a project `playbook_doc`, read by the tick on
  every invocation: done-state checklist per phase (items tagged code-checked or
  judgment), a scenario table, the required parts of each worker brief, and the testing
  required per phase and risk. The snapshot prints each slice's checklist with each
  code-checked item met or missing; each brief carries its role's section.
- **Slack on every tick.** Each tick run, including skipped, paused and capped ones, posts
  one plain-language line: what it did, what is running, what is next.

## Calls made in writing (owner to check)

- Hindsight left out of v1.
- Tick every 30 minutes plus a tick on every worker exit, with a skip when nothing changed
  (at most 2 hours between model ticks).
- State in `~/.local/state/devloop/<project>/`, with STATUS.md copied to the vault.
- Evidence only from applied runs. The tick cannot record a GO by hand.
- R2 is Dennis then Codex at cutover, and the "Codex only" note is treated as expired.
- Owner questions take the recommended default, except R3 side effects, which still wait.
- `devloop-state` runs beside the plugin's `.loop-state.json` and does not replace it.

## Left to the builder

- Script language: Python 3 stdlib plus bash, matching `decoy-rows`.
- The exact snapshot layout and the stale and skip thresholds within the stated bounds.
- Whether to reuse the `decoy-rows note` and `roadmap` code in `~/scratch/orch-build`.
- STATUS.md layout.
