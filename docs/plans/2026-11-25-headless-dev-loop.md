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
4. **Target:** first live tick today (2026-11-25). Slices 1 and 2 are the minimum to run;
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
- Deadlines: planner 2h, builder and slice-runner 4h, dennis 90m, codex-review 1h, codex-fix
  1h, test 2h, docs 1h. Idle kill: 20 minutes with no log growth.
- Caps from `project.json`; Decoy builder 8, tester 4, reviewer 2, planner 3. Memory floor
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
- Merge command errors or `gh` state unknown: slice stays GATE with `merge_unknown`, no retry
  (`devloop/merge.py`).

### Routes

Every run kind and its result reader (one table in `devloop/runs.py`, `KINDS`):
- `planner`, `builder`, `slice-runner`, `dennis`, `docs`: Claude worker writes `result.md`.
- `codex-review`: wrapper parses the last `VERDICT:` line from the out file.
- `codex-fix`: wrapper records exit and the plan file hash.
- `test`: wrapper parses the last `^EXIT n` line and failing test IDs (pattern from
  `~/bin/decoy-vm-t3:194`).
- An unknown kind is refused at dispatch.
- The kinds and deadlines come from the spec (`docs/specs/2026-11-25-headless-dev-loop-design.md:151`
  slots per kind, `docs/specs/2026-11-25-headless-dev-loop-design.md:314` dispatch).

### FK shapes

N/A: no database or foreign keys; references are run ID to slice key and slice to PR and
head, covered under fields.

### Fields

Slice file keys: `row, slice, phase, risk, rulebooks, worktree, branch, repo, pr, head,
plan_path, plan_sha, round, evidence{head_or_plan_sha: {kind: {verdict, run, at}}},
full_suite_required, run, waiting_on, next_action, last_moved_at, failures{kind}, merge_unknown,
park_reason, history[]`.
- Run `request.json`: `id, kind, row, slice, head, argv, worktree, slot, deadline_s, pid,
  pid_start, started_at, retry_of`.
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
| 1 | value-free output | `status.scrub()` is the only path to STATUS.md, vault copy and Slack text; fixed reason codes | `test_status_scrubs_planted_secret` | remove the `scrub()` call in `render_status` |
| 2 | cleanup on every exit path | wrapper `finally` writes `exit`; collect marks dead no-exit runs FAILED | `test_wrapper_writes_exit_on_kill`, `test_dead_run_without_exit_is_failed` | drop the `finally` block in `wrap.run` |
| 3 | state consistency | single writer `devloop-state`, atomic replace under flock, `TRANSITIONS` table | `test_bad_transition_leaves_file_unchanged`, `test_concurrent_set_no_lost_update` | write in place without `os.replace` |
| 4 | route completeness | `KINDS` table maps every kind to slot, deadline and reader | `test_every_kind_has_reader_and_slot`, `test_unknown_kind_refused` | delete the `test` entry from `KINDS` |
| 5 | referential integrity | `apply_run` checks run slice key and head against the slice | `test_apply_refuses_head_mismatch` | skip the head comparison in `apply_run` |
| 6 | untrusted input | `parse_result` contract; contradiction or missing status is FAILED | `test_zero_exit_without_result_is_failed`, `test_status_contradicts_exit_is_failed` | default a missing status to DONE |
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
| S3 Decoy wiring and cutover | 10, 11 | project.json, TICK-decoy.md, node directory, parity table, hook guards, backfill, first live tick | Dennis on the docs and config; owner watches first two ticks |

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
  next_action, waiting_on, risk, rulebooks, full_suite_required`; `full_suite_required` can
  only go False to True; a changed `head` increments nothing and leaves old evidence keyed by
  the old head); `move(project, key, to_phase, reason) -> dict` raising
  `TransitionError(reason)`; `record_evidence(project, key, kind, verdict, run_id, ref) ->
  None` (internal: called by `runs.apply_run`, not exposed on the CLI except `selfcheck` and
  `docs`); `slice_key(row, slice_id) -> str` = `f"{row}--{slice_id}"`.
- Transition table:

| From | To |
|---|---|
| FRAME | PLAN |
| PLAN | DEVELOP |
| DEVELOP | SELF_CHECK |
| SELF_CHECK | VERIFY, REMEDIATE |
| VERIFY | REVIEW, REMEDIATE |
| REVIEW | GATE, REMEDIATE |
| REMEDIATE | VERIFY, REVIEW, PLAN |
| GATE | DOCUMENT (only via `devloop-merge`), REMEDIATE |
| DOCUMENT | DONE |
| any active | PARKED, HELD |
| PARKED, HELD | the phase recorded at park time |

- Exit conditions checked by `move` (all on the current `head`, plan evidence on `plan_sha`):
  - FRAME to PLAN: `risk` in R0..R3 and `rulebooks` non-empty.
  - PLAN to DEVELOP: `plan_path` exists, contains `Rules consulted:`, and every reviewer in
    `reviews[risk].plan` has verdict GO on `plan_sha`, in config order (each GO's `at` later
    than the previous reviewer's).
  - DEVELOP to SELF_CHECK: `branch`, `head` set.
  - SELF_CHECK to VERIFY: `selfcheck` GO on head.
  - VERIFY to REVIEW: `t1` GO on head.
  - REVIEW to GATE: every reviewer in `reviews[risk].code` GO on head, in order.
  - Into REMEDIATE: `round += 1`; at round 3 the move goes to PARKED with reason
    `round-3` instead.
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
    slices.record_evidence(proj, k, "code:dennis", "GO", "r1", "a1")
    with pytest.raises(slices.TransitionError, match="codex"): slices.move(proj, k, "GATE", "x")

def test_new_head_clears_evidence(proj):
    k = mk_slice(proj, phase="REVIEW", risk="R1", head="a1")
    slices.record_evidence(proj, k, "code:codex", "GO", "r1", "a1")
    slices.set_fields(proj, k, head="b2")
    with pytest.raises(slices.TransitionError): slices.move(proj, k, "GATE", "x")

def test_round_three_parks(proj): ...  # two REMEDIATE entries ok, third lands PARKED, reason round-3

def test_board_mirror_calls_cli_only(proj, fakebin): ...  # fake board records argv; no rows.json touched

def test_concurrent_set_no_lost_update(proj): ...  # 20 processes set different fields; all present
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
  list[str], recommended: str, r3: bool) -> dict` (non-R3: `applied = recommended` at once,
  event, notify; R3: `applied = None`, slice waits); `answer(project, key, choice) -> None`.
  Problems and lessons are stored in `problems.json` and `lessons.json` (source of truth),
  rendered to `known-problems.md` and `lessons.md` after each write. Same key: `count += 1`,
  `last_seen` updated, no new entry. Lessons with `count >= 3` get `catalog_candidate: true`.
- CLI: `devloop-state <p> ticklog -`, `problem KEY --impact --fix`, `lesson KEY --area
  --wrong --instead`, `ask KEY --question --option ... --recommended X [--r3]`, `answer KEY X`,
  `problem-close KEY`.

- [ ] **Step 1: Failing tests** `test_repeat_problem_key_increments_count` (two calls, one
  entry, count 2), `test_ticklog_rolls_over_past_200` (205 entries, 200 in main file, 5 in the
  month file), `test_lesson_flagged_at_three`, `test_ask_non_r3_applies_default`,
  `test_ask_r3_waits`.
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
  - `KINDS: dict[str, Kind]` where `Kind(slot: str|None, deadline_s: int, reader: str,
    writes_worktree: bool)`: planner(planner, 7200, claude, False), builder(builder, 14400,
    claude, True), slice-runner(builder, 14400, claude, True), dennis(reviewer, 5400, claude,
    False), codex-review(reviewer, 3600, verdict, False), codex-fix(planner, 3600, exit,
    False), test(tester, 7200, exitline, False), docs(None, 3600, claude, False).
  - `dispatch(project, kind, key, *, brief: Path, worktree: Path|None, argv: list[str],
    retry_of: str|None=None) -> str` returns run ID or raises `Refused(reason)`. Admission
    under `admit.lock`: PAUSE or `cap-until` future; slot count of live runs `>=
    capacity[slot]`; live writer in same worktree when `writes_worktree`; `MemAvailable <
    min_free_gb`. Launch: `subprocess.Popen([devloop-wrap, project, run_id],
    start_new_session=True, stdin=DEVNULL, close_fds=True)`; records `pid` and `pid_start`
    (field 22 of `/proc/<pid>/stat`). Sets slice `run`.
  - `adopt(project, key, kind, *, pid: int|None, handle: str|None) -> str` for cutover.
  - `is_alive(req: dict) -> bool` (pid exists and start time matches).
  - `collect(project) -> list[str]` marks dead no-exit runs FAILED (writes `exit` with reason
    `lost`); returns finished, unapplied run IDs.
  - `parse_result(run_dir: Path, kind: str) -> Result(status, head, pr, verdict, tests, next,
    summary)`: `result.md` header lines `key: value` until a `---` line. Missing file, missing
    status, `status: DONE` with non-zero exit, or `status: FAILED` with exit 0 and no reason
    -> status FAILED.
  - `apply_run(project, run_id) -> None`: refuses if run slice key or head differs from the
    slice (event `apply-mismatch`, run marked applied with outcome `stale`); records
    evidence by kind (`dennis` -> `code:dennis` or `plan:dennis` when slice phase is PLAN,
    `codex-review` same pattern with reviewer name from config, `test` -> `t1`/`t2`/`full` per
    request `level`); FAILED: `failures[kind] += 1`, at 2 move to PARKED with reason and
    notify; writes `applied`.
  - Brief build: `build_brief(project, key, kind, body: str) -> str` from
    `skills/dev-loop/brief-template.md` with: risk, rulebook paths under `rules_dir`,
    `catalogs/recurring-defects.md` check, standing rules from `project.brief_extra`,
    `nodes.directory` for builder and test kinds, `lessons_for(area)`, the hand-back contract.
    For `codex-review` it appends: "End with one line: `VERDICT: GO`, `VERDICT:
    GO-WITH-FIXES` or `VERDICT: NO-GO`." Only `GO` counts as GO.
- CLI: `devloop-dispatch <project> KIND KEY --brief FILE [--worktree DIR] [--level t1|t2|full]
  [--retry-of RUN] -- ARGV...`; prints run ID or `REFUSED: <reason>` exit 3. `devloop-state <p>
  applied RUN`, `devloop-state <p> adopt KEY KIND --pid N | --handle H`.

- [ ] **Step 1: Failing tests:** the admission four, `test_dead_run_without_exit_is_failed`,
  `test_reused_pid_reads_dead` (request with live pid but wrong start time),
  `test_status_contradicts_exit_is_failed`, `test_zero_exit_without_result_is_failed`,
  `test_apply_refuses_head_mismatch`, `test_every_kind_has_reader_and_slot`,
  `test_unknown_kind_refused`, `test_failed_twice_parks`,
  `test_brief_contains_rules_lessons_and_contract`.
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
  DEVLOOP_RUN_DIR`; own process group. Polls every 15 s: deadline -> kill group, reason
  `deadline`; log size unchanged for `DEVLOOP_IDLE_SECS` (default 1200) -> kill, reason
  `hung`. In `finally`: build `result.md` for `verdict` and `exitline` readers if the worker
  did not write one (verdict: last `^VERDICT:\s*(GO|GO-WITH-FIXES|NO-GO)` in the out file
  named by `request.out` or the log, none -> status FAILED; exitline: last `^EXIT (\d+)`,
  0 -> GO, else NO-GO with failing IDs from lines matching `^FAILED `), write `exit` JSON
  atomically, then spawn `devloop-tick <project>` detached. Claude kinds use argv `claude -p
  --model <model> --permission-mode auto --no-session-persistence --output-format stream-json
  --verbose` so the log grows while the worker works.

- [ ] **Step 1: Failing tests:** `test_wrapper_writes_exit_on_kill` (payload `sleep 60`,
  deadline 1 s), `test_idle_payload_killed_as_hung` (`DEVLOOP_IDLE_SECS=1`),
  `test_codex_zero_exit_without_verdict_is_failed`, `test_exit_line_parsed`,
  `test_wrapper_wakes_tick` (fake `devloop-tick` touches a file).
- [ ] **Step 2 to 4:** run red, implement, run green.
- [ ] **Step 5: Commit** `feat(devloop): add worker wrapper with deadline and idle kill`

## Task 6: Merge on exact head

**Files:**
- Create: `devloop/merge.py`, `bin/devloop-merge`
- Test: `tests/devloop/test_merge.py`

**Interfaces:**
- Produces: `merge(project, key) -> str` returning `merged`, `refused:<reason>` or
  `unknown`. Steps: slice phase GATE; `merge.allowed` true; every `reviews[risk].code` GO,
  `t1` GO, `t2` GO when `tests.t2` configured, `full` GO when `full_suite_required`, all on
  head; `merge_unknown` set -> only re-check state. Notify "merging ROW SLICE PR". Run `gh pr
  merge <pr> -R <repo> --squash --match-head-commit <head>`. Then `gh pr view <pr> -R <repo>
  --json state,headRefOid`: `MERGED` and `headRefOid == head` -> evidence `merged`, move to
  DOCUMENT, board `merged ROW SLICE PR <next_action text>`, event; anything else ->
  `merge_unknown = true`, problem `merge-state-unknown`, return `unknown`.

- [ ] **Step 1: Failing tests:** `test_merge_refuses_head_mismatch` (PR head from fake gh
  differs), `test_merge_refuses_missing_full_suite`, `test_merge_unknown_state_no_retry` (second
  call does not invoke `gh pr merge`, asserted from the fake's argv log),
  `test_merge_success_moves_to_document`.
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
  run with `[redacted]`, and cuts free text to 160 chars. All free text goes through
  `scrub`.

- [ ] **Step 1: Failing tests:** `test_status_renders_counts_from_fixture_journal`,
  `test_status_scrubs_planted_secret` (next_action holds `ghp_` token; STATUS.md and vault
  copy lack it), `test_snapshot_capped`, `test_hash_ignores_ages_and_digits`.
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
  -p, --model, models.tick, --permission-mode, auto, --no-session-persistence,
  --output-format, stream-json, --verbose]`, stdin = generic `TICK.md` + project `tick_doc`
  file text + `intent` + `roadmap` + snapshot; env `DEVLOOP_HEADLESS=1`. After: `write_status`;
  `detect_cap(rc, output) -> datetime|None` (124 is a timeout, not a cap; a stream event with
  `"type":"rate_limit"` or a result line matching `(usage|rate) limit` sets reset from a
  parsed time or now+1h); timeout or cap -> one notify per distinct reason per 6 h (stored in
  `alerts.json`). Every pass ends with `post_tick_line(project, outcome) -> None`: one
  `notify` call, text built by `status.tick_line(project, outcome, summary)` where `outcome` is
  `model`, `skipped`, `paused` or `capped` and `summary` is the model's last `TICKLINE:` output
  line (falls back to counts from this pass's events). Format: `Dev loop HH:MM CST: <summary>.
  Running: B builders, T tests, R reviews. Next: <one item>.` Passed through `scrub`, cut to
  300 chars. A notify failure is logged to `cron.log` and never fails the tick.
  Continue loop only if `tick.requested` exists.
  - `cron.add(project, bin_dir, interval_min=30) -> None`: reads `crontab -l`, removes any
    line tagged `# devloop:<project>`, appends `*/30 * * * * PATH=<install PATH>
    HOME=<HOME> DEVLOOP_HEADLESS=1 <bin_dir>/devloop-tick <project> >> <state>/cron.log 2>&1
    # devloop:<project>`, writes with `crontab -`, re-reads and checks every old line is
    present, else restores the saved copy and raises. `cron.remove(project)` same pattern.
    Crontab binary overridable by `DEVLOOP_CRONTAB` for tests.

- [ ] **Step 1: Failing tests:** `test_every_tick_posts_one_slack_line` (one notify argv per
  pass for each of model, skipped, paused), `test_tick_slack_line_is_scrubbed`,
  `test_lock_collision_sets_requested_and_reruns`,
  `test_skip_unchanged_snapshot_until_two_hours`,
  `test_cap_detection_sets_cap_until_and_blocks_model`, `test_pause_collects_only`,
  `test_cron_add_is_idempotent_and_keeps_others`, `test_cron_remove_keeps_others`.
- [ ] **Step 2 to 4:** red, implement, green.
- [ ] **Step 5: Commit** `feat(devloop): add cron tick with lock, skip and cap detection`

## Task 9: Skill, TICK.md, brief template, install, shareability

**Files:**
- Create: `skills/dev-loop/SKILL.md`, `skills/dev-loop/TICK.md`,
  `skills/dev-loop/brief-template.md`, `skills/dev-loop/project.example.json`,
  `bin/devloop-install`; Modify: `README.md` (one section), `pyproject.toml` if needed
- Test: `tests/devloop/test_shipped.py`

**Interfaces:**
- `SKILL.md` modes (description names "dev loop", "start the dev loop cron", "check the dev
  loop state", "stop the dev loop"):
  - **start [project]:** if no `project.json`, ask the setup questions from the spec table
    one at a time, write it, show it. Run `devloop-state <p> check` (validates config, tools
    on PATH, roadmap path, rules_dir). Run one tick in a cron-like env. `devloop-cron add`.
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
  `TICKLINE: <one plain sentence of what this tick did>` line for the Slack post. Limits: state
  only through the commands; dispatch long work, never do it in the tick; never take an R3
  action; finish within 30 minutes.
- `brief-template.md` placeholders: `{risk} {rulebooks} {recurring_defects} {standing_rules}
  {node_directory} {lessons} {body} {contract}`.
- `devloop-install <bin_dir>`: copies each `bin/devloop-*` to `<bin_dir>/.name.tmp` then
  `os.replace`; links `~/.claude/skills/dev-loop` to the repo skill dir (replaces only a link,
  never a real dir).

- [ ] **Step 1: Failing tests:** `test_no_private_strings_in_shipped_files` (patterns
  `/home/`, `decoy`, `192\.168\.`, `\.116\b`, `\.55\b`, `obsidian`, `cam\b` over `devloop/`,
  `bin/devloop-*`, `skills/dev-loop/`), `test_example_project_validates`,
  `test_install_replaces_atomically`.
- [ ] **Step 2 to 4:** red, write files, green.
- [ ] **Step 5: Commit** `feat(devloop): add dev-loop skill, tick prompt and installer`
- [ ] **Step 6: Mutation check:** `tests/devloop/mutations.sh` applies each mutation in the
  defect table with `sed` on a temp copy, runs its test, expects FAIL, restores. All killed.

## Task 10: Decoy wiring: project.json, TICK-decoy.md, node directory, parity table

Local and vault files only; nothing here goes in the plugin repo.

**Files:**
- Create: `~/.local/state/devloop/decoy/project.json` (values from the spec's Decoy column;
  `reviews` R1 `{plan:[codex], code:[codex]}`, R2 `{plan:[dennis,codex], code:[dennis,codex]}`,
  R0 `{plan:[], code:[codex]}`; `notify.cmd` slack-notify manual; `board` decoy-rows with
  the spec's phase map; `nodes.directory` the vault node directory; `snapshot_cmds`:
  node-watch status, decoy-capacity, `decoy-rows show`, `gh run list` on main for CLI and
  engine, `gh pr list` for both repos, the roadmap section, `OWNER-QUESTIONS.md` open items)
- Create: `~/obsidian-vault/10_Projects/decoy/TICK-decoy.md`
- Create: `~/obsidian-vault/10_Projects/decoy/NODE-DIRECTORY.md`
- Modify: `~/.claude/skills/dev-nodes/SKILL.md` (becomes a short pointer to the directory)
- Modify: `~/.claude/scripts/loop-protocol-reminder.py`, `~/.claude/scripts/slack-notify.sh`
  (exit early when `DEVLOOP_HEADLESS=1`; edit copy then `mv`)

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
dispatch per PR, no Fable, edit live scripts atomically, handoffs to Obsidian). A rule that
needs neither code nor text (retired or superseded) is listed with "retired" and the memory
that retired it.

- [ ] **Step 1:** Write `project.json`; run `devloop-state decoy check`; expect OK.
- [ ] **Step 2:** Live-check every node; write `NODE-DIRECTORY.md`; point `dev-nodes` at it.
- [ ] **Step 3:** Write `TICK-decoy.md` with priority order, closing set, plan buffer, duties
  (morning repin, evening scorecard, daily roadmap PR, hourly row post is cron), and the
  parity table.
- [ ] **Step 4:** Add the `DEVLOOP_HEADLESS` early exits; check with `DEVLOOP_HEADLESS=1
  python3 ~/.claude/scripts/loop-protocol-reminder.py < /dev/null` printing nothing.
- [ ] **Step 5:** Dennis reviews the parity table against the sources (gate item).
- [ ] **Step 6:** Commit the vault files (`docs(decoy): add headless loop tick doc and node
  directory`).

## Task 11: Cutover

- [ ] **Step 1:** Ping Slack: "Headless loop ready; stopping the /loop dispatcher."
- [ ] **Step 2:** Stop the live `/loop` from dispatching (owner's terminal or CronDelete of
  its schedule). List its running workers.
- [ ] **Step 3:** Backfill: for each in-flight slice in `decoy-rows show`, open PRs and
  worktrees, `devloop-state decoy slice new` with phase, head, PR, risk; `adopt` each running
  worker by PID. Compare the slice list with the board; every row with an active slice has a
  file.
- [ ] **Step 4:** `devloop-install ~/bin`; invoke the skill: "start the dev loop cron" for
  `decoy`. Watch the first two ticks: one applied result, one dispatch, STATUS.md renders, the
  vault copy updates.
- [ ] **Step 5:** First gated merge checked by hand against the slice evidence.
- [ ] **Step 6:** Mark revision 4 `Status: record`; close HO-1 to HO-5 as superseded with the
  defaults applied; ping Slack with the status page link.
- [ ] **Rollback:** "stop the dev loop", restart `/loop` with STATUS.md as its handoff.
