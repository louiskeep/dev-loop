---
Status: plan
updated: 2026-11-25
---

# Headless Dev Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
autonomous-operation, delegation, reuse-first, proportionality, observability-and-resilience,
testing, workspace-and-vcs, cross-cutting-concerns, api-and-compatibility, destructive-operations,
profiles/local-operations.

Risk: **R2**. Plan review: Dennis (at most 2 rounds), then Codex to green through codex-fix.
Code review: Dennis, then Codex, on the exact head.

**Goal:** Replace the live `/loop` orchestrator with a cron-driven, fresh-context `claude -p`
tick that runs the same dev loop, the same reviews and tests, the same capacity and the same
merge rules, with all state in files.

**Architecture:** A small Python stdlib package `devloop/` holds every state rule. Thin `bin/`
entry points call it. Workers run detached under a wrapper that records their exit and wakes
the tick. The tick script does the mechanical work (lock, collect, snapshot, skip, render) and
calls the model only to decide; the model changes state only through `devloop-state`,
`devloop-dispatch` and `devloop-merge`, which refuse any move the rules do not allow.

**Tech Stack:** Python 3.11 stdlib, bash, cron, `flock`, `gh`, `claude` CLI. Tests: pytest
with fake `claude`, `gh`, `codex-review`, `vm-run` scripts on PATH.

**Spec:** `docs/specs/2026-11-25-headless-dev-loop-design.md` (approved 2026-11-25), plus the
two owner additions on 2026-11-25 recorded below (node directory, rules parity).

## Review record

- Owner call 2026-11-25 (supersedes the round-3 park below): review rounds never park a
  slice. Rounds 1 to 3 are fixed by the Claude builder, rounds 4 to 6 by Codex
  (`codex-builder`), and the move that would be round 7 goes to PLAN with reason `redesign`.
  Parks stay only for run failures and holds.
- Dennis round 1 (2026-11-25, head 395120c): GO-WITH-FIXES, 12 HIGH, 10 MEDIUM, 6 LOW. All
  HIGH and MEDIUM items are folded into the tasks below; LOW items taken except the Slack
  volume note (owner asked for a line per tick). M10 (a second state store beside
  `.loop-state.json`) is accepted in the spec and logged as a follow-up: merge the two after
  cutover.
- Codex round 1 (2026-11-25, head 4ac534f): NO-GO, 16 findings. codex-fix produced a GO
  version, but it added mechanism today's loop leaves to the orchestrator (test depth
  recomputed from each diff, a digest manifest for the deployment, a signed owner release).
  Owner call 2026-11-25: "the loop will start headless, so the orchestrator can handle the
  merge if double gate green." So the tick model keeps today's judgment calls (test depth,
  full-suite triggers, when to merge), and code enforces only what keeps evidence honest.
  Folded from Codex: merge checks base branch and named required checks; reviews and tests
  freeze their purpose and run on a private checkout of the head; Codex code review cannot
  start before Dennis GO; verdicts come only from the final message; codex-fix credit goes to
  the installed bytes; train members must be ancestors of the tested head; apply is
  replay-safe; lost test runs hold their slot; a cutover import command; an installed
  package path; Slack lines built from events, not model text; one gate validator shared
  by transition and merge; the guard hook reuses `gate_guard.classify`; S3 gets Dennis then
  Codex before cron starts. Codex round 2 reviews this version.
- Codex round 2 (head 41742b4): NO-GO, 12 findings, all on evidence and run contracts, none
  asking to undo the owner call. Folded: reviewer, kind and purpose bound in config and
  checked at admission and apply; `codex-fix` is plan-only; engine merge runs the engine's
  own required-checks verifier and needs the gate dispatch to start after the final Codex
  GO; the tick's self-check record is validated as its own evidence type; planner, review
  and repair inputs and outputs named explicitly; plan reviews follow the predecessor rule
  and plan rounds are keyed by plan path; merge trains take an explicit batch head; wrapper
  and payload liveness are tracked apart and an unconfirmed test keeps its slot; writer
  reservations last until the run is applied; repair install is crash-safe; import can
  carry plan approval and resumes merged PRs in DOCUMENT; the board merge text is fixed
  words only. Round 3 is the last plan round (review-rounds cap); anything left after it
  is fixed in the build and checked at code review.
- Codex round 3 (head 38f7d4a, last plan round): NO-GO, 4 BLOCKS-BUILD and 4 FIX-IN-BUILD.
  The 4 blockers are closed in the plan text: the engine gate status must point at the
  same `workflow_dispatch` run that is checked (Task 6); merge trains are stacked only, so
  the tested head is always a slice head (Task 4); `plan_path` is frozen once a plan run
  exists (Task 2); builder, planner and repair all reserve the canonical worktree root
  (Task 4). The 4 FIX-IN-BUILD items are listed under "Build notes" for the builder and
  are checked at code review.

## Parity gaps closed (2026-11-25, from the Decoy parity sweep)

- **Duties** (morning repin, evening scorecard, daily roadmap PR, the Codex docs batch per
  tick): new run kind `duty` (no slot, no slice evidence, deadline from config). Argv comes
  from `duties[name].argv`; the key is `DUTY-<NAME>--<date>`. The tick dispatches due duties
  per the project tick doc. Owner 11-23 "owner handles Codex" covers Codex as builder; the
  docs batch is the owner-approved docs lane and runs as a duty.
- **Slack explainer per merge:** written by the DOCUMENT docs run, plain language, passed
  through `scrub`, posted by `notify`. This is the one public text that is model-written,
  by design (owner close-out rule); the docs brief forbids SHAs, paths and data values.
- **Codex batching of tiny PRs:** not in v1; one Codex review per slice. Recorded as a
  deviation (more Codex use); revisit after cutover.
- **CHANGELOG check per repo:** `merge.repos[repo].changelog_glob` (Decoy CLI
  `CHANGELOG.md`, engine `changelog.d/*`), with test `test_merge_changelog_glob_per_repo`.
- **Capacity:** owner reconfirmed 8 builders and 4 reviewers on 11-23 and 8 builders and 4
  testers on 11-22. Decoy caps: builder 8, tester 4, reviewer 4, planner 3.

## Build notes (Codex round 3 FIX-IN-BUILD)

1. Config has one command per allowed (reviewer, kind) pair; both Codex commands get
   `CODEX_LAST_MSG`; the brief appends the verdict line its reader expects (`VERDICT:` for
   reviews, `SELF-VERDICT:` for repair); a run whose final-message file is missing is
   FAILED. Test both helpers end to end.
2. A test run whose launch succeeded keeps its tester slot until termination is confirmed
   (exit 0 or 1 from the payload, or the tick's `release` after a node check), including
   255 disconnects. Store host and job label in the run. Elapsed time only triggers a
   reconcile note in the snapshot, never a release.
3. `exit_checklist(project, key, to_phase)` evaluates a named transition. DEVELOP to
   SELF_CHECK also needs an applied builder run with status DONE whose head equals the
   slice head (an imported PR needs one builder pass or `--plan-approved` import plus a
   tick selfcheck). REVIEW shows GATE items as blocking while REMEDIATE stays open. Task 8
   stdin includes `PLAYBOOK.md` and the project's `playbook_doc`; test with distinct
   markers in both and in a worker's role section.
4. Before `gh pr merge`, persist `merge_attempt{repo, pr, base, head}` on the slice. Every
   merge call first reconciles a stored attempt (state, base and head) and finishes the
   DOCUMENT move once. Test a crash right after a successful merge call.

## Owner additions (2026-11-25, after the spec)

1. **Node directory.** One current page that explains each node: what it is, where it lives,
   how to reach it, how to check it, what to use it for and not, which tools target it. The
   tick snapshot carries its summary, and every builder and tester brief names it. The
   existing `dev-nodes` skill is out of date (no Dell, retired GCP VMs listed); it becomes a
   pointer to the directory, so there is one source. Generic config key: `nodes.directory`.
2. **Rules parity.** "Rebuild the same process we have now, headless, so all our rules
   apply." Every standing rule the live loop follows today is listed in a parity table (Task
   10) with where the headless loop enforces it (code), carries it (TICK or brief text) or
   configures it (`project.json`). A rule with no row is a gate failure for this plan.
3. **Slack on every tick.** Every tick run posts one plain-language Slack line, including
   skipped and paused ticks, so the owner can follow the loop without opening anything.
   A model tick posts what it did (applied, moved, dispatched, merged, questions defaulted)
   and what is next. A skipped tick posts "no change since HH:MM CST, N running". The tick's
   own post replaces the global Stop hook post, which headless runs skip.
4. **Playbook (owner, 2026-11-25).** The orchestrator gets a dev loop playbook it reads on
   every tick: a done-state checklist per phase, a scenario table (what happened, what to
   do), the key parts of each worker brief, and the testing required per phase and risk.
   Code backs it: the snapshot prints each slice's checklist for its current phase with
   each item marked met or missing, from the same functions `move` uses. Workers get the
   playbook section for their role in their brief. See Task 9 and Task 10.
5. **Target:** first live tick today (2026-11-25). Slices 1 and 2 are the minimum to run;
   Slice 3 is the cutover.

## Global Constraints

- Python 3.11 stdlib only in `devloop/` and `bin/`. No new dependencies.
- No private path, hostname, person or "decoy" string in `devloop/`, `bin/devloop-*`,
  `skills/dev-loop/`. Decoy values live only in `~/.local/state/devloop/decoy/project.json`
  and the vault.
- State dir `~/.local/state/devloop/<project>/`, mode 0700; overridable by `DEVLOOP_HOME`
  (tests use `tmp_path`).
- Every state write: temp file in the same directory, `os.replace`, under `fcntl.flock` on
  `<file>.lock`.
- Phases: `FRAME, PLAN, DEVELOP, SELF_CHECK, VERIFY, REVIEW, REMEDIATE, GATE, DOCUMENT, DONE,
  PARKED, HELD`.
- Deadlines: planner 2h, builder 4h, dennis 90m, codex-review 1h, codex-fix
  1h, test 2h, docs 1h. Idle kill: 20 minutes with no log growth.
- Caps from `project.json`; Decoy builder 8, tester 4, reviewer 4, planner 3. Memory floor
  2.5 GB `MemAvailable`.
- Tick: cron every 30 minutes, plus one on every wrapper exit. At most 3 back-to-back runs per
  lock holder. Skip the model when the stable snapshot hash is unchanged and the last model
  tick is under 2 hours old. Model call `timeout 30m`.
- Stale: no live run and `last_moved_at` older than 2 hours. Round 3 on one artifact parks.
  A FAILED run gets one retry; the second failure parks and alerts.
- Merge: `gh pr merge <PR> --squash --match-head-commit <head>`, then confirm `state ==
  MERGED` and `headRefOid == head` before any board or state update. Unknown state: no retry.
- R3 side effects (spend, VM create or delete, force operations, deleting user work) always
  wait for the owner. Merge to main is under the owner's standing grant.
- Env `DEVLOOP_HEADLESS=1` on the tick and every worker.
- Slack: one line per tick run (model, skipped, paused or capped), plain language, times in
  CST, no SHAs, paths or tokens; plus the existing alerts (park, failure, merge, R3 wait).
- Commit messages: Conventional Commits subject, 72 chars max, no body unless it explains why,
  final line `cam`. Test depth: T0 per edit, T1 per commit. No em-dashes anywhere.

## Review Focus

1. A worker that writes no `result.md` and exits 0: must be FAILED, never DONE (Task 4 test
   `test_zero_exit_without_result_is_failed`).
2. `project.json` hand-edited with a typo in a reviewer name or tier: `devloop-state` must
   refuse at load with the key path, not crash mid-tick (Task 1 test
   `test_bad_project_config_is_refused`).
3. Clock or PID reuse after a devbox reboot: a run whose wrapper PID now belongs to another
   process must read as dead (Task 4 test `test_reused_pid_reads_dead`).
4. The model tick prints a secret or token into `next_action`: STATUS.md, the vault copy and
   Slack must not carry it (Task 7 test `test_status_scrubs_planted_secret`).
5. Crontab with other jobs present when `start` runs twice: no duplicate line, no lost line
   (Task 8 test `test_cron_add_is_idempotent_and_keeps_others`).

## File map

| File | Responsibility |
|---|---|
| `devloop/store.py` | Paths, atomic JSON and text write under flock, event append |
| `devloop/project.py` | Load and validate `project.json` |
| `devloop/slices.py` | Phase set, transition table, evidence checks, board mirror |
| `devloop/notes.py` | tick log, known problems, lessons, questions |
| `devloop/runs.py` | Run dirs, admission, launch, liveness, result parsing, apply |
| `devloop/wrap.py` | Worker wrapper: timeout, idle kill, exit file, result build, wake tick |
| `devloop/merge.py` | Gate check and merge on exact head |
| `devloop/snapshot.py` | Snapshot build, stable hash, size cap |
| `devloop/status.py` | STATUS.md render and scrub |
| `devloop/tick.py` | Lock and requeue, pause and cap, model call, cap detection |
| `devloop/cron.py` | Crontab add and remove |
| `bin/devloop-state`, `-dispatch`, `-wrap`, `-merge`, `-tick`, `-cron`, `-install` | Thin CLIs over the package |
| `skills/dev-loop/SKILL.md`, `TICK.md`, `brief-template.md`, `project.example.json` | Skill, generic tick prompt, brief template, config example |
| `tests/devloop/` | Tests and fakes (`tests/devloop/fakebin/`) |

## Reuse inventory

- `~/bin/decoy-rows` (`slice`, `merged`, `close`, `show`): board mirror, called through its
  CLI only (`~/bin/decoy-rows:220`, `:232`, `:264`).
- `~/bin/codex-review` and `~/bin/codex-fix`: review and plan repair payloads, unchanged
  (`~/bin/codex-review:11` runs `codex exec`; verdict words GO, GO-WITH-FIXES, NO-GO come from
  `~/.codex/v1-bar-preamble.md:5`).
- `~/bin/vm-run`, `~/bin/decoy-vm-t3`: test payloads; T3 prints `EXIT n`
  (`~/bin/decoy-vm-t3:194`).
- `~/.claude/scripts/slack-notify.sh manual`: the `notify` command.
- node-watch, decoy-capacity: snapshot commands.
- `claude --agent dennis`: the Dennis payload, with its clean-architecture and clean-code
  lenses.
- `hooks/rules_gate.py`: unchanged; fires in headless sessions.
- `~/scratch/orch-build/bin/decoy-rows` (`note`, `roadmap`): not reused. `decoy-rows` stays
  frozen and the roadmap is read by the project's snapshot command. The staging dir is
  deleted after cutover (owner OK needed: it is user work).
- Not reused: `hooks/loop_state.py` `.loop-state.json`. Kept separate per the spec.

## Frozen contracts

- `decoy-rows` CLI arguments and `rows.json` shape (cron hourly post and the web board read
  them).
- `codex-review <workdir> <prompt> <out> [label]` and `vm-run`, `decoy-vm-t3` argument order.
- `slack-notify.sh manual "<msg>"`.
- The phase table in `orchestrator-loop.md` stays the authority; this plan does not edit it
  except to add a pointer to the headless loop at cutover.

## Enumerations

### Exit paths

Every way a run or tick can end, and what records it. Today's state writer has no atomic
replace (`hooks/loop_state.py:151` writes in place); the new store does not copy that.
- Payload exits 0 or non-zero: wrapper writes `exit` with code (`devloop/wrap.py`).
- Payload passes its deadline: wrapper kills the process group, `exit` with reason
  `deadline`.
- Payload idle 20 minutes: wrapper kills, reason `hung`.
- Wrapper itself killed (reboot, OOM): no `exit`; collect marks FAILED by liveness
  (`devloop/runs.py`).
- Tick model call times out (124) or hits a usage cap: `cap-until` or one alert per reason.
- Tick crashes in Python: cron log keeps the traceback; the lock is released by fd close;
  the next cron run proceeds.
- Merge command errors and `gh` cannot report the PR state: slice stays GATE with
  `merge_unknown`, no merge retry until a state check reads OPEN (`devloop/merge.py`).
- Worker or Codex hits a usage cap: `exit` reason `capped`, `cap-until` set, no failure count.

### Routes

Every run kind and its result reader (one table in `devloop/runs.py`, `KINDS`):
- `planner`, `builder`, `docs`: Claude worker writes `result.md`.
- `dennis`: wrapper reads the anchored `VERDICT:` line from the final stream-json result
  event; the read-only reviewer never writes files.
- `codex-review`: wrapper reads the anchored `VERDICT:` line from the out file.
- `codex-fix`: wrapper reads the anchored `SELF-VERDICT:` line and the plan file hash.
- `test`: wrapper maps the payload exit code (vm-run returns the command's code, 124 stall,
  255 transport). The Decoy full suite runs through a poll script that waits for the
  remote `^EXIT n` line (`~/bin/decoy-vm-t3` prints it on the node log; 75 means no slots)
  and exits with it, failing when BRANCH-ONLY failures exist.
- An unknown kind is refused at dispatch.
- The kinds and deadlines come from the spec (`docs/specs/2026-11-25-headless-dev-loop-design.md:151`
  slots per kind, `docs/specs/2026-11-25-headless-dev-loop-design.md:314` dispatch).

### FK shapes

N/A: no database or foreign keys; references are run ID to slice key and slice to PR and
head, covered under fields.

### Fields

Slice file keys: `row, slice, phase, risk, rulebooks, worktree, branch, repo, pr, head,
plan_path, purpose, round, plan_rounds{reviewer}, evidence{head_or_plan_sha: {kind: {verdict,
run, at}}}, full_suite_required, run, waiting_on, next_action, last_moved_at, failures{kind},
merge_unknown, park_reason, history[]`.
- Run `request.json`: `id, kind, row, slice, head, plan_sha, reviewer, level, select, covers,
  out, argv (built by dispatch), worktree, slot, deadline_s, idle_s, pid, pid_start, boot_id,
  payload_pgid, payload_start, started_at, retry_of`.
- `result.md` header keys: `status, head, pr, verdict, tests, next`.
- `exit`: `code, reason, ended_at`.

### Output surfaces

- `STATUS.md` and its vault copy.
- Slack via `notify`: one tick line per run, plus alerts.
- `events.jsonl`, `tick-log.md`, `known-problems.md`, `lessons.md`, `questions.md`.
- Board mirror (`decoy-rows`), phase map from
  `docs/specs/2026-11-25-headless-dev-loop-design.md:165`.
- Worker briefs (`runs/<id>/brief.md`).

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | public text (Slack, STATUS, vault copy) rendered from event kinds, counts and IDs; `scrub()` as a second layer | `test_public_outputs_use_event_fields`, `test_status_scrubs_planted_secret` | append raw `next_action` to the tick line |
| 2 | cleanup on every exit path | wrapper `finally` writes `exit`; collect marks dead no-exit runs FAILED | `test_wrapper_writes_exit_on_kill`, `test_dead_run_without_exit_is_failed` | drop the `finally` block in `wrap.run` |
| 3 | state consistency | single writer `devloop-state`, atomic replace under flock, `TRANSITIONS` table, run effects and `processed_run_ids` in one replace | `test_bad_transition_leaves_file_unchanged`, `test_concurrent_set_no_lost_update`, `test_apply_replay_counts_once` | write the processed ID in a second replace |
| 4 | route completeness | `KINDS` table maps every kind to slot, deadline and reader | `test_every_kind_has_reader_and_slot`, `test_unknown_kind_refused` | delete the `test` entry from `KINDS` |
| 5 | referential integrity | frozen request purpose and head; one `validate_gate` with roster order via `predecessor_run` | `test_apply_refuses_head_mismatch`, `test_late_plan_review_grants_no_code_go`, `test_codex_code_review_refused_before_dennis_go` | take the evidence key from the slice phase at apply time |
| 6 | untrusted input | `parse_result` contract; verdict only from the final message's last line, full-matched, longest option first, exit 0 | `test_zero_exit_without_result_is_failed`, `test_go_with_fixes_is_not_go`, `test_transcript_go_cannot_supply_verdict` | scan the whole transcript for the last verdict line |
| 7 | TOCTOU (check then use) | admission under `admit.lock`; merge with `--match-head-commit` then state check | `test_admission_refuses_ninth_builder`, `test_merge_refuses_head_mismatch` | take the admission count outside the lock |
| 8 | weak tests | each test above paired with its mutation, run at the gate with a mutation script | `tests/devloop/mutations.sh` reports every listed mutation killed | revert any one assertion |
| 9 | refactor drift | N/A: no existing code is refactored; existing tools are called unchanged through their CLIs | n/a | n/a |
| 10 | frozen contracts | `decoy-rows` called only through `board.cmd` argv; no import, no file read | `test_board_mirror_calls_cli_only` | write `rows.json` directly |

## Acceptance tests

All in `tests/devloop/`, red first, with fakes in `tests/devloop/fakebin/` put first on PATH:
- `test_lock_collision_sets_requested_and_reruns`
- `test_admission_refuses_ninth_builder`, `test_admission_refuses_fifth_tester`,
  `test_admission_refuses_second_writer_same_worktree`, `test_admission_refuses_low_memory`
- `test_dead_run_without_exit_is_failed`, `test_reused_pid_reads_dead`
- `test_status_contradicts_exit_is_failed`, `test_zero_exit_without_result_is_failed`
- `test_codex_zero_exit_without_verdict_is_failed`
- `test_merge_refuses_head_mismatch`, `test_merge_unknown_state_no_retry`
- `test_cap_detection_sets_cap_until_and_blocks_model`
- `test_skip_unchanged_snapshot_until_two_hours`
- `test_bad_transition_leaves_file_unchanged`
- `test_status_renders_counts_from_fixture_journal`
- `test_gate_refused_without_cross_model_go_on_head`, `test_new_head_clears_evidence`
- `test_no_private_strings_in_shipped_files`
- `test_repeat_problem_key_increments_count`, `test_ticklog_rolls_over_past_200`
- `test_brief_contains_rules_lessons_and_contract`
- `test_every_tick_posts_one_slack_line`, `test_tick_slack_line_is_scrubbed`
- `test_frame_exit_requires_risk_and_rulebooks`
- the five Review Focus tests above.

## Slices

| Slice | Tasks | Ships | Gate |
|---|---|---|---|
| S1 state core | 1, 2, 3 | `devloop-state` with phases, evidence, notes | Dennis then Codex on the S1 head |
| S2 run and tick | 4, 5, 6, 7, 8, 9 | dispatch, wrapper, merge, tick, status, cron, skill | Dennis then Codex on the S2 head; T1 = `pytest tests/devloop` |
| S3 Decoy wiring and cutover | 10, 11 | project.json, TICK-decoy.md, node directory, parity table, hook guards, backfill, first live tick | Dennis then Codex on the S3 files before cron starts; owner watches first two ticks |

S1 and S2 can ship as one PR to the plugin repo if review rounds stay short; S3 is local
config and vault docs, committed to the vault.

## Pre-mortem

- **The model ignores TICK.md and does nothing useful.** Mitigation: the snapshot lists the
  exact next command per slice; the stale guard surfaces drift within 2 hours; first two
  ticks are watched.
- **Headless permission prompt hangs a worker.** Mitigation: `--permission-mode auto` plus
  the 20-minute idle kill and one retry; the cause goes in `known-problems.md`.
- **Cron env lacks PATH, `gh` auth or Codex auth.** Mitigation: `devloop-cron add` writes the
  install-time PATH and HOME into the line; `start` runs one tick from a cron-like env (`env
  -i HOME=... PATH=...`) before installing.
- **Board drift** if `decoy-rows` fails. Mitigation: mirror failure becomes a known problem and
  an event, never a failed transition; the hourly post shows the old state until fixed.
- **Usage cap mid-tick leaves half-applied work.** Mitigation: every command is atomic per
  slice; `applied` markers mean the next tick resumes where it stopped.
- **Rules lost in translation from the long `/loop` prompt.** Mitigation: Task 10 parity table
  is a gate item; Dennis checks it against `orchestrator-loop.md` lines 216 to 264 and the
  standing memory rules.

---

## Task 1: Store and project config

**Files:**
- Create: `devloop/__init__.py`, `devloop/store.py`, `devloop/project.py`
- Test: `tests/devloop/test_store.py`, `tests/devloop/conftest.py`

**Interfaces:**
- Produces: `state_dir(project: str) -> Path` (honours `DEVLOOP_HOME`);
  `write_json(path: Path, obj: dict) -> None`; `read_json(path: Path, default=None) -> dict`;
  `locked(path: Path)` context manager (flock on `path.with_suffix(path.suffix + ".lock")`);
  `append_event(project: str, kind: str, **fields) -> None` (one JSON line with `at`);
  `load_project(project: str) -> dict` raising `ConfigError(msg)` that names the bad key path.
- `project.json` required keys: `name, intent, roadmap{path, section, row_id}, repos[{name,
  path}], rules_dir, capacity{builder, tester, reviewer, planner}, models{tick, planner,
  builder, reviewer}, reviews{R0,R1,R2: {plan: [reviewer], code: [reviewer]}},
  reviewers{name: {kind}}`. Optional: `process_doc, tick_doc, tests{t1, t2, full},
  board{cmd, phase_map}, nodes{directory, snapshot_cmds[]}, merge{allowed: bool},
  notify{cmd[]}, status_copy, snapshot_cmds[{name, argv, max_chars}], min_free_gb (2.5),
  brief_extra`.

- [ ] **Step 1: Write failing tests**

```python
def test_write_json_is_atomic(tmp_path, monkeypatch):
    p = tmp_path / "a.json"; store.write_json(p, {"x": 1})
    assert store.read_json(p) == {"x": 1}
    assert not list(tmp_path.glob("*.tmp*"))

def test_bad_project_config_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    cfg = minimal_project(); cfg["reviews"]["R2"]["code"] = ["dennnis"]
    write_project(tmp_path, "p", cfg)
    with pytest.raises(project.ConfigError, match=r"reviews\.R2\.code"):
        project.load_project("p")
```

`minimal_project()` and `write_project()` live in `tests/devloop/conftest.py`.

- [ ] **Step 2: Run, expect FAIL** `pytest tests/devloop/test_store.py -v`
- [ ] **Step 3: Implement** as in Interfaces. `state_dir` creates the dir with mode 0700.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(devloop): add state store and project config`

## Task 2: Slices, transitions and evidence

**Files:**
- Create: `devloop/slices.py`, `bin/devloop-state`
- Test: `tests/devloop/test_slices.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `PHASES: tuple[str, ...]`; `TRANSITIONS: dict[str, set[str]]`;
  `new_slice(project, row, slice_id, *, repo, risk=None) -> dict`;
  `set_fields(project, key, **fields) -> dict` (only `worktree, branch, pr, head, plan_path,
  purpose, next_action, waiting_on, risk, rulebooks, full_suite_required`; `plan_path`
  only until the first plan run is dispatched (after that a new plan means moving back to
  FRAME, which starts a new lineage with zero rounds); `risk` only in
  FRAME, so a tier change means moving back to FRAME and earning the new roster's reviews;
  `full_suite_required` can only go False to True; a changed `head` increments nothing and
  leaves old evidence keyed by the old head); `plan_sha(slice) -> str` = sha256 of the
  `plan_path` file, computed when needed, never set by hand; `move(project, key, to_phase,
  reason) -> dict` raising `TransitionError(reason)`; `record_evidence(project, key, kind,
  verdict, run_id, *, head: str|None=None, plan_sha: str|None=None) -> None` (exactly one of
  `head`, `plan_sha`; internal, called by `runs.apply_run`; the CLI exposes only `selfcheck`
  and `docs`); `slice_key(row, slice_id) -> str` = `f"{row}--{slice_id}"`. `new_slice`
  refuses while another slice of the same row is not DONE (one open slice per row; PARKED
  and HELD still hold the row). `import_slice(project, row, slice_id, *, repo, risk,
  rulebooks, worktree, branch, pr, head, plan_path, purpose, phase) -> dict` for cutover
  only: refused when `DEVLOOP_HEADLESS=1`; same row rule. Options: `--plan-approved`
  stores the plan file's current sha as `approved_plan_sha` (those plans passed their gates
  under `/loop`; the import runs in the owner's interactive session, never headless).
  Without it the slice lands in PLAN with its PR and worktree kept. An open PR at GATE or
  later lands in REVIEW, so its gates run again here. A PR that `gh` shows MERGED into the
  configured base at the recorded head lands in DOCUMENT. `row_close(project, row) -> None` calls
  `board.cmd + ["close", row]` and appends a `row-closed` event; the tick calls it when the
  row's last roadmap slice is DONE.
- Transition table:

| From | To |
|---|---|
| FRAME | PLAN |
| PLAN | DEVELOP |
| DEVELOP | SELF_CHECK |
| SELF_CHECK | VERIFY, REMEDIATE |
| VERIFY | REVIEW, REMEDIATE |
| REVIEW | GATE, REMEDIATE |
| REMEDIATE | SELF_CHECK, PLAN |
| GATE | DOCUMENT (only via `devloop-merge`), SELF_CHECK (new head after a rebase, no round), REMEDIATE |
| FRAME | HELD (R3, with a `--wait` question) |
| DOCUMENT | DONE |
| any active | PARKED, HELD |
| PARKED, HELD | the phase recorded at park time |

- Exit conditions checked by `move` (all on the current `head`, plan evidence on `plan_sha`):
  - FRAME to PLAN: `risk` in R0..R2 and `rulebooks` non-empty. R3 goes to HELD with a
    `--wait` question; only `devloop-state answer`, which is refused when
    `DEVLOOP_HEADLESS=1`, releases it, and the slice then runs under the R2 roster.
  - PLAN to DEVELOP: `plan_path` exists and contains `Rules consulted:`, and the plan review
    list `reviews[risk].plan` is met. Each entry is a reviewer name, optionally with a round
    cap (`{"name": "dennis", "cap": 2}`). A capped reviewer is met by a GO on any earlier
    sha of the same `plan_path`, or by `plan_rounds[plan_path][name] >= cap` (the standing flow: at
    most 2 Dennis rounds, then Codex fixes). The LAST reviewer must have GO on the current
    `plan_sha`, from a run whose `predecessor_run` is the capped reviewer's satisfying run
    (its GO, or its round that reached the cap); dispatch refuses the last reviewer before
    that run is applied. For Codex, a `codex-review` GO or a `codex-fix` GO both count; a
    `codex-fix` GO is credited to the repaired file's sha only once that file is installed
    (Task 4). On PLAN exit the slice
    stores `approved_plan_sha`; `validate_gate` refuses if the plan file no longer has it.
  - DEVELOP to SELF_CHECK: `branch`, `head` set.
  - SELF_CHECK to VERIFY: `selfcheck` GO on head. This is the tick's own record (evidence
    `source: tick`, written by `devloop-state selfcheck KEY GO --head SHA`, refused unless
    SHA equals the slice head); it has no run dir and is validated as its own type.
  - VERIFY to REVIEW: `t1` GO on head.
  - REVIEW to GATE: `validate_gate(project, key) -> list[str]` returns no problems. It is
    the one evidence check, used here and by `devloop-merge`: on the current head,
    `selfcheck` GO, `t1` GO, `t2` GO when configured, `full` GO when `full_suite_required`,
    every reviewer in `reviews[risk].code` GO in roster order (each run's
    `predecessor_run` is the previous reviewer's GO run), each review and test evidence
    entry re-read from its run dir (the tick's `selfcheck` record is checked by its own
    head field), and `approved_plan_sha` still matching. `exit_checklist(project, key) ->
    list[tuple[str, bool]]` returns the named items for the current phase's exit, met or
    missing, built from the same checks; `move` and `validate_gate` call it, and the
    snapshot prints it.
  - REVIEW to REMEDIATE only: `round += 1`; at round 3 the move goes to PARKED with
    reason `round-3` instead. SELF_CHECK, VERIFY and GATE moves to REMEDIATE do not count.
    Plan review rounds are counted in `plan_rounds[plan_path][reviewer]`, separate from
    `round`; a new plan path starts at zero.
  - DOCUMENT to DONE: `docs` GO on head.
- Every successful move: sets `last_moved_at`, appends to `history`, appends an event, calls
  the board mirror (`board.cmd + [ "slice", row, slice, phase_map[phase] ]`); a mirror failure
  calls `notes.problem("board-mirror-failed", ...)` and does not fail the move.
- CLI: `devloop-state <project> slice new|set|move|show|list ...`, `selfcheck KEY GO|NO-GO
  --head SHA`, `docs KEY GO --head SHA`, `event KIND k=v...`. `--head` must equal the slice
  head or the command is refused.

- [ ] **Step 1: Failing tests**

```python
def test_bad_transition_leaves_file_unchanged(proj):
    k = mk_slice(proj, phase="FRAME"); before = raw(proj, k)
    with pytest.raises(slices.TransitionError): slices.move(proj, k, "GATE", "x")
    assert raw(proj, k) == before

def test_frame_exit_requires_risk_and_rulebooks(proj):
    k = mk_slice(proj, phase="FRAME", risk="R2", rulebooks=[])
    with pytest.raises(slices.TransitionError, match="rulebooks"): slices.move(proj, k, "PLAN", "x")

def test_gate_refused_without_cross_model_go_on_head(proj):
    k = mk_slice(proj, phase="REVIEW", risk="R2", head="a1")
    slices.record_evidence(proj, k, "code:dennis", "GO", "r1", head="a1")
    with pytest.raises(slices.TransitionError, match="codex"): slices.move(proj, k, "GATE", "x")

def test_new_head_clears_evidence(proj):
    k = mk_slice(proj, phase="REVIEW", risk="R1", head="a1")
    slices.record_evidence(proj, k, "code:codex", "GO", "r1", head="a1")
    slices.set_fields(proj, k, head="b2")
    with pytest.raises(slices.TransitionError): slices.move(proj, k, "GATE", "x")

def test_round_three_parks(proj): ...  # two REVIEW->REMEDIATE ok, third lands PARKED, reason round-3
def test_rebase_at_gate_costs_no_round(proj): ...  # GATE->SELF_CHECK leaves round unchanged
def test_risk_change_outside_frame_refused(proj): ...
def test_r3_held_until_owner_answer(proj): ...  # answer refused with DEVLOOP_HEADLESS=1
def test_import_refused_headless_and_capped_at_review(proj): ...
def test_gate_refused_when_approved_plan_changed(proj): ...
def test_r2_plan_passes_after_two_dennis_rounds_and_codex_go(proj): ...  # dennis NO-GO x2, codex GO on new sha
def test_r2_plan_refused_with_codex_go_on_old_sha(proj): ...
def test_new_slice_refused_while_row_open(proj): ...  # also while PARKED

def test_board_mirror_calls_cli_only(proj, fakebin): ...  # fake board records argv; no rows.json touched

def test_concurrent_set_no_lost_update(proj): ...  # two processes, synced start, set next_action and waiting_on; both survive; repeat 50 times
def test_exit_checklist_matches_move(proj): ...  # every missing item named by the checklist is the reason move refuses
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** per the table. Evidence order uses `at` timestamps.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(devloop): enforce phase transitions and review evidence`

## Task 3: Notes: tick log, known problems, lessons, questions

**Files:**
- Create: `devloop/notes.py`; extend `bin/devloop-state`
- Test: `tests/devloop/test_notes.py`

**Interfaces:**
- Produces: `ticklog(project, text: str) -> None` (newest 200 entries in `tick-log.md`, older
  moved to `tick-log-YYYY-MM.md` by entry month); `problem(project, key, *, impact, fix,
  detail="") -> None`; `lesson(project, key, *, area, wrong, instead) -> None`;
  `lessons_for(project, area) -> list[dict]`; `ask(project, key, *, question, options:
  list[str], recommended: str, wait: bool) -> dict` (`wait=False`: `applied = recommended`
  at once, event, notify; `wait=True` for R3, high-risk or product-level questions: `applied
  = None`, the slice waits); `answer(project, key, choice) -> None`. One store: questions
  are written to `questions.path` (default `<state>/questions.md`), inside a managed block
  between `<!-- devloop:questions -->` markers so a hand-kept owner page can hold it; owner
  edits to a question's `Answer:` line inside the block are read back as `answer`.
  Problems and lessons are stored in `problems.json` and `lessons.json` (source of truth),
  rendered to `known-problems.md` and `lessons.md` after each write. Same key: `count += 1`,
  `last_seen` updated, no new entry. Lessons with `count >= 3` get `catalog_candidate: true`.
- CLI: `devloop-state <p> ticklog -`, `problem KEY --impact --fix`, `lesson KEY --area
  --wrong --instead`, `ask KEY --question --option ... --recommended X [--wait]`, `answer KEY X`,
  `problem-close KEY`.

- [ ] **Step 1: Failing tests** `test_repeat_problem_key_increments_count` (two calls, one
  entry, count 2), `test_ticklog_rolls_over_past_200` (205 entries, 200 in main file, 5 in the
  month file), `test_lesson_flagged_at_three`, `test_ask_applies_default`,
  `test_ask_wait_blocks_slice`, `test_questions_block_keeps_text_outside_markers`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(devloop): add tick log, known problems, lessons, questions`

## Task 4: Runs: dispatch, admission, liveness, results

**Files:**
- Create: `devloop/runs.py`, `bin/devloop-dispatch`
- Test: `tests/devloop/test_runs.py`, `tests/devloop/fakebin/{claude,codex-review,vm-run,gh}`

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces:
  - `KINDS: dict[str, Kind]` where `Kind(slot: str|None, deadline_s: int, idle_s: int,
    reader: str, writes_worktree: bool, pinned: bool)`: planner(planner, 7200, 1200, claude,
    True, False), builder(builder, 14400, 1200, claude, True, False), dennis(reviewer, 5400,
    1200, claude-verdict, False, True), codex-review(reviewer, 3600, 3600, verdict, False,
    True), codex-fix(planner, 3600, 3600, selfverdict, True, False), test(tester, per level
    from `tests[level].deadline_s`, 1200, testexit, False, True), docs(None, 3600, 1200,
    claude, False, False), duty(None, from `duties[name].deadline_s`, 1200, exit, False,
    False). `codex-fix` is plan-only: refused unless the slice is in PLAN.
    `reviewers[name].kinds` binds each reviewer to its allowed kinds (Decoy: dennis
    `[dennis]`, codex `[codex-review, codex-fix]`); dispatch refuses any other pairing, and
    `apply_run` checks the same binding again. No `slice-runner` kind: a runner that calls Codex itself makes
    evidence nobody can check and spends Codex twice.
  - The argv is built by dispatch from config, never passed by the caller:
    `models[role]` for Claude kinds (planner and docs use `models.planner`, which must be an
    Opus model: guides and plans never run on Sonnet); `reviewers[name].argv` for review
    kinds; `tests[level].argv` for tests, plus `--select` items appended for `t1`. Templates
    may use `{brief} {worktree} {out} {head} {run_dir} {plan} {last_msg}`. Inputs and
    outputs are explicit: a planner writes `plan_path` (set before dispatch, in its reserved
    worktree); a plan review reads `{plan}`, the frozen copy in the run dir; a repair runs
    `codex-fix <run_id> <brief> {plan}` with env `CODEX_LAST_MSG={last_msg}`, and its
    repaired output is `~/.cache/codex-fix/<run_id>/<plan basename>` (named in
    `reviewers.codex.repair_out`); its brief asks for a final `SELF-VERDICT:` line. Code
    reviews read the private checkout.
  - `dispatch(project, kind, key, *, brief: Path, worktree: Path|None, reviewer: str|None,
    level: str|None, select: list[str]=(), covers: list[str]=(), retry_of: str|None=None)
    -> str` returns run ID or raises `Refused(reason)`. The request is frozen at admission
    and never edited: `purpose` is `author` for a planner, `plan` for plan reviews and
    repairs, else `code`; plan reviews and repairs store `plan_sha` and a copy of the plan
    in the run dir; code runs store `head` and
    `predecessor_run`. Admission under `admit.lock`: PAUSE or `cap-until` future; slot
    count of live or slot-holding runs `>= capacity[slot]`; a live writer reserving the
    canonical worktree root (`git rev-parse --show-toplevel` of the target, symlinks
    resolved) for every writer: builder, planner and codex-fix (whose target is the
    worktree holding the plan); `MemAvailable <
    min_free_gb`; a review whose roster predecessor has no applied satisfying run (code:
    GO on this head; plan: GO or the round cap on this plan path), so Codex never starts
    before Dennis is done. A writer's reservation (builder, planner, codex-fix) lasts until
    its run is applied, not only while its process lives. Pinned kinds (code reviews and tests) run in a
    private detached checkout made with `git worktree add --detach <run_dir>/tree <head>`
    and removed after exit, so a builder's uncommitted files can never reach a test or
    review. Every payload gets an explicit `cwd` (the reserved worktree, the private
    checkout, or the run dir). Merge trains are stacked only (the standing "stacked PRs, one
    T3" flow): the tick stacks the members so the top slice's head contains them, and
    dispatches the `full` test on that top slice with `covers` (slice keys). The tested
    head is always the dispatching slice's head. Dispatch refuses a member from another
    repo or whose head is not an ancestor of that head (`git merge-base --is-ancestor`),
    and freezes each `(slice, head)` pair. Sibling batches that no slice head contains
    are not supported in this version. Launch:
    `subprocess.Popen([devloop-wrap, project, run_id], start_new_session=True,
    stdin=DEVNULL, stdout=<run_dir>/wrap.log, stderr=STDOUT, close_fds=True)` (no inherited
    pipes, so the dispatching shell returns at once); records `pid`, `pid_start` (field 22 of
    `/proc/<pid>/stat`) and `boot_id` (`/proc/sys/kernel/random/boot_id`). The wrapper adds
    `payload_pgid` and `payload_start` when it starts the payload. Sets slice `run`.
  - `wrapper_alive(req) -> bool` and `payload_alive(req) -> bool`, each by pid or pgid plus
    start time and the same `boot_id`.
  - `collect(project) -> list[str]`: for a run with no `exit` whose wrapper is dead, check
    `exit` again, kill the payload group if it lives (no unsupervised writer), then create
    `exit` with reason `lost` using `O_CREAT|O_EXCL` (a wrapper that wrote first wins);
    returns finished, unapplied run IDs. A `test` run that ended without a remote result
    (`lost`, `hung` or `deadline`) keeps its tester slot (the remote job may still run:
    vm-run kill leaves the remote tree) until `devloop-state release RUN`, which the tick
    calls after checking the node with the node directory's command, or until twice the
    deadline has passed.
  - `parse_result(run_dir: Path, kind: str) -> Result(status, head, pr, verdict, tests, next,
    summary)`: `result.md` header lines `key: value` until a `---` line. Missing file, missing
    status, `status: DONE` with non-zero exit, or `status: FAILED` with exit 0 and no reason
    -> status FAILED.
  - `apply_run(project, run_id) -> None`: all effects and the run ID go into
    `processed_run_ids` in ONE locked replace of the slice file; the `applied` marker is
    written after, and a replay of a processed ID only writes the marker. Refuses if the
    run's slice key differs, or for `code` runs its `head` differs from the slice head, or
    for `plan` runs its `plan_sha` differs from the plan file now (event `apply-mismatch`,
    outcome `stale`). Evidence key comes from the frozen request only: `<purpose>:<reviewer>`
    for reviews, `t1`/`t2`/`full` for tests. A `full` test with `covers` records `full` on
    each member whose head still equals its frozen head; each member update is its own
    locked replace with the run ID in that member's `processed_run_ids`, and `applied` is
    written only after every member is done, so a replay finishes the rest. A `codex-fix`
    GO: first write `repair.json` with `input_sha` and `output_sha` in the run dir; then if
    the live plan's sha equals `input_sha`, install with `os.replace`; credit GO to
    `output_sha`. On replay: live sha equals `output_sha` -> finish the credit; equals
    `input_sha` -> install, then credit; anything else -> `repair-conflict`, install and
    credit nothing. Failure counts are keyed by head (or plan sha), so a
    new head starts with a fresh retry. Plan reviews add one to
    `plan_rounds[reviewer]`. Exit reasons `capped` and `infra` (test exit codes in
    `tests[level].infra_codes`, Decoy 124, 255, 75) count no failure and no round; the slice
    is re-dispatched after `cap-until`. Other FAILED: `failures[kind] += 1`, at 2 move to
    PARKED with reason and notify. Writes `applied`.
  - Brief build: `build_brief(project, key, kind, body: str) -> str` from
    `skills/dev-loop/brief-template.md` with: risk, rulebook paths under `rules_dir`,
    `catalogs/recurring-defects.md` check, standing rules from `project.brief_extra`,
    `nodes.directory` for builder and test kinds, `lessons_for(area)`, the hand-back contract.
    For review kinds it appends: "End with one line: `VERDICT: GO`, `VERDICT:
    GO-WITH-FIXES` or `VERDICT: NO-GO`." Only `GO` counts as GO.
- CLI: `devloop-dispatch <project> KIND KEY --brief FILE [--worktree DIR] [--reviewer NAME]
  [--level t1|t2|full] [--select ID ...] [--covers KEY ...]
  [--retry-of RUN]`; no argv
  passthrough; prints run ID or `REFUSED: <reason>` exit 3. `devloop-state <p> applied RUN`.

- [ ] **Step 1: Failing tests:** the admission four, `test_dead_run_without_exit_is_failed`,
  `test_reused_pid_reads_dead` (request with live pid but wrong start time),
  `test_status_contradicts_exit_is_failed`, `test_zero_exit_without_result_is_failed`,
  `test_apply_refuses_head_mismatch`, `test_every_kind_has_reader_and_slot`,
  `test_unknown_kind_refused`, `test_failed_twice_parks`,
  `test_brief_contains_rules_lessons_and_contract`, `test_argv_built_from_config_only`,
  `test_planner_refuses_non_opus_model`, `test_review_refused_on_dirty_worktree`,
  `test_plan_review_stale_after_plan_edit`, `test_infra_exit_counts_no_failure`,
  `test_train_member_must_be_ancestor`, `test_lost_wrapper_kills_payload_group`,
  `test_collect_does_not_overwrite_wrapper_exit`, `test_late_plan_review_grants_no_code_go`,
  `test_codex_code_review_refused_before_dennis_go`, `test_pinned_run_ignores_uncommitted_files`,
  `test_dispatch_from_unrelated_cwd`, `test_apply_replay_counts_once`,
  `test_repair_installed_only_if_plan_unchanged`, `test_lost_test_holds_slot`,
  `test_dispatch_closes_capture_pipes`, `test_repair_refused_outside_plan`,
  `test_reviewer_kind_binding_enforced`, `test_simultaneous_plan_reviews_refused`,
  `test_plan_rounds_reset_on_new_plan_path`, `test_stacked_train_credits_members_and_crash_after_first`,
  `test_repair_replay_after_install`, `test_writer_reserved_until_applied`,
  `test_planner_creates_absent_plan`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.** Memory read from `/proc/meminfo`, overridable by
  `DEVLOOP_FAKE_MEMAVAIL_KB` for tests only.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(devloop): add dispatch, admission and run results`

## Task 5: Wrapper

**Files:**
- Create: `devloop/wrap.py`, `bin/devloop-wrap`
- Test: `tests/devloop/test_wrap.py`

**Interfaces:**
- Produces: `run(project, run_id) -> int`. Reads `request.json`; runs `argv` with stdin from
  `brief.md`, stdout and stderr to `log`, env `DEVLOOP_HEADLESS=1 DEVLOOP_PROJECT DEVLOOP_RUN
  DEVLOOP_RUN_DIR`; own process group (records `payload_pgid`, `payload_start`). Polls every
  15 s: deadline -> kill group, reason `deadline`; neither `log` nor `request.out` grew for
  the kind's `idle_s` (`DEVLOOP_IDLE_SECS` overrides in tests) -> kill, reason `hung`. Cap
  check on the log and out file with the tick's `detect_cap`: a cap kills nothing but, at
  exit, sets `cap-until` and reason `capped`. In `finally`, build `result.md` when the
  reader is not `claude`:
  - `verdict` and `claude-verdict`: take the reviewer's FINAL message only (Codex: the
    file written by `codex exec -o`, which `codex-review` and `codex-fix` add when env
    `CODEX_LAST_MSG` names a file; Claude: the `result` field of the last
    `{"type":"result"}` stream-json event). The process must exit 0, and the last
    non-blank line, stripped, must `re.fullmatch(r"VERDICT:\s*(GO-WITH-FIXES|NO-GO|GO)")`;
    anything else -> FAILED. A GO quoted earlier in a transcript can never count, and a
    `GO-WITH-FIXES` line can never read as `GO`.
  - `selfverdict`: same rule on a final `SELF-VERDICT:` line.
  - `testexit`: the payload's exit code. 0 -> GO; a code in `tests[level].infra_codes` ->
    FAILED with reason `infra`; else NO-GO with failing IDs from lines matching `^FAILED `
    or the block after `FAILING TEST IDS`.
  Then write `exit` JSON (create-exclusive), then spawn `devloop-tick <project>` detached.
  Claude kinds use argv `claude -p --model <model> --permission-mode auto --output-format
  stream-json --verbose`. No `--no-session-persistence`: the rules gate hook reads the
  session transcript to see which rulebooks were read, and without a transcript every
  Write or Edit would be blocked.

- [ ] **Step 1: Failing tests:** `test_wrapper_writes_exit_on_kill` (payload `sleep 60`,
  deadline 1 s), `test_idle_payload_killed_as_hung` (`DEVLOOP_IDLE_SECS=1`),
  `test_codex_zero_exit_without_verdict_is_failed`, `test_go_with_fixes_is_not_go`,
  `test_dennis_verdict_read_from_stream_json_result`, `test_transcript_go_cannot_supply_verdict`, `test_codex_out_growth_is_not_idle`,
  `test_worker_cap_sets_cap_until_not_failure`, `test_test_exit_codes_mapped`,
  `test_wrapper_wakes_tick` (fake `devloop-tick` touches a file).
- [ ] **Step 2 to 4:** run red, implement, run green.
- [ ] **Step 5: Commit** `feat(devloop): add worker wrapper with deadline and idle kill`

## Task 6: Merge on exact head

**Files:**
- Create: `devloop/merge.py`, `bin/devloop-merge`
- Test: `tests/devloop/test_merge.py`

**Interfaces:**
- Produces: `MergeResult(outcome: Literal["merged","refused","unknown"], reason: str)` and
  `merge(project, key) -> MergeResult`. Steps:
  1. Slice phase GATE; `merge.allowed` true.
  2. `slices.validate_gate(project, key)` returns no problems (the same check as REVIEW to
     GATE).
  3. `gh pr view <pr> -R <repo> --json
     baseRefName,headRefOid,statusCheckRollup,mergeStateStatus,files`: `baseRefName` equals
     `merge.repos[repo].base` (a stacked PR into a parent branch is refused); head equals
     slice head; every name in `merge.repos[repo].required_checks` is present with
     SUCCESS (missing, pending, skipped or failed refuses); no other check is failed or
     pending; when `merge.repos[repo].verify_cmd` is set (Decoy engine:
     `scripts/dev/required_checks.py verify --tier merge --sha {head}`, run from a fresh
     `origin/main` checkout so a newly required job is never missed) it must exit 0; when
     `merge.repos[repo].dispatch_workflow` is set (both are required for Decoy engine), the
     status named in `merge.repos[repo].gate_status` must point (its target URL run ID) at
     a run of that workflow with event `workflow_dispatch`, on this head, created after the
     final Codex GO, conclusion SUCCESS, every job SUCCESS; an older dispatch or a push run
     does not count; the verifier runs with the engine venv python and `GH_TOKEN` from the
     notify env file; `mergeStateStatus` must be
     CLEAN (or HAS_HOOKS) when `merge.require_up_to_date` (UNKNOWN refuses); a file matching `merge.changelog_glob` is in
     `files` when set. The later steps use this same repo and PR number.
  4. Notify "merging ROW SLICE PR". Run `gh pr merge <pr> -R <repo> --squash
     --match-head-commit <head>`.
  5. Whatever its exit code, `gh pr view --json state,headRefOid`: MERGED with the head ->
     evidence `merged`, move to DOCUMENT, board `merged ROW SLICE PR "<ROW> <SLICE>"`
     (fixed words, no model text: the board helper posts this argument to Slack), event,
     `merged`. OPEN with the same head -> nothing merged,
     `refused` with the gh error (scrubbed), flag stays clear. gh itself errors -> set
     `merge_unknown`, problem `merge-state-unknown`, `unknown`.
  6. With `merge_unknown` set, a later call runs step 5's check only; OPEN clears the flag.
- A PreToolUse hook `hooks/headless_merge_guard.py` denies a Bash `gh pr merge` when
  `DEVLOOP_HEADLESS=1`, so the tick and workers can merge only through `devloop-merge` (its
  own subprocess call is not a tool call and is not seen by the hook). It parses with the
  existing `gate_guard.classify` (`hooks/gate_guard.py:127`), which already handles `gh`
  global options such as `-R`.

- [ ] **Step 1: Failing tests:** `test_merge_refuses_head_mismatch` (PR head from fake gh
  differs), `test_merge_refuses_missing_full_suite`, `test_merge_unknown_state_no_retry` (second
  call does not invoke `gh pr merge`, asserted from the fake's argv log),
  `test_merge_success_moves_to_document`, `test_merge_refuses_red_or_pending_check`,
  `test_merge_refuses_evidence_without_run_dir`, `test_merge_open_after_error_clears_flag`,
  `test_merge_guard_denies_gh_merge_when_headless` (also `gh -R o/r pr merge 5`),
  `test_merge_refuses_wrong_base`, `test_merge_refuses_missing_required_check`,
  `test_merge_refuses_gate_dispatch_before_codex_go`, `test_merge_runs_verify_cmd`,
  `test_merge_refuses_unknown_merge_state`, `test_merge_with_public_selfcheck_succeeds`.
- [ ] **Step 2 to 4:** red, implement, green.
- [ ] **Step 5: Commit** `feat(devloop): merge only on the exact gated head`

## Task 7: Snapshot and STATUS

**Files:**
- Create: `devloop/snapshot.py`, `devloop/status.py`
- Test: `tests/devloop/test_snapshot_status.py`

**Interfaces:**
- Produces: `build_snapshot(project) -> tuple[str, str]` (markdown, stable hash). Sections in
  order: stale and failed; finished unapplied runs with parsed results; in-flight slices
  (phase, risk, head, PR, round, next action, age, the exact next command expected); free
  slots by class and memory; open questions and R3 waits; open known problems; last 5
  tick-log entries; lessons for in-flight areas; each `snapshot_cmds` output truncated to its
  `max_chars`; node directory summary (first 60 lines of `nodes.directory`). Hard cap 60,000
  chars, trimming command outputs last-listed first. Hash covers slice phases, heads, runs'
  exit and applied state, questions, problems and command outputs with digits stripped.
  `render_status(project) -> str` and `write_status(project) -> None` (writes `STATUS.md`,
  copies to `status_copy`). Counts from `events.jsonl`: merges today, this week, total; rows
  closed; slices per phase; per-row progress. `scrub(text) -> str` replaces matches of
  `(ghp_|gho_|github_pat_|sk-|xox[abp]-|AKIA)[A-Za-z0-9_\-]{8,}` and any 40+ char base64-ish
  run with `[redacted]`, replaces hex runs `\b[0-9a-f]{7,40}\b` and paths (`~/` or `/`
  followed by two or more segments) with `[ref]`, and cuts free text to 160 chars. All free
  text goes through `scrub`. Times use `zoneinfo("America/Chicago")` and its own CST or CDT
  label.

- [ ] **Step 1: Failing tests:** `test_status_renders_counts_from_fixture_journal`,
  `test_status_scrubs_planted_secret` (next_action holds `ghp_` token; STATUS.md and vault
  copy lack it), `test_scrub_removes_sha_and_path`, `test_snapshot_capped`,
  `test_hash_ignores_ages_and_digits`.
- [ ] **Step 2 to 4:** red, implement, green.
- [ ] **Step 5: Commit** `feat(devloop): add tick snapshot and status page`

## Task 8: Tick and cron

**Files:**
- Create: `devloop/tick.py`, `devloop/cron.py`, `bin/devloop-tick`, `bin/devloop-cron`
- Test: `tests/devloop/test_tick.py`, `tests/devloop/test_cron.py`

**Interfaces:**
- Produces: `tick(project) -> int`: `flock -n tick.lock` (fcntl, non-blocking); busy ->
  touch `tick.requested`, return 0. Loop up to 3 times: remove `tick.requested`; `collect`;
  if PAUSE or cap -> write status, break; `build_snapshot`; skip when hash equals
  `last_hash` and `last_model_at` under 7200 s; else run model: argv `[timeout, 1800, claude,
  -p, --model, models.tick, --permission-mode, auto,
  --output-format, stream-json, --verbose]`, stdin = generic `TICK.md` + project `tick_doc`
  file text + `intent` + `roadmap` + snapshot; env `DEVLOOP_HEADLESS=1`. After: `write_status`;
  `detect_cap(rc, output) -> datetime|None` (124 is a timeout, not a cap; a stream event with
  `"type":"rate_limit"` or a result line matching `(usage|rate) limit` sets reset from a
  parsed time or now+1h); timeout or cap -> one notify per distinct reason per 6 h (stored in
  `alerts.json`). Every pass ends with `post_tick_line(project, outcome) -> None`: one
  `notify` call, text built by `status.tick_line(project, outcome, summary)` where `outcome` is
  `model`, `skipped`, `paused`, `capped` or `failed` and `summary` is rendered from this
  pass's events only (fixed words per event kind plus row and slice IDs, for example
  "merged CLI-12 s2; started builder on ENG-4 s1; Dennis review done on CLI-9 s1"). Model
  free text never reaches Slack, STATUS or the vault copy; the model's `TICKLINE:` goes to
  the private tick log only. The post runs in
  a `finally`, so a crashed or timed-out pass still posts `failed`. `notify` runs
  `notify.cmd` with variables from `notify.env_file` (mode 0600 in the state dir, holds
  the webhook; never in the crontab line) added to its env. Format: `Dev loop HH:MM CST: <summary>.
  Running: B builders, T tests, R reviews. Next: <one item>.` Passed through `scrub`, cut to
  300 chars. A notify failure is logged to `cron.log` and never fails the tick.
  Continue loop only if `tick.requested` exists.
  - `cron.add(project, bin_dir, interval_min=30) -> None`: reads `crontab -l` (a non-zero
    exit counts as empty only when stderr says `no crontab for`; any other error raises),
    saves it to `<state>/crontab.bak`, removes any
    line tagged `# devloop:<project>`, appends `*/30 * * * * PATH=<install PATH>
    HOME=<HOME> DEVLOOP_HEADLESS=1 <bin_dir>/devloop-tick <project> >> <state>/cron.log 2>&1
    # devloop:<project>`, writes with `crontab -`, re-reads and checks the result equals
    the intended text exactly, else restores the saved copy and raises.
    `cron.remove(project)` same pattern (intended = old minus the tagged line).
    Crontab binary overridable by `DEVLOOP_CRONTAB` for tests.

- [ ] **Step 1: Failing tests:** `test_every_tick_posts_one_slack_line` (one notify argv per
  pass for each of model, skipped, paused), `test_tick_slack_line_is_scrubbed`,
  `test_lock_collision_sets_requested_and_reruns`,
  `test_skip_unchanged_snapshot_until_two_hours`,
  `test_cap_detection_sets_cap_until_and_blocks_model`, `test_pause_collects_only`,
  `test_cron_add_is_idempotent_and_keeps_others`, `test_cron_remove_keeps_others`,
  `test_cron_read_error_is_not_empty`, `test_failed_tick_still_posts`, `test_public_outputs_use_event_fields` (a short password
  and `https://user:pw@host` planted in next_action, purpose, TICKLINE and an error reach
  neither Slack, either STATUS copy, nor the board helper's argv),
  `test_notify_gets_env_file_vars`.
- [ ] **Step 2 to 4:** red, implement, green.
- [ ] **Step 5: Commit** `feat(devloop): add cron tick with lock, skip and cap detection`

## Task 9: Skill, TICK.md, brief template, install, shareability

**Files:**
- Create: `skills/dev-loop/PLAYBOOK.md`, `skills/dev-loop/SKILL.md`, `skills/dev-loop/TICK.md`,
  `skills/dev-loop/brief-template.md`, `skills/dev-loop/project.example.json`,
  `bin/devloop-install`; Modify: `README.md` (one section), `pyproject.toml` if needed
- Test: `tests/devloop/test_shipped.py`

**Interfaces:**
- `SKILL.md` modes (description names "dev loop", "start the dev loop cron", "check the dev
  loop state", "stop the dev loop"):
  - **start [project]:** if no `project.json`, ask the setup questions from the spec table
    one at a time, write it, show it. Run `devloop-state <p> check` inside the cron-like env
    (`env -i HOME PATH` from install): validates config (including a phase map entry for
    every phase), tools on PATH, roadmap path, rules_dir, each `snapshot_cmds` entry, a
    `notify` dry run with the env file, `gh auth status`, and `ssh -o BatchMode=yes` to each
    host in `nodes.hosts`. Clear PAUSE. Run one tick in the same env. `devloop-cron add`.
    Notify.
  - **stop:** touch PAUSE, `devloop-cron remove`.
  - **status:** read STATUS.md, events, slices, problems, lessons, questions, and the board
    command's `show` if configured; answer the eight items in the spec, plain language.
  - **answer:** `devloop-state <p> answer KEY CHOICE` for owner overrules.
- `TICK.md` (about 100 lines): read the rules (list from spec "Dev rules"), then in order:
  apply finished runs (`devloop-state applied`), handle stale and failed, act on node lines,
  move each slice whose exit condition is met, record SELF_CHECK diff reads with the head,
  gate and `devloop-merge`, fill free slots in project priority order, due duties, defaults to
  new questions, record problems and lessons, end with `devloop-state ticklog` and a final
  `TICKLINE: <one plain sentence>` line for the private tick log. Judgment the live loop
  makes today stays with the tick: test depth and full-suite triggers (`set
  full_suite_required` per testing.md triggers), when a slice is ready to gate, and when
  to call `devloop-merge` (double gate green). Limits: state
  only through the commands; dispatch long work, never do it in the tick; never take an R3
  action; finish within 30 minutes.
- `PLAYBOOK.md` (generic, read by the tick every invocation; the project's
  `playbook_doc` is appended after it and may add or tighten, never loosen):
  - **Done state per phase:** one checklist per phase, each item tagged `[code]` (checked by
    `exit_checklist`, shown met or missing in the snapshot) or `[judgment]` (the tick
    decides and writes the reason in `next_action`). Example DEVELOP: builder hand-back
    DONE `[code]`, commits on branch with head recorded `[code]`, tests written before code
    `[judgment]`, lint and format clean `[judgment]`, CHANGELOG entry when the repo keeps
    one `[judgment]`. GATE: the `validate_gate` items `[code]`, full suite needed per
    testing triggers `[judgment]`, double gate green on this head `[code]`.
  - **Scenarios:** a table of situation, what to do, command. At least: worker FAILED once
    or twice, hung, capped, infra exit; Dennis or Codex NO-GO, GO-WITH-FIXES, round cap;
    plan round cap; red main; PR behind main or conflicting; merge refused (each reason);
    merge unknown; stale slice; idle node with queued work; memory low; R3 question; owner
    overrule; repair conflict; flaky test suspected; import mismatch.
  - **Worker briefs:** the required parts per role (builder, planner, Dennis, Codex review,
    Codex repair, tester, docs): goal and row, risk and rulebooks, plan path and the
    acceptance tests it defines, files in scope, test depth to run, where to run tests,
    recurring-defects check, standing rules, lessons, what not to do, hand-back contract.
  - **Testing:** what each phase must show per risk tier (T0 per edit, T1 at VERIFY, T2 at
    GATE, full suite only on triggers), with `{tests}` pointing at the project's test
    policy.
  - **Close-out:** the DOCUMENT checklist.
  `build_brief` inserts the role's brief section from the playbook; the tick's stdin
  carries the whole playbook every invocation (it is the per-tick "hook").
- `brief-template.md` placeholders: `{risk} {rulebooks} {recurring_defects} {standing_rules}
  {node_directory} {lessons} {body} {contract}`.
- `devloop-install <bin_dir>`: copies the `devloop/` package to
  `~/.local/share/devloop/lib/devloop.new` then swaps it in with `os.replace` on the
  directory link; copies each `bin/devloop-*` to `<bin_dir>/.name.tmp` then `os.replace`.
  Each launcher puts `~/.local/share/devloop/lib` first on `sys.path` (override
  `DEVLOOP_LIB`), so cron needs no repo checkout on the path; links `~/.claude/skills/dev-loop` to the repo skill dir (replaces only a link,
  never a real dir).

- [ ] **Step 1: Failing tests:** `test_no_private_strings_in_shipped_files` (patterns
  `/home/`, `decoy`, `192\.168\.`, `\.116\b`, `\.55\b`, `obsidian`, `cam\b` over `devloop/`,
  `bin/devloop-*`, `skills/dev-loop/`), `test_example_project_validates`,
  `test_install_replaces_atomically`, `test_playbook_covers_every_phase_and_kind` (a
  done-state section per PHASE and a brief section per KINDS role), `test_tick_stdin_has_playbook`,
  `test_brief_has_role_section`, `test_installed_start_stop_start` (from an unrelated
  cwd under `env -i` with install HOME and PATH and a fake crontab holding another job:
  install, start, stop, start; the launcher imports the package; the other job survives;
  PAUSE clears; exactly one tagged line).
- [ ] **Step 2 to 4:** red, write files, green.
- [ ] **Step 5: Commit** `feat(devloop): add dev-loop skill, tick prompt and installer`
- [ ] **Step 6: Headless smoke test** (real `claude`, not a fake): dispatch a `builder` on a
  throwaway repo worktree whose brief says read the three rulebooks the rules gate
  requires (`hooks/rules_gate.py:60`), then write and commit `x.py`; the run must end DONE
  with the commit present. Set the slice head to that commit, then a `dennis` code review
  on it must yield a parsed verdict.
- [ ] **Step 7: Mutation check:** `tests/devloop/mutations.sh` applies each mutation in the
  defect table with `sed` on a temp copy, runs its test, expects FAIL, restores. All killed.

## Task 10: Decoy wiring: project.json, TICK-decoy.md, node directory, parity table

Local and vault files only; nothing here goes in the plugin repo.

**Files:**
- Create: `~/.local/state/devloop/decoy/project.json` (values from the spec's Decoy column;
  `reviews` R1 `{plan:[codex], code:[codex]}`, R2 `{plan:[dennis,codex], code:[dennis,codex]}`,
  R2 plan is `[{name: dennis, cap: 2}, codex]`; R0 `{plan:[], code:[codex]}`; `models.planner`
  Opus; `notify.cmd` slack-notify manual with `notify.env_file` holding
  `SLACK_WEBHOOK_URL` (copied from the settings env block, mode 0600); `board` decoy-rows
  with a phase map entry for every phase; `tests` t1 (vm-run with selection), t2 (the
  gate set per testing.md, vm-run), full (`~/bin/decoy-t3-wait`), `infra_codes` 124, 255,
  75; `merge.repos` with base `main` and the required check names read from each repo's
  branch protection and CI (engine includes the gate-selfhosted verdict); `merge.require_up_to_date` and
  `merge.changelog_glob` `CHANGELOG.md`; `questions.path` the vault `OWNER-QUESTIONS.md`;
  `brief_extra` linking `~/obsidian-vault/10_Projects/decoy/handoffs/LOCAL-BUILDER-BRIEF.md`
  with its key rules inline; `nodes.directory` the vault node directory and `nodes.hosts`;
  `snapshot_cmds`: top resident processes over 4 GB,
  node-watch status, decoy-capacity, `decoy-rows show`, `gh run list` on main for CLI and
  engine, `gh pr list` for both repos, the roadmap section, `OWNER-QUESTIONS.md` open items)
- Create: `~/obsidian-vault/10_Projects/decoy/TICK-decoy.md`
- Create: `~/obsidian-vault/10_Projects/decoy/NODE-DIRECTORY.md`
- Modify: `~/.claude/skills/dev-nodes/SKILL.md` (becomes a short pointer to the directory)
- Modify: `~/.claude/scripts/loop-protocol-reminder.py` (exit early when
  `DEVLOOP_HEADLESS=1`) and `~/.claude/scripts/slack-notify.sh` (early exit ONLY for the
  `stop` and `notification` modes; `manual` must still post); edit a copy then `mv`.
- Create: `~/bin/decoy-t3-wait` (runs `decoy-vm-t3`, polls the node log over ssh every 60 s
  printing a heartbeat line, exits with the remote `EXIT n`, prints the `FAILING TEST IDS`
  block, exits 1 when BRANCH-ONLY is non-empty).
- Modify: `~/bin/codex-review` and `~/bin/codex-fix`: when env `CODEX_LAST_MSG` is set,
  add `-o "$CODEX_LAST_MSG"` to `codex exec`; without it they behave exactly as today (edit
  a copy then `mv`).
- Create: `~/obsidian-vault/10_Projects/decoy/PLAYBOOK-decoy.md` (`playbook_doc`): the Decoy
  done-state items on top of the generic ones (close-out every merge: Slack explainer,
  graph update, CHANGELOG before merge, `decoy-rows close`; engine gate dispatch after
  Codex GO; T3 baseline rewrite after a CLI main merge; coverage and mutation on changed
  units), the Decoy scenarios (Dell mirror empty objects, gate venv lacks companion, vm-run
  kill leaves remote, stacked PR base strands work, red main first), the builder brief
  parts from `LOCAL-BUILDER-BRIEF.md`, and the Decoy test policy (where each tier runs,
  per the node directory).
- Modify: `~/.claude/settings.json` PreToolUse Bash: add the plugin's
  `headless_merge_guard.py` (back up first; owner told in the cutover ping).

**Node directory content:** one section per node, live-checked today (ssh or `gcloud
compute instances list` with the decoy-ops service account config, `gh api` runner status),
never from memory alone. For each: name and aliases; what it is (hardware, OS, cores, RAM,
disk); where it lives (host, IP or zone, user, SSH alias); state today (running, parked,
stopped) and who may start or stop it; what to use it for and not; tools that target it
(vm-run, decoy-vm-t3, win-run, recheck, runners); how to check it (one command each for
liveness, load, disk, runner busy); known gotchas (Dell mirror empty objects, test-5 runner,
gate venv companion, vm-run kill leaves remote). Nodes: devbox `.55`, pve2 `.116`, Dell 5820
(WSL2 and Windows lane), Mac mini, `decoy-test-5`, `decoy-test-6`, the gate runners, plus a
"retired, do not use" list. Header line: `Status: current`, the date checked, and the
owner's node policy (Dell and Mac first, one GCP overflow, 8 builders and 4 testers).

**Rules parity table** (in `TICK-decoy.md`, section "Rules parity"): one row per standing
rule with columns rule, source, where enforced (code test name, TICK step, brief line, or
config key). Sources to sweep: `orchestrator-loop.md` `/loop` prompt (lines 216 to 264) and
phase table (lines 131 to 181); every MEMORY.md entry marked STANDING or owner-dated that
governs the loop (at least: gate roster, Dennis lenses, plan flow and 2-round cap, Codex
usage tiers, root-cause fixes, builders route around, one writer per worktree, check merged
first, check nodes live, allocate to bottlenecks, close-out every merge, CHANGELOG pre-merge,
graph current, stacked PR base, red main first, test depth and merge testing, read T3 by
EXIT, lint own edits, log hygiene, commit messages, Slack plain language and times in CST,
closing set, plan buffer, capacity 8 and 4, release and Windows last, V1 bar, engine gate
dispatch per PR, no Fable, edit live scripts atomically, handoffs to Obsidian). Also every
item from Dennis round 1 M1: off-roadmap work limited to red-main and gate-blocking fixes;
at least 3 builders busy and the next critical-path row always staffed; the process-signals
table each tick; T3 baseline rewrite after each CLI main merge; engine and CLI halves
planned together with the release named; one PR per row, fewer larger slices; R2 plan
contents (pinned guarantee, established solutions, choke point with guard test and
mutation, placement against CODEMAP); V1-bar preamble to reviewers; scoped Codex prompts,
delta-only re-review, batched tiny PRs; never the author; the test-only fix diff check
(headless is stricter: full re-review, recorded as a deviation); `decoy-rows close` and
the row-closed note; claudeworkerbee retired; builder brief path; devbox runs no tests;
per-process 4 GB memory watch; owner-questions default rule; no per-PR full suite and
merge trains; stacked PRs one T3; coverage and mutation on changed units; plan defines
tests; survey sources first; guides and plans Opus only; commit and push working docs;
best practice becomes a sentry; registries and sizing; V1 pragmatic gates; solid base and
V1 health items; QA only on complete groups; Library and Trust QA held; owner handles
Codex; CLI V1 first, platform parked; init wizard stopped; scheduler never deleted; CLI
gates `--full`; gate venvs use venv-native-lock; parallel ledger files keep both; engine
repin checklist; dependency failures in a clean env; `git checkout` wipes; cost controls. A rule that
needs neither code nor text (retired or superseded) is listed with "retired" and the memory
that retired it.

- [ ] **Step 1:** Write `project.json`; run `devloop-state decoy check`; expect OK.
- [ ] **Step 2:** Done 2026-11-25 12:04 CST: `NODE-DIRECTORY.md` written from a live check,
  `dev-nodes` now points to it.
- [ ] **Step 3:** Write `PLAYBOOK-decoy.md`, then `TICK-decoy.md` with priority order, closing set, plan buffer, duties
  (morning repin, evening scorecard, daily roadmap PR, hourly row post is cron), and the
  parity table.
- [ ] **Step 4:** Add the `DEVLOOP_HEADLESS` early exits; check with `DEVLOOP_HEADLESS=1
  python3 ~/.claude/scripts/loop-protocol-reminder.py < /dev/null` printing nothing, and
  `DEVLOOP_HEADLESS=1 env -i HOME=$HOME PATH=/usr/bin:/bin` running the devloop notify path
  with a fake curl first on PATH: the fake must be called.
- [ ] **Step 5:** Gate S3 before cron starts: Dennis, then Codex, on `project.json`,
  `PLAYBOOK-decoy.md`, `TICK-decoy.md` with the parity table, `decoy-t3-wait`, the two helper edits, the hook
  wiring and the slack-notify guard. Codex reviews after Dennis GO.
- [ ] **Step 6:** No commit: the vault is not a git repo and syncs to `.116` by Syncthing.
  Run `qmd-refresh` so the new pages are searchable at once.

## Task 11: Cutover

- [ ] **Step 1:** Ping Slack: "Headless loop ready; stopping the /loop dispatcher."
- [ ] **Step 2:** Stop the live `/loop` from dispatching new work (owner's terminal or
  CronDelete of its schedule). Let its running workers finish and hand back there; review
  and test runs it already started are read there and recorded as slice evidence only
  through re-dispatch (no adoption: an adopted run has no argv to retry and its evidence
  cannot be checked).
- [ ] **Step 3:** Backfill from this interactive session (not headless): for each
  in-flight slice in `decoy-rows show`, open PRs and worktrees, `devloop-state decoy import`
  with phase, head, PR, risk, rulebooks, purpose, and `--plan-approved` for plans that
  passed their plan gate under `/loop`. Open PRs at GATE or later land in REVIEW and earn
  their gates again here; merged PRs land in DOCUMENT. Wait for every `/loop` writer on a worktree to finish before
  importing that slice. Compare the slice list with the board; every row with an active slice has a
  file.
- [ ] **Step 4:** `devloop-install ~/bin`; invoke the skill: "start the dev loop cron" for
  `decoy`. Watch the first two ticks: one applied result, one dispatch, STATUS.md renders, the
  vault copy updates.
- [ ] **Step 5:** First gated merge checked by hand against the slice evidence.
- [ ] **Step 6:** Mark revision 4 `Status: record`; close HO-1 to HO-5 as superseded with the
  defaults applied; ping Slack with the status page link.
- [ ] **Rollback:** "stop the dev loop" (PAUSE plus cron removal), wait until no writer run
  (builder, planner, codex-fix) is live or unapplied (`devloop-state list --writers`;
  apply or settle each), then restart `/loop` with STATUS.md as its
  handoff.
