---
Status: plan
updated: 2026-11-27 (revision 3, after Dennis round 2 and code review)
---

# RUN-STATUS: programmatic run statuses in a SQLite store

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
profiles/local-operations, autonomous-operation, architecture, cross-cutting-concerns,
data-and-migrations, api-and-compatibility, observability-and-resilience, security,
testing, proportionality, workspace-and-vcs, destructive-operations.

Risk: **R2** overall (schema, migration, concurrency, the write path every agent uses).
Each slice has its own risk in section 10.3. Plan review: Dennis (at most 2 rounds), then
Codex through codex-fix to green. Code review: Codex for R1 slices; Dennis then Codex for
R2 slices.

Owner ask (2026-11-27): "Programmatic statuses that agents must report." Slices and runs
become rows in a database, not text that drifts. Subagents update a run log as they work,
so there is a record of everything and results can be queried without parsing text.

Owner decisions (2026-11-27):

- Every RUN-STATUS slice branches from the plugin's `main` and merges back to `main` when
  done. Nothing stays on a side branch.
- Slice 0 gets the narrow review: the merge resolutions, the unpushed commits, a full
  suite on the merge head, and the ancestor check plus a reviewed list of what each
  landed branch adds (section 10.2). The owner approved this exception on 2026-11-27.

After Dennis round 1, slice 0
lands from the real install source, and the parity diff is replaced by an ancestor check.
Three invariants are added (I14 to I16). The import keeps every record. A run can now
cover many slices. Leases are judged at read time, and long waits are declared. The
state-file inventory is complete, questions have one source of truth, and the rollback
mirror is written on every change. S3 is split into S3a and S3b. Section 14 logs the
V2 items.

## 1. Frame

### 1.1 Where the code is today

- `origin/main` of the plugin has **no `devloop/` package**. All loop code is on
  unmerged branches that stack on `origin/feat/headless-loop` (131 commits past main).
- The installed library (`~/.local/share/devloop/lib`, a link to
  `versions/1795737073500286094`, which `~/bin/devloop-state` and `~/bin/devloop-tick`
  load) is **byte-identical in `devloop/` to `origin/feat/push-gate-s1` at `a753f7f`**.
  That branch also equals the `restore-slice-runner` worktree. It already contains
  `feat/docs-skip-qa` (with the PR-create guard) and `feat/t1-batch` (with its
  `batch.py`). It has `devloop-state merged` (`merge.record_direct`), which the live
  loop uses.
- `feat/full-suite-batches` (`eec303c`) is **not** in the install. Its `batch.py`, which
  lets a full run test a pair of slices, differs from the installed one.
- `feat/dash-mission-control` (dash-mc, `47ee2e4`) runs the live dashboard from the
  `.worktrees/dash-mc` worktree. It and push-gate-s1 diverge: 44 commits are only on
  dash-mc and 23 only on push-gate-s1. Local dash-mc is 9 commits ahead of its origin
  copy.
- Local `main` has 5 unpushed "rules: sync ..." commits that are on no other branch, and
  is 1 commit behind `origin/main` (`174096a`, the rules-gate change).

So the build cannot branch from main as it is now. Slice 0 (section 10.2) lands the
install source and the dashboard on main first. Every build slice then branches from
main. This plan cites code in the installed lib (= push-gate-s1), and dash-mc for
dashboard code.

### 1.2 How state works today

- **Slices:** one JSON file per slice in `slices/` (77 files), written only by
  `slices.py` under an `fcntl.flock` on `<file>.lock` (`store.locked`). Each file holds
  a 200-entry `history` list and JSON blobs for evidence and plan rounds.
- **Events:** `events.jsonl`, appended under `events.jsonl.lock` with an fsync per line.
  2606 events from 2026-11-25 21:02 UTC to 2026-11-27 06:35 UTC.
- **Runs:** one directory per run in `runs/` (527 directories, 223 MB). Each holds a frozen
  `request.json`, `proc.json`, `exit`, `applied` and the result file. Run ids are
  `<kind>.<key>.<stamp>`, for example `builder.24zj--E-S1a.20261126T135847fa63`.
  **Only the headless dispatcher creates runs.** The last `run-dispatched` event is
  2026-11-26 23:14 UTC (17:14 CST). Since the project went to live mode, subagents leave
  no run record. Their only trace is the free text in a slice's `next_action`.
- **One run can cover many slices:** `batch.py` dispatches one t1 run for a batch of
  slices, and the 18:37 tech writer worked on 5 slices at once.
- **Liveness for headless runs:** the wrapper's pid, start time and boot id, plus an idle
  limit on output growth (`wrap.py`). Live subagents have none of this.
- **Locks:** 21 top-level `.lock` files, 78 slice locks, and 1,969 lock files inside run
  directories.
- **Owner questions:** the vault page `OWNER-QUESTIONS.md`. The owner answers by editing
  that page, and `notes.read_answers` applies the answers. The page is the owner's input
  surface.
- **Live mode bypass:** `slices._move` in live mode skips both the transition table and
  the evidence checks (`if to not in TRANSITIONS[frm] and not (live and ...)`). The
  live-mode skill (`~/.claude/skills/dev-loop-ui/SKILL.md`) says that moves "skip the
  headless run-evidence checks".
- **The tick in live mode:** the headless cron is off. `devloop-tick` runs only when the
  orchestrator calls it. Anything judged only by the tick goes stale when the
  orchestrator is busy or hung.

### 1.3 The failure cases, with evidence from the live state

Read from `~/.local/state/devloop/decoy/` and GitHub on 2026-11-27, read only.

1. **Plan slices in build phases.** 11 `slice-moved` events put 9 different plan slices
   into DEVELOP. Examples: `RUN-STREAM--plan` (twice), `PROP-FALLBACK--plan` (twice),
   `PER-COLUMN-ROUTING--plan` and `EXPERIMENTAL-TIER--plan` (2026-11-26 15:55 UTC).
   Today 3 plan slices sit in VERIFY: `PER-COLUMN-ROUTING--plan`,
   `COLUMN-SHAPE-RULE--plan` and `RUN-STREAM--plan`. Plan slices also use REVIEW and
   REMEDIATE for plan-review rounds (`58--G7-2-plan` and `58--G7-3-plan`, 8 moves between
   04:09 and 04:28 UTC). In total, 100 of the 722 recorded moves are not in
   `slices.TRANSITIONS`. Examples: VERIFY to GATE (10), REMEDIATE to REVIEW (9), FRAME to
   DEVELOP (8), DOCUMENT to DOCUMENT (6).
2. **DOCUMENT slices with no shipped-log entry after the tech writer died.** Five slices
   have the same `next_action`: "Tech writer (subagent): batched shipped-log PR + engine
   graph refresh, started 18:37 CST". They are C6C-I--evidence-kwargs (#817),
   EMPTY-LAYOUT-PARITY--fix (#791), EXPERIMENTAL-TIER--S0 (#832), DECOY-LANG--S2 (#419)
   and TNR-ROADMAP--docs (#415). On CLI `origin/main`, `docs/RECENTLY-SHIPPED.md` has no
   entry for #817, #791 or #832. No process, run record or event shows the writer after
   18:37, and nothing told the orchestrator.
3. **A wait on an owner question that was already answered.** `NONFK-OOM--C1-docs` is
   PARKED with `waiting_on` "ENGINE-PIN (owner question: merge #408 now or after engine
   #791; #791 is now merged)". The question `ENGINE-PIN--0.28.4` has the answer
   `wait-for-fix`.
4. **A GATE slice whose branch lacks main.** `DATE-SHIFT-TYPED--S1b-1` is in GATE. Its
   `next_action` says "Engine gate passed on 01f9f7b35 and CI green, but the branch lacks
   main (#822)".
5. **A red check under "merge when green".** Engine PR #789 (`58--G7-1`) has its required
   check `engine-gate/verdict` failing. Engine PR #809 (`F31-AUDIT--AE3`) has
   `compat-preflight` failing. Engine main requires it through branch protection. GitHub reports BLOCKED. Both slices say they will merge when green. Nothing in the state shows
   that green will not come.
6. **Orphaned headless runs in live mode.** Four run directories have `exit` and no
   `applied`, `rejected`, `released` or `discarded`: the runs of `ENGINE-SELECT--S1`,
   `EXPECT-OFF-ROUTE--S2`, `WHEN-READS-SOURCE--S2` and `24zj--E-S1a`. Each of those
   slices still points at its run in the `run` field, and its `next_action` reads as
   work in progress. The mode is live, so no tick will apply them.
7. **Two open slices share one PR.** Engine #828 belongs to both
   `COLUMN-SHAPE-RULE--plan` and `COLUMN-SHAPE-RULE--E5`. Engine #829 belongs to both
   `PER-COLUMN-ROUTING--plan` and `PER-COLUMN-ROUTING--S0`. A merge recorded on one
   leaves the other open.

Smaller signs of the same drift: `RUN-STATUS--plan` and `PUSH-GATE--redesign` record
`repo=engine` for plugin work. There are 302 `board-mirror-failed` events (42% of the
moves) in the journal, which hide the useful events.

### 1.4 Definition of done

- Every subagent and every long command the orchestrator starts has a run row: role,
  the slices it covers, lease, last beat, an ordered step log, end status, verdict, head
  and test counts. A reader gets these from a typed command or from SQL, never by parsing
  prose.
- A run that stops beating past its lease shows as "runner silent" on every read
  (STATUS.md, the dashboard, `run list`) from the moment the lease ends, with or without
  a tick. The tick also prints it.
- Each tick runs the invariant list (section 7). Each of the seven cases above is caught
  by a named invariant, on today's data.
- Illegal phases and moves are refused when they are written. After phase 2, the
  database's own constraints refuse them too, except for rows imported with the legacy
  flag, which are reported until fixed.
- After phase 2, all slice, event, run, question-copy, problem and ledger state is in one
  SQLite file. The import keeps every record (no skips), and a rollback path is tested.

### 1.5 Guarantee and non-goals

**Guarantee:** state written through `devloop-state` is typed, value-free, checked
against the phase rules, and durable when many writers run at once. A run that stops
reporting reads as silent the moment its lease ends. The invariants in section 7 run
every tick.

**Non-goals:**

- No change to how the owner answers questions. The vault page stays the input surface
  and the source of truth for answers (section 8.3).
- No raw SQL for agents. The owner and the orchestrator may read the file with the
  `sqlite3` CLI or DuckDB for reports, read only.
- No move of run artifacts (result files, transcripts, worktrees: 223 MB) into the
  database. Rows point at the run directory.
- No automatic fixes from invariants. They report. Leases are judged, not acted on,
  except that `run sweep` records the silent state change for the log.
- No defense against a hostile agent that forges a token or writes SQL directly. The
  threat model is local, single user, accidents only (owner decision, memory
  "devloop-local-threat-model").
- No remote writers over a network filesystem. A remote node reports through a command
  that runs on the devbox (over ssh), never through a shared mount of the database file.
- No redesign of the headless dispatcher. Its `runs/` directories keep working, and
  phase 2 indexes them as rows.
- No landing of `feat/full-suite-batches` in this plan (section 10.2).

### 1.6 Expected size

About 4,300 changed lines over 8 build slices, tests included, plus slice 0 (section
10.2). New public names: the `devloop-state <project> run ...`, `invariants` and
`db ...` command families (section 5), the `devloop.state` sub-package, the slice field
`kind`, and the project config keys `store` and `invariants`. Each slice's size is in
section 10.3.

## 2. Established solutions

Searched on 2026-11-27, online and in the codebase.

- **SQLite WAL** ([sqlite.org/wal.html](https://www.sqlite.org/wal.html)): readers do
  not block writers, and writers do not block readers. Only one writer runs at a time.
  WAL does not work over a network filesystem. A checkpoint cannot pass an active reader,
  so readers that stay open can make the WAL grow without bound ("checkpoint
  starvation"). With `synchronous=NORMAL`, recent commits can roll back after a power
  loss. With `FULL`, every commit syncs. After a crash, the next connection runs recovery,
  and committed data in the WAL survives. The `-wal` file must never be separated from
  the database.
- **Write transactions** ([lang_transaction](https://www.sqlite.org/lang_transaction.html),
  [isolation](https://sqlite.org/isolation.html), Simon Willison on
  [SQLITE_BUSY](https://simonwillison.net/2025/Feb/17/sqlite-busy)): a deferred
  transaction that reads and then upgrades to write can fail with `SQLITE_BUSY` (or
  `SQLITE_BUSY_SNAPSHOT` in WAL) at once, and the busy timeout does not help it.
  `BEGIN IMMEDIATE` takes the write lock at the start, so `busy_timeout` applies. The
  common setting is a `busy_timeout` of about 5000 ms.
- **Schema versions:** `PRAGMA user_version`
  ([pragma](https://www.sqlite.org/pragma.html#pragma_user_version)) plus an ordered list
  of migrations, each run once in one transaction. A `migrations` table records the name
  and checksum of each step.
- **Constraints:** `STRICT` tables ([stricttables](https://www.sqlite.org/stricttables.html),
  SQLite 3.37 or later; the devbox has 3.40.1 and the plugin venv has 3.53.1), `CHECK`
  constraints, foreign keys (off by default, so `PRAGMA foreign_keys=ON` on every
  connection, see [foreignkeys](https://www.sqlite.org/foreignkeys.html)), partial unique
  indexes, views, and `BEFORE UPDATE` triggers that `RAISE(ABORT)`.
- **Leases and heartbeats:** Temporal's activity heartbeat timeout
  ([docs](https://docs.temporal.io/encyclopedia/detecting-activity-failures)) is
  separate from the whole-task timeout. Heartbeats are sent at most every 0.8 of the
  timeout. Job queues on SQLite and Postgres (go-flywheel, forge-jobs, the
  [heartbeat-lease write-up](https://webdock.io/en/docs/how-guides/programming-guides/handling-worker-crashes-with-heartbeat-based-leases))
  extend a lease from a background heartbeat while the work runs. They judge expiry with
  a timestamp compare at read time (`lease_until < now`), not only in a sweeper. They use
  a fencing (claim) token on final writes so a stale worker cannot finish a job it lost.
- **In the codebase:** `store.locked` and atomic replace (today's model), `runs.KINDS`
  and `runs.KIND_PHASES` (the headless role-to-phase table), `wrap.py` (deadline plus
  idle limit), `tick._alert` (one alert per reason per six hours), `scrub.py` (secret,
  path and hex patterns), `store.canonical_digest` on dash-mc (a canonical JSON digest),
  `check.run_checks` (the doctor checks), and `merge.repos.<repo>.required_checks` in
  `project.json` (the loop configuration).

**What the design follows:** WAL with `synchronous=FULL` (today's code fsyncs every
write, so durability stays the same), `busy_timeout=5000`, `BEGIN IMMEDIATE` for every
write, `user_version` migrations, STRICT tables with CHECK constraints and foreign keys,
a Temporal-style heartbeat lease judged at read time, and a fencing token. A background
beater keeps a long local command alive, and a declared wait covers work that cannot
beat. Everything uses the stdlib `sqlite3` module, with no new dependency. Every query
binds its values as parameters; no value is put into SQL text (security rulebook).

**Custom code that remains, and why:** the run commands and the invariants use this
loop's own vocabulary (roles, phases, slices). No queue library fits a "worker" that is
a Claude subagent able only to run shell commands. A library would add a daemon and a
dependency to support a few small commands.

## 3. Design

### 3.1 Phase order, checked against the code

The owner's order is phase 1 (commands, leases and invariants on today's store), then
phase 2 (the move to SQLite with an import of the history). The code supports this
order with one change: **run rows go into SQLite already in phase 1.**

Why:

- Live runs have no store today, so there is no live run history to import. A JSON
  format for live runs, with its own lock files, would be built in phase 1 and thrown
  away in phase 2.
- Heartbeats are the highest write rate in the system: up to 16 runs, each beating every
  few minutes. These writes need WAL and `BEGIN IMMEDIATE` the most.
- Most invariants only read, so they work on the JSON store as it is. Those come first
  (S1), because they catch most of today's cases with no change to the write path. The
  invariants that need run rows (I3, I8, I9, I13) come with the run store (S3b).
- Slices, events, questions and problems carry history, and the dashboard and STATUS.md
  read them. Their move is the risky part, so it stays in phase 2, with an import, a
  shadow period and a rollback.

So in phase 1 the state is in two places: the JSON files, and `devloop.db` with only the
run tables. The dashboard gets one new reader for runs. Phase 2 moves the rest into the
same file.

### 3.2 Design notes

- **New structure:** a sub-package `devloop/state/`. The top-level `devloop/` package
  already has 27 modules, over the fan-out limit of about 25 in the architecture
  rulebook, so this plan adds no top-level modules.
- **Layers inside `devloop/state/`** (clean-architecture lens; the pain is a package
  cycle and I/O mixed into policy):
  - *Rules (no I/O):* `phases.py` (phases, transitions, slice kinds, live kinds),
    `fields.py` (the slice field validators, moved out of `slices.py`), `text.py` (the
    text registry), `invariants.py` (pure checks over a snapshot).
  - *Storage:* `db.py` (the only module that opens SQLite), `schema.py` (migrations).
  - *Adapters with I/O:* `probes.py` (git and `gh` calls for invariants, with caching),
    `runlog.py` (run storage operations), `importer.py`, `export.py`.
  - *Interface:* `run_cli.py` (argparse subcommands).
- **Import direction (enforced by a test, S3a):** rules modules import nothing from
  `devloop` except `devloop.scrub` and each other. Storage imports rules and
  `devloop.store`. Adapters import storage and rules. Top-level modules (`slices.py`,
  `runs.py`, `notes.py`, `status.py`, `tick.py`, `state_cli.py`) may import from
  `devloop.state`. Nothing in `devloop.state` imports `slices`, `runs`, `notes`, `tick`
  or `state_cli`. This removes the cycle Dennis found: `PHASES`, `ACTIVE`,
  `TRANSITIONS` and `_FIELDS` move into `state/phases.py` and `state/fields.py`, and
  `slices.py` re-exports the names so old callers still work. The importer uses
  `state/fields.py`, not `slices._validate`.
- **One choke point per rule** (cross-cutting concerns). All database access goes
  through `state/db.py`. All free text goes through the column registry in
  `state/text.py`. The phase rules are in one table, and the phase 2 trigger data is
  generated from it.
- **Dependency inversion, only where a second backend really exists.** The phase 2
  switch preserves the public read contracts. Slices and ledger readers select the backend.
  The question parser and headless run directory iterator still read their input files.
  The dashboard reads the question copy through a separate read operation. No new abstract layer is added.
- **One place per fact:** headless roles stay in `runs.KINDS` and `runs.KIND_PHASES`,
  unchanged. Live kinds go in a separate table, `state/phases.py: LIVE_KINDS`, with a
  deadline and the allowed phases per kind (section 5.2). Legal moves are in
  `state/phases.py`. Text limits are in `state/text.py`. Invariants are in
  `state/invariants.py`. The SQL CHECK value lists are generated from these Python
  tuples when the schema is built, never typed twice.
- **House style:** typed context objects (`RunCtx`, `Snapshot`), no flag arguments that
  switch behavior, and comments that explain why.

### 3.3 Placement

The plugin has no CODEMAP. Slice 1 adds `docs/CODEMAP.md` with a short "Where things go"
section for `devloop/`, `devloop/state/` and `devloop/dash/` only.

| New file | Directory | Map line (added in S1) | Sits beside |
|---|---|---|---|
| `__init__.py` | `devloop/state/` | "state rules, storage, run records and invariants go in `devloop/state/`" | `devloop/store.py` |
| `phases.py`, `fields.py` | `devloop/state/` | same | `devloop/slices.py` |
| `invariants.py`, `probes.py` | `devloop/state/` | same | `devloop/check.py` |
| `db.py`, `schema.py` | `devloop/state/` | same | `devloop/store.py` |
| `text.py` | `devloop/state/` | same | `devloop/scrub.py` |
| `runlog.py`, `run_cli.py` | `devloop/state/` | same | `devloop/runs.py`, `devloop/state_cli.py` |
| `importer.py`, `export.py` | `devloop/state/` | same | `devloop/state/db.py` |
| tests | `tests/devloop/` | "tests mirror the package" | the existing `tests/devloop/test_*.py` |

`state_cli.py` stays the one entry point. It adds subparsers and hands off to
`state/run_cli.py`, so it stays under the 800-line cap. `slices.py` (1,063 lines) gets
smaller, because phases and validators move out.

### 3.4 Reuse inventory

| Need | Existing code that does it | Use |
|---|---|---|
| Refusal exit code | `state_cli.REFUSALS` and the `REFUSED:` line with exit 3 | Reuse as is; new errors join the tuple |
| Name checks | `store.NAME`, `store.TOKEN`, `store.check_name` | Reuse for slice keys, run ids and steps |
| Secret and path shapes | `scrub._TOKEN`, `_URL_CRED`, `_LONG`, `_PATH` | Expose one public pattern check in scrub.py and reuse it from text.py |
| Headless deadlines and phases | `runs.KINDS`, `runs.KIND_PHASES` | Read only; unchanged and pinned by a test |
| Process liveness | `runs.proc_start` (pid plus start time) | `run exec` uses it for the beater |
| Alert dedupe | `tick._alert` and `alerts.json` | Reuse for silent-run alerts |
| Canonical digest | `store.canonical_digest` (dash-mc) | Import idempotency and reconcile |
| Slice validation | `slices._validate`, `_FIELDS` | Moved to `state/fields.py`; slices re-exports |
| Questions parser | `notes.load_questions`, `notes.read_answers` | The vault page stays the source; the db copies it |
| Required checks | GitHub PR rollup and repo rules from `merge.repo_settings` | I14 combines them as described in section 7 |
| Doctor checks | `check.run_checks` | Adds `quick_check` and the stray-JSON warning |

New code is only what has no owner today: the SQLite connection module, the run
commands, the invariant registry, the probes and the importer.

### 3.5 Frozen contracts

These must not change, or may change only by adding:

- Every existing `devloop-state` command, including `merged`: name, arguments, stdout
  format and exit codes (0, 2 and 3). Exit codes 4 and 5 apply only to the new `run`
  and `db` commands.
- The `events.jsonl` line format, including `append_event(once=...)` behavior, while
  JSON is written, because the dashboard on dash-mc and `status.summarize` parse it.
- The headless run directory layout (`request.json`, `proc.json`, `exit`, `applied`,
  `repair.json`, result header), read by `apply.collect` and the dashboard.
- `runs.KINDS` and `runs.KIND_PHASES`: what the headless dispatcher accepts.
- The slice JSON fields. `schema_version` 2 only adds `kind`, and version 1 files still
  load.
- The owner questions page format in the vault.
- The STATUS.md sections that exist today. New sections are added after them.

A snapshot test per contract (CLI help output, an event line, a slice file, a STATUS.md
render, the two headless tables) is part of S1 and is kept for every later slice.

## 4. Schema

One file per project: `~/.local/state/devloop/<project>/devloop.db`, mode 0600, on local
disk. Create the database, backups, raw exports, mirrors and journals with owner-only
access from the first open. Test permissions under umask 022, including WAL sidecars
and events.jsonl. Every table is `STRICT`. The CHECK value lists are generated from the Python
tuples named in section 3.2.

Phase 1 (slice S3a) creates `migrations`, `runs`, `run_slices`, `run_log`, `run_seq` and
the `runs_now` view. Phase 2 (slice S5) adds the rest.

```sql
CREATE TABLE migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  sha TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT;

CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY,
  origin       TEXT NOT NULL CHECK (origin IN ('live','headless')),
  seq          INTEGER UNIQUE,       -- live runs only
  role         TEXT NOT NULL CHECK (role IN ('planner','builder','reviewer','tester',
                                             'tech-writer','duty')),
  kind         TEXT NOT NULL,        -- a runs.KINDS name or a LIVE_KINDS name
  node         TEXT NOT NULL CHECK (node IN ('devbox','mac','dell','gcp')),
  state        TEXT NOT NULL CHECK (state IN ('running','waiting','silent','done',
                                              'blocked','failed','abandoned','lost')),
  token        TEXT,                 -- fencing token; live runs only; never rendered
  lease_s      INTEGER CHECK (lease_s IS NULL OR lease_s BETWEEN 300 AND 14400),
  deadline_at  TEXT,
  started_at   TEXT NOT NULL,
  last_beat_at TEXT,
  lease_until  TEXT,                 -- the lease end, or the declared wait end
  wait_on      TEXT,                 -- the declared wait (section 6), value-free
  ended_at     TEXT,
  ended_by     TEXT CHECK (ended_by IN ('runner','orchestrator','import')),
  verdict      TEXT CHECK (verdict IN ('GO','GO-WITH-FIXES','NO-GO')),
  head         TEXT CHECK (head IS NULL OR (length(head) BETWEEN 7 AND 64
                                            AND head NOT GLOB '*[^0-9a-f]*')),
  head_verified INTEGER NOT NULL DEFAULT 0 CHECK (head_verified IN (0,1)),
  pr           INTEGER CHECK (pr IS NULL OR pr > 0),
  passed       INTEGER CHECK (passed IS NULL OR passed >= 0),
  failed       INTEGER CHECK (failed IS NULL OR failed >= 0),
  summary      TEXT,                 -- value-free, at most 200 characters
  run_dir      TEXT,                 -- headless runs only
  request_sha  TEXT,                 -- headless runs only (from runs.index.json)
  legacy       INTEGER NOT NULL DEFAULT 0 CHECK (legacy IN (0,1)),
  legacy_reason TEXT,
  -- live ids are r-YYYYMMDD-NNNN; headless ids are the store.TOKEN shape the dispatcher made
  CHECK ((origin = 'live' AND run_id = printf('r-%s-%04d', substr(run_id,3,8),seq)
          AND seq > 0 AND run_id GLOB 'r-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9]*'
          AND token IS NOT NULL AND lease_s IS NOT NULL AND seq IS NOT NULL)
      OR (origin = 'headless' AND run_dir IS NOT NULL)),
  CHECK ((state IN ('running','waiting','silent')) = (ended_at IS NULL)),
  CHECK ((ended_at IS NULL) = (ended_by IS NULL)),
  CHECK (legacy = 1 OR state <> 'done' OR role NOT IN ('reviewer','tester')
         OR verdict IS NOT NULL),
  CHECK ((legacy = 1) = (legacy_reason IS NOT NULL))
) STRICT;
CREATE INDEX runs_live ON runs(state, lease_until);

-- One run can cover many slices (a batched tech writer, a t1 run over a pair).
CREATE TABLE run_slices (
  run_id    TEXT NOT NULL REFERENCES runs(run_id),
  slice_key TEXT NOT NULL,           -- REFERENCES slices(key) from phase 2
  start_phase TEXT,                  -- required at live admission, per slice for I13
  writer    INTEGER NOT NULL CHECK (writer IN (0,1)),   -- builder or tech-writer
  open      INTEGER NOT NULL CHECK (open IN (0,1)),     -- 1 while the run is not ended
  PRIMARY KEY (run_id, slice_key)
) STRICT;
-- Two writers on one slice is a standing ban (memory: no-concurrent-builders).
CREATE UNIQUE INDEX one_live_writer ON run_slices(slice_key) WHERE writer = 1 AND open = 1;
CREATE INDEX run_slices_by_slice ON run_slices(slice_key, open);
CREATE TRIGGER run_close_slices AFTER UPDATE OF ended_at ON runs
  WHEN NEW.ended_at IS NOT NULL
  BEGIN UPDATE run_slices SET open = 0 WHERE run_id = NEW.run_id; END;

-- Lease expiry is judged at read time. Every reader uses this view, never runs.state.
CREATE VIEW runs_now AS
  SELECT *, CASE
    WHEN state IN ('running','waiting')
         AND lease_until < strftime('%Y-%m-%dT%H:%M:%fZ','now') THEN 'silent'
    ELSE state END AS state_now
  FROM runs;

CREATE TABLE run_log (              -- append-only: the record of everything a run did
  id     INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  at     TEXT NOT NULL,
  step   TEXT NOT NULL CHECK (step IN ('started','note','plan-read','worktree-ready',
          'build-started','commit','pushed','pr-opened','test-started','test-done',
          'review-started','review-done','docs-written','blocked','waiting','resumed',
          'ended','silent','revived','abandoned','lease-held','lease-lost')),
  note   TEXT                       -- value-free, at most 120 characters
) STRICT;
CREATE TRIGGER run_log_no_update BEFORE UPDATE ON run_log
  BEGIN SELECT RAISE(ABORT, 'run_log is append-only'); END;
CREATE TRIGGER run_log_no_delete BEFORE DELETE ON run_log
  BEGIN SELECT RAISE(ABORT, 'run_log is append-only'); END;
CREATE TABLE run_seq (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL) STRICT;
```

Timestamps are stored as UTC ISO strings with a `Z` suffix and fixed width, so string
compares order them. `db.py` writes them; a test checks that the `runs_now` compare and
the Python compare agree.

Phase 2 adds these tables. Columns are shortened here, and every TEXT column is in the
`text.py` registry.

```sql
CREATE TABLE repos (name TEXT PRIMARY KEY, fetched_at TEXT, gh_at TEXT) STRICT;   -- filled from project.json repos
CREATE TABLE slices (
  key TEXT PRIMARY KEY, row TEXT NOT NULL, slice TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('build','plan','docs','duty','release')),
  repo TEXT NOT NULL REFERENCES repos(name),
  phase TEXT NOT NULL CHECK (phase IN (/* PHASES */)),
  risk TEXT CHECK (risk IN ('R0','R1','R2','R3')),
  branch TEXT, pr INTEGER CHECK (pr IS NULL OR pr > 0), head TEXT /* hex rule as runs */,
  worktree TEXT, plan_path TEXT, purpose TEXT, next_action TEXT,
  run TEXT,                                              -- today's slice.run pointer
  waiting_kind TEXT NOT NULL DEFAULT 'none' CHECK (waiting_kind IN
    ('none','owner-question','slice','run','external','slot')),
  waiting_ref TEXT, waiting_note TEXT,
  round INTEGER NOT NULL CHECK (round >= 0), plan_lineage INTEGER NOT NULL,
  parked_from TEXT CHECK (parked_from IS NULL OR parked_from IN (/* ACTIVE */)),
  park_reason TEXT, owner_answered INTEGER NOT NULL CHECK (owner_answered IN (0,1)),
  evidence TEXT NOT NULL CHECK (json_valid(evidence)),
  plan_rounds TEXT NOT NULL CHECK (json_valid(plan_rounds)),
  extra TEXT NOT NULL CHECK (json_valid(extra)),         -- the other slice fields
  text_ok INTEGER NOT NULL DEFAULT 1,                    -- 0 for imported text that fails the rule
  legacy INTEGER NOT NULL DEFAULT 0 CHECK (legacy IN (0,1)),
  legacy_reason TEXT,
  last_moved_at TEXT NOT NULL,
  CHECK ((legacy = 1) = (legacy_reason IS NOT NULL)),
  -- a plan slice never enters a build phase; plan-review rounds may use REVIEW and REMEDIATE
  CHECK (legacy = 1 OR kind <> 'plan' OR phase IN ('FRAME','PLAN','REVIEW','REMEDIATE',
                                                   'DONE','PARKED','HELD')),
  CHECK (legacy = 1 OR (phase IN ('PARKED','HELD')) = (parked_from IS NOT NULL)),
  CHECK (legacy = 1 OR phase NOT IN ('GATE','DOCUMENT') OR kind IN ('plan','duty')
         OR pr IS NOT NULL)
) STRICT;
-- one open slice per PR; legacy rows are reported by I16 instead
CREATE UNIQUE INDEX one_slice_per_pr ON slices(repo, pr)
  WHERE pr IS NOT NULL AND legacy = 0 AND phase NOT IN ('DONE','PARKED','HELD');
CREATE TABLE phase_moves (mode TEXT, kind TEXT, from_phase TEXT, to_phase TEXT,
  PRIMARY KEY (mode, kind, from_phase, to_phase)) STRICT;   -- generated from phases.py
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1),
  mode TEXT NOT NULL CHECK (mode IN ('headless','live')),
  allow_override INTEGER NOT NULL DEFAULT 0,      -- set inside one transaction by --override
  dispatch_seq INTEGER NOT NULL DEFAULT 0) STRICT;
CREATE TRIGGER slice_phase_legal BEFORE UPDATE OF phase ON slices
  WHEN NEW.phase <> OLD.phase AND (SELECT allow_override FROM settings) = 0 AND NOT EXISTS (
    SELECT 1 FROM phase_moves m WHERE m.mode = (SELECT mode FROM settings)
      AND m.kind = NEW.kind AND m.from_phase = OLD.phase AND m.to_phase = NEW.phase)
  BEGIN SELECT RAISE(ABORT, 'illegal phase move'); END;
CREATE TABLE slice_moves (id INTEGER PRIMARY KEY, key TEXT NOT NULL REFERENCES slices(key),
  at TEXT NOT NULL, from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, reason TEXT,
  run_id TEXT REFERENCES runs(run_id), override INTEGER NOT NULL DEFAULT 0) STRICT;
CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  slice_key TEXT, run_id TEXT, fields TEXT NOT NULL CHECK (json_valid(fields)),
  -- once=(field,value) searches prior rows of the same kind under the write lock
  source_line INTEGER UNIQUE) STRICT;                       -- line number in events.jsonl
-- events and slice_moves get the same append-only triggers as run_log
CREATE TABLE questions (key TEXT PRIMARY KEY, slice_key TEXT,  -- a copy of the vault page
  kind TEXT NOT NULL CHECK (kind IN ('wait','default')),
  status TEXT NOT NULL CHECK (status IN ('open','answered','defaulted','withdrawn')),
  question TEXT NOT NULL, context TEXT,
  page_record TEXT NOT NULL CHECK (json_valid(page_record)), -- full parsed record, including decision receipt
  options TEXT NOT NULL CHECK (json_valid(options)), recommended TEXT, answer TEXT,
  applied TEXT, asked_at TEXT, answered_at TEXT, page_sha TEXT NOT NULL,
  CHECK (status <> 'answered' OR answer IS NOT NULL),
  CHECK (status <> 'defaulted' OR applied IS NOT NULL)) STRICT;
CREATE TABLE problems (key TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('agent','invariant','lease','import')),
  open INTEGER NOT NULL CHECK (open IN (0,1)), impact TEXT NOT NULL, fix TEXT NOT NULL,
  detail TEXT, closed_at TEXT, extra TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(extra)),
  count INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)
  STRICT;
CREATE TABLE lessons (key TEXT PRIMARY KEY, area TEXT NOT NULL, wrong TEXT NOT NULL,
  instead TEXT NOT NULL, at TEXT NOT NULL, extra TEXT NOT NULL CHECK (json_valid(extra)))
  STRICT;
CREATE TABLE merges (key TEXT PRIMARY KEY, at TEXT NOT NULL,          -- merges.json
  entry TEXT NOT NULL CHECK (json_valid(entry))) STRICT;
CREATE TABLE closures (row TEXT PRIMARY KEY, closed INTEGER NOT NULL CHECK (closed IN (0,1)),
  entry TEXT NOT NULL CHECK (json_valid(entry))) STRICT;            -- closures.json
CREATE TABLE alerts (reason TEXT PRIMARY KEY, last_at TEXT NOT NULL) STRICT;
CREATE TABLE import_raw (id INTEGER PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL,
  position INTEGER NOT NULL, payload BLOB NOT NULL, reason TEXT NOT NULL,
  UNIQUE(source, name, position)) STRICT;
CREATE TABLE import_log (id INTEGER PRIMARY KEY, source TEXT NOT NULL, sha TEXT NOT NULL,
  rows INTEGER NOT NULL, legacy INTEGER NOT NULL, raw INTEGER NOT NULL, at TEXT NOT NULL)
  STRICT;
```

Notes:

- **Legacy rows.** Rows imported from the history that break a rule (a plan slice in
  VERIFY, a reviewer run with no verdict) are stored with `legacy=1` and a
  `legacy_reason`. The relaxed CHECKs accept them, and I17 (section 7) reports each one
  until repaired. Only the importer may mark a row legacy. A normal insert uses 0.
  On update, db.write reads the old row inside the write transaction. A normal edit
  can retain 1 and its reason while a recorded violation remains. Check the proposed
  row against the normal constraints, including other open PR holders even if flagged. When it passes, clear
  the flag and reason in that same update. Refuse a new violation or a 0 to 1 change.
  Include S2's legal repair moves in the generated phase_moves table. A field-only
  edit does not run the move trigger.
- `waiting_on` today is free text. It becomes `waiting_kind`, `waiting_ref` and
  `waiting_note`, so case 3 becomes a join. The import maps old text to
  `waiting_kind='external'`, with the text in `waiting_note`. I4 keeps its legacy
  prefix match for these imported waits after the flip.
- `kind` is new on slices. S2 adds it to the JSON files as `schema_version` 2. The
  default comes from the key (`state/phases.py: kind_for_key`): a `--plan` or `-plan`
  suffix gives `plan`, `--docs` gives `docs`, `DUTY-RELEASE` gives `release`, another
  `DUTY-` prefix gives `duty`, and everything else gives `build`. `slice set --kind`
  corrects a wrong guess.
- `evidence` and `plan_rounds` stay JSON columns, checked with `json_valid`.
- A headless run row keeps `run_dir`. Its result file stays on disk. Headless rows also
  link to existing slices by request key or batch membership. A standalone duty
  request keeps its key as metadata (section 8.5).

## 5. Agent-facing commands

All commands are `devloop-state <project> ...`, the existing entry point. Agents never
run SQL.

### 5.1 Exit codes (all run, invariant and db commands)

| Code | Meaning | What the caller does |
|---|---|---|
| 0 | Done | Go on |
| 2 | Usage error (argparse) | Fix the command |
| 3 | Refused by a rule (`REFUSED: <reason>` on stderr; the reason names the rule or field and, for a writer conflict, the holding run id) | Fix the record; never work around it |
| 4 | Lease lost: the run was abandoned or ended by the orchestrator, or the token does not match | Stop (see below) |
| 5 | Store unavailable (busy past the timeout, disk full, schema newer than the code) | Retry once after 10 s. A failed start cannot spawn work. An admitted run reports the outage |

Code 3 keeps its current meaning, so existing callers do not change.

A wrapped child can exit 4 or 5. For either code from `run exec`, check the token
with a separate beat. A successful beat leaves the child failure for the caller to
handle. Never rerun the child solely because its exit code matches a store error.
Test both child codes.

**After confirmed lease loss**, the runner must:

1. Stop starting new steps. Do not push, open a PR, merge or move a slice.
2. Leave its worktree and local commits as they are. Do not reset or delete anything.
3. Not run `run end`. The orchestrator already owns the record.
4. Say "lease lost on RUN" in its final message, with what it had done (the last step).

A lapsed lease alone never gives exit 4. A run that was silent and beats again is
revived (section 6). A wrong token or a fenced run gives exit 4.

### 5.2 Commands

| Command | Who | Effect | Refuses (code) |
|---|---|---|---|
| `run start --slice KEY [--slice KEY ...] --role ROLE --kind KIND [--node N] [--lease 20m]` | orchestrator | Inserts a `running` row, one `run_slices` row per slice, and a `started` log row. Prints `RUN=r-YYYYMMDD-NNNN TOKEN=xxxxxxxx`. | unknown slice (3); the kind does not allow a slice's phase in `LIVE_KINDS` (3); a live writer already on a slice (3, include the holding run id even when silent); lease out of range (3); store (5) |
| `run beat RUN --token T` | runner | Sets `last_beat_at` to now and `lease_until` to now plus `lease_s`. A silent run (by `runs_now`) goes back to `running` with a `revived` log row. | ended or abandoned (4); wrong token (4) |
| `run log RUN --token T STEP [--note TEXT]` | runner | Appends a log row, then beats. | unknown step (2); the note fails the text rule (3); lease (4) |
| `run hold RUN --token T --for 90m --why STEP` | runner | One lease extension for a known long local step, capped at `deadline_at`. Logs `lease-held`. | past the deadline (3); lease (4) |
| `run wait RUN --token T --on KIND:REF --for 3h` | runner or orchestrator | Declares a wait that cannot beat (section 6). Sets `state=waiting`, `wait_on`, and `lease_until` to now plus `--for`, capped at the deadline. Logs `waiting`. The next `beat` or `log` sets `running` and logs `resumed`. | `KIND` not in `WAIT_KINDS` (2); past the deadline (3); lease (4) |
| `run exec RUN --token T -- CMD ...` | runner or orchestrator | Runs `CMD` as a child in its own process group. Beats every 60 s while the child lives. Passes the child's exit code and its stdout and stderr through unchanged. | the first beat as for `beat` (4); after that, see "exec on lease loss" |
| `run end RUN --token T --status done\|blocked\|failed [--verdict V] [--head SHA] [--pr N] [--passed N --failed N] --summary TEXT [--as orchestrator]` | runner or orchestrator | Reads the row, checks the token and state, then sets the end fields and logs `ended`, in one `BEGIN IMMEDIATE` transaction. The same end sent twice is a no-op that exits 0. | reviewer or tester `done` with no verdict (3); head not hex (3); text rule (3); a different end on an ended run (4); wrong token (4) |
| `run abandon RUN --reason TEXT` | orchestrator | Sets `state=abandoned` and `ended_by=orchestrator`. Later beats get code 4. For a run with a `node` other than devbox, prints the node's cancel hint (memory: a local `vm-run` kill leaves the remote run). | already ended (3) |
| `run show RUN [--json]` | anyone | One line, or public run fields (including `state_now`) and typed log steps as JSON | unknown run (3) |
| `run list [--live] [--silent] [--slice KEY] [--json]` | anyone | Rows from `runs_now` | none |
| `run watch [--timeout 30m]` | orchestrator | Blocks. Prints one line when a run goes silent, ends or passes its deadline, then exits 0. Exits 1 on timeout. Made for the Monitor tool. | none |
| `run sweep` | tick | Records the silent state for runs that `runs_now` shows as silent: sets `state=silent`, logs `silent`, opens `lease:<run>` problems, prints `SILENT ...` lines | none |
| `invariants [--json] [--no-github]` | tick, orchestrator | Runs section 7, prints `FAIL <name> <subject>` lines, upserts problems | exits 3 when any check fails |
| `db status`, `db import --dry-run\|--apply`, `db export DIR`, `db reconcile`, `db backup`, `db downgrade --to N` | owner, tick (phase 2) | Section 8 | |

**`--head` check.** `run end --head` checks only the hex shape. Then it tries
`git cat-file -e` in the slice's repo, after one `git fetch origin <branch>` with a 10 s
limit. Check for a commit object, including after an unavailable fetch. If found, `head_verified=1`. If not, the head is stored with
`head_verified=0` and the command still exits 0, because a head pushed from the Mac or
the Dell is often not on the devbox yet. I7 compares heads against GitHub, so an
unverified head is still checked.

**`exec` on lease loss.** When a background beat gets code 4, `exec` records a fixed local lease-loss diagnostic,
sends SIGTERM to the child's process group, waits 30 s, sends SIGKILL if the group is
still alive, and exits 4. It kills only local processes. A job the child started on a
remote node is the orchestrator's cleanup, through the cancel hint that `run abandon`
prints. When a beat gets code 5, `exec` keeps the child running and tries again at the
next interval.

**Live kinds** (`state/phases.py: LIVE_KINDS`, separate from the headless tables):

| Live kind | Role | Deadline | Allowed slice phases |
|---|---|---|---|
| `planner` | planner | 2 h | FRAME, PLAN, REVIEW, REMEDIATE (plan slices) |
| `builder` | builder | 4 h | DEVELOP, SELF_CHECK, REMEDIATE |
| `slice-runner` | builder (writer=1) | 6 h | DEVELOP, SELF_CHECK, VERIFY, REMEDIATE, GATE |
| `dennis` | reviewer | 1.5 h | PLAN, REVIEW |
| `codex-low`, `codex` | reviewer | 1 h | PLAN, REVIEW, GATE |
| `codex-fix` | planner | 1 h | PLAN, REMEDIATE |
| `t1`, `full` | tester | 2 h, 4 h | VERIFY, REVIEW, GATE |
| `tech-writer` | tech-writer | 1 h | DOCUMENT |
| `duty` | duty | 1 h | any |

At `run start`, validate the role against the live kind, derive its writer flag and
check the current phase of each covered slice. Later moves, beats and ends do not
repeat this admission check. Use `--role builder --kind slice-runner` for the default
runner in ORCHESTRATOR.md.

`runs.KINDS` and `runs.KIND_PHASES` do not change. A test pins both tables, so the
headless dispatcher accepts exactly what it accepts today.

### 5.3 How a subagent learns its run id

A failed start never admits work, including a store error. The orchestrator runs `run start` **before** it spawns the subagent, and pastes the two
printed values into the brief. The Agent tool cannot pass environment variables, so the
brief is the only channel. Because the row exists before the spawn, a subagent that
crashes before its first command still has a row, and it reads as silent when its lease
ends.

For work the orchestrator runs itself (Codex reviews, `vm-run` tests), the orchestrator
is the runner and wraps the command, for example:
`devloop-state decoy run exec RUN --token T -- ~/bin/codex-review ...`.

When the subagent's completion notice arrives, the orchestrator runs `run show RUN`. If
the row is not ended, the orchestrator ends it with `--as orchestrator --status failed
--summary "ended without a report"`.

### 5.4 Text for every builder, reviewer, tester and tech-writer brief

`skills/dev-loop/runner-header.md`, `skills/dev-loop/brief-template.md` and the decoy `LOCAL-BUILDER-BRIEF.md` get this
block, which the orchestrator fills in:

```text
Run record (required):
  RUN=r-20261127-0042  TOKEN=k3v9q2xa  PROJECT=decoy
- After each step, run: devloop-state decoy run log RUN --token TOKEN STEP --note "short plain note"
  Steps: plan-read, worktree-ready, build-started, commit, pushed, pr-opened, test-started,
  test-done, review-started, review-done, docs-written, blocked.
- Wrap any local command that can run longer than 5 minutes (tests, vm-run, builds, codex):
  devloop-state decoy run exec RUN --token TOKEN -- <command>
- Before you wait on something that cannot beat (a detached decoy-vm-t3 run, a Monitor
  wait, CI), declare it: devloop-state decoy run wait RUN --token TOKEN --on t3:<label> --for 3h
- To watch a detached job on the Mac, replace 12345 with its numeric PID:
  devloop-state decoy run exec RUN --token TOKEN -- ssh mac 'while kill -0 12345 2>/dev/null; do sleep 30; done'
- If no step has happened for 10 minutes, run: devloop-state decoy run beat RUN --token TOKEN
- Last action before your final message:
  devloop-state decoy run end RUN --token TOKEN --status done|blocked|failed
    [--verdict GO|GO-WITH-FIXES|NO-GO] [--head SHA] [--pr N] [--passed N --failed N]
    --summary "one plain sentence"
- If exec returns 4 or 5, beat separately to distinguish a child failure.
- Exit 4 from a beat means the run is no longer yours: stop, push nothing, do not run end, and say
  "lease lost" in your final message. Exit 5: retry once, then go on.
- Notes and summaries: plain words. No secrets, data values, file paths or command output.
```

## 6. Leases

- **Default lease:** 20 minutes for every role. That is 2 times the 10-minute idle beat in
  the brief, and 20 times the `exec` beat interval. `run start` sets it from
  `project.json invariants.lease_default_s`. The orchestrator may pass `--lease` from
  5 minutes to 4 hours.
- **Deadline:** the start time plus the `LIVE_KINDS` deadline (section 5.2). After the
  deadline the run stays as it is, and I9 reports it as overdue. The loop never kills a
  subagent by itself.
- **Judged at read time.** Every reader (`run list`, `run show`, STATUS.md, the
  dashboard, the invariants) reads `runs_now`. A run whose `lease_until` has passed
  reads as `silent` from that moment, even if no tick ever runs. `run sweep` only
  records the change in the log and opens the problem. It is bookkeeping, not the
  detector.
- **What extends a lease:** `beat`, `log`, `hold` (one long local step, capped at the
  deadline), and `exec`, which beats every 60 s while its child lives. A 40-minute t1 on
  the Mac under `exec` never looks dead. An `exec` beat proves only that the child is
  alive, not the agent. That is enough, because the agent is blocked on that child.
- **Work that cannot beat** declares a wait with `run wait --on KIND:REF --for DURATION`.
  `WAIT_KINDS` are:
  - `t3:<label>`, a detached `decoy-vm-t3` run whose log is on the VM;
  - `monitor:<name>`, an agent blocked on the Monitor tool;
  - `ci:<pr>`, waiting on GitHub checks;
  - `subagent:<run>`, an orchestrator waiting on another run.
  The run shows as `waiting` with its reason until `lease_until`, then as silent. The
  orchestrator may instead run the watcher under `exec` (for example
  `run exec ... -- ssh mac 'while kill -0 12345 2>/dev/null; do sleep 30; done'`), with the job's numeric PID. The beat lasts as long as the watcher runs.
- **On expiry:** the run reads as silent. The next `run sweep` (inside every tick) logs
  `silent`, opens problem `lease:<run>` with source `lease`, and records the alert, in one
  SQLite transaction after the S6 flip. Before the flip, the problem ledger is JSON. Reconcile its
  open/closed state from the committed run state on each sweep, even after a crash
  between the two writes. Silent is not terminal. A late beat revives the run, logs `revived` and
  closes the problem. Sweep records each silent episode once. End and abandon also
  close an open lease problem. Only `run abandon` or an orchestrator `end` fences a run, and after
  that its beats get code 4.
- **How the orchestrator is told, in live mode:**
  1. `devloop-tick decoy`, which the orchestrator runs at the start of each iteration,
     prints `SILENT run=<id> role=<role> slices=<keys> last_beat=<n>m` lines to stdout.
     Today the live pass prints nothing.
  2. STATUS.md gets a "Runner silent" section after the existing sections, read from `runs_now`, so it is
     right the moment it is rendered.
  3. The dashboard shows silent runs under "Needs you", read from `runs_now` on each
     request.
  4. Optionally, the orchestrator keeps `run watch` running under the Monitor tool, so
     a silent run interrupts it between ticks.

  Slack gets nothing in live mode, because the owner asked for the orchestrator to be
  alerted. In headless mode the tick's `_alert` path posts once per run, with its
  existing six-hour dedupe.

## 7. Invariants

Each invariant is one entry in `state/invariants.py: INVARIANTS`: a name, a pure check
function over a `Snapshot`, the probes it needs, and the slice that turns it on. Probes
(`state/probes.py`) do the git and `gh` calls and fill the snapshot. A failure upserts
problem `inv:<name>:<subject>` with source `invariant`. A pass closes it. The tick prints
each failure as `FAIL <name> <subject>`, STATUS.md lists the failures, and the first
sighting of a new failure alerts once.

| ID | Name | Catches | Data | GitHub | Slice | Hits on 2026-11-27 |
|---|---|---|---|---|---|---|
| I1 | `plan-in-build-phase` | a plan slice in DEVELOP, SELF_CHECK, VERIFY, GATE or DOCUMENT | slices | no | S1 | 3 now (VERIFY); 9 in history |
| I2 | `document-without-shipped-entry` | DOCUMENT or DONE with a merged PR, more than 2 h after the merge, and no `<repo label> #<pr>` line in `shipped_doc` on the goal repo's `origin/main` | slices, `git show` | no | S1 | #817, #791, #832 |
| I3 | `claim-without-run` | `next_action` names a role "(subagent)", no open run covers the slice in `run_slices`, and the claim is older than the default lease | slices, runs | no | S3b | the five 18:37 tech-writer slices |
| I4 | `waiting-on-answered-question` | `waiting_kind='owner-question'` and the question is answered or defaulted. For legacy free-text waits (including imported waiting_note): `waiting_on` or `next_action` says "owner" or "question" and names a key that is the prefix of an answered question | slices, questions | no | S1 | NONFK-OOM--C1-docs |
| I5 | `gate-branch-lacks-main` | GATE, or REVIEW with a GO, where `origin/<base>` is not an ancestor of `origin/<branch>` (`git merge-base --is-ancestor`) | slices, local git after a fetch | no | S1 | DATE-SHIFT-TYPED--S1b-1 |
| I6 | `pr-state-mismatch` | a slice before DOCUMENT whose PR is merged or closed, or a slice in DOCUMENT or DONE whose PR is still open | slices, GitHub | yes | S1 | not checked yet |
| I7 | `head-mismatch` | the slice or last run `head` differs from the PR's `headRefOid` | slices, runs, GitHub | yes | S1 | not checked yet |
| I8 | `runner-silent` | `runs_now.state_now = 'silent'` | runs | no | S3b | starts with S3b |
| I9 | `runner-overdue` | not ended and past `deadline_at` | runs | no | S3b | starts with S3b |
| I10 | `illegal-move` | a `slice-moved` event since the last tick outside the move table for its mode and kind, or a move recorded with `override` | events | no | S1 | 100 of 722 |
| I11 | `held-without-question` | HELD, or PARKED for an owner reason, with no open `hold:<key>` question; or a hold that was answered but is still held after one tick | slices, questions | no | S1 | not checked yet |
| I12 | `stale-slice` | an active slice that is not waiting, with no move, no run and no log row for 12 h | slices, runs | no | S1 | not checked yet |
| I13 | `ended-run-not-applied` | a live run ended `done` more than 30 minutes ago, and a slice it covers is still in the phase the run started in | runs, slices | no | S3b | starts with S3b |
| I14 | `required-check-red` | a slice in VERIFY, REVIEW or GATE with a PR, whose head has a red required check, or whose merge state is BLOCKED with any red check | slices, GitHub | yes | S1 | #789 and #809 |
| I15 | `orphaned-headless-run` | a run directory with `exit` and none of `applied`, `rejected`, `released`, `discarded` while the mode is live; or a slice whose `run` field names such a run | run dirs, slices | no | S1 | 4: ENGINE-SELECT--S1, EXPECT-OFF-ROUTE--S2, WHEN-READS-SOURCE--S2, 24zj--E-S1a |
| I16 | `pr-shared` | two slices not in DONE, PARKED or HELD with the same repo and PR | slices | no | S1 | #828, #829 |
| I17 | `legacy-row` | a row imported with `legacy=1` that is still flagged | slices, runs | no | S5 | 3 plan slices in VERIFY, plus the #828 and #829 pairs |

New move events carry mode, kind and the override flag. For historical replay use
recorded mode when available. Mark ambiguous cases unchecked. An override changes
only the move-table check. Preserve owner authorization, held-slice release checks,
row constraints and headless evidence checks.

**I14 in detail.** For PR N, read `mergeStateStatus` and the current head's rollup.
On each CheckRun request `name`, `conclusion` and `isRequired(pullRequestNumber: N)`.
On each StatusContext request `context`, `state` and `isRequired(pullRequestNumber: N)`.
Pass each alias its own integer PR number. A check is required if GitHub returns true
or its name matches an entry in `project.json merge.repos.<repo>.required_checks` or
the repo's `gate_status`. Absent config contributes no names. FAILURE, ERROR, CANCELLED,
TIMED_OUT and ACTION_REQUIRED are red. A red required check fires I14. BLOCKED with
any red check also fires. The slice phase alone determines when I14 applies.

**How often:** every tick. In live mode that is every orchestrator iteration, because
`devloop-tick` already runs first. In headless mode it is the cron tick.
`devloop-state decoy invariants` runs them on demand. Run silence does not wait for a
tick (section 6).

**Cost:**

- Local checks: reading about 77 slice files, about 2,600 events and the `exit` and
  `applied` names in 527 run directories takes under 1 s.
- I2 and I5 need one `git fetch` per repo per tick, 1 to 3 s each. A fetch younger than
  5 minutes is reused (stamped in `tick-state.json`, and per repo in `repos` after phase 2).
  After the fetch, `git show` and `merge-base` take about 20 ms per slice.
- I6, I7 and I14 start with one GraphQL call per repo per tick. It uses one alias per distinct repo PR number in the snapshot, including closed or merged PRs
  for I6 (today 18 GATE PRs plus the VERIFY and REVIEW ones, about 30 in all), each
  with `state merged headRefOid baseRefName mergeStateStatus` and the check rollup above. With 50 contexts
  per PR, one call costs about 1 to 2 points of the 5,000-point hourly budget. Results
  are cached for up to 10 minutes. If the
  GraphQL call fails, the fallback is one `gh pr view N --json statusCheckRollup,...`
  per PR (about 30 calls), at most once per 10 minutes. On GraphQL, request pageInfo
  and follow cursors on the same head. A known failure still fires. If a context page, required flag or
  merge state needed to decide I14 is missing, report `unchecked`. The per-PR CLI
  rollup alone cannot prove a red unlisted check optional. Keep prior open findings on
  an unchecked result. Cache by PR and head. With `--no-github`, or when `gh`
  fails both ways, I6, I7 and I14 report `unchecked`, never `pass`.
- A check that raises an error is reported as `FAIL invariant-error <name>`, and the
  other checks still run.

## 8. Concurrency, crash safety and migration

### 8.1 Concurrency

- **Writers:** up to 16 runs, the orchestrator, the tick and an operator shell. Each
  command opens its own connection with `isolation_level=None`, then sets
  `journal_mode=WAL` (persistent), `busy_timeout=5000`, `foreign_keys=ON` and
  `synchronous=FULL`. Check strict Python types before binding values because SQLite STRICT can coerce numeric strings and booleans. Every write is one `BEGIN IMMEDIATE ... COMMIT` inside
  `db.write()`, including writes that read first (`run end`, `run beat` on a silent run,
  `slice move`). Each transaction holds the lock for well under 10 ms. At about 1 write
  per second at peak, waits are rare and short.
- **Readers:** the dashboard and STATUS.md open the file with a `file:...?mode=ro` URI,
  so a write through a reader fails. They read in one short transaction per request and
  never keep a read open while a page builds. This prevents checkpoint starvation. The
  dashboard server opens one connection per thread.
- **Checkpoints:** the auto-checkpoint stays at its default. The tick runs
  `PRAGMA wal_checkpoint(TRUNCATE)` when the WAL is over 8 MB.
- **Remote nodes:** WAL needs all writers on one host, and all writers run on the devbox.
  Mac and Dell jobs start from the devbox through `vm-run`, under `run exec` on the
  devbox. A future remote worker reports with `ssh devbox devloop-state ...`.

### 8.2 State files: import, keep or archive

Measured on 2026-11-27: 21 top-level lock files, 78 slice locks, and 1,969 lock files
inside run directories.

| File | Writer today | Phase 2 verdict | Lock after S7 |
|---|---|---|---|
| `slices/*.json` (77) | `slices.py` | import to `slices`, `slice_moves` | 78 slice locks archived |
| `events.jsonl` | `store.append_event` | import to `events` | archived |
| `problems.json` | `notes.problem` | import to `problems` | archived |
| `lessons.json` | `notes.lesson` | import to `lessons` | archived |
| `alerts.json` | `tick._alert` | import to `alerts` | archived |
| `merges.json` (merge ledger) | `board.py` | import to `merges` | archived |
| `closures.json` (row closures; absent today) | `board.py` | import to `closures` when present | archived |
| `seq.json` | `store.next_seq` | import to `settings.dispatch_seq`; keep the live `run_seq` independent | archived |
| `runs.index.json` | `store.register_request` | import to `runs.request_sha` | archived |
| `runs/<id>/` (527 dirs, incl. `repair.json`) | wrapper, `apply.py` | keep on disk; index as `runs` rows | 1,969 run locks stay |
| `project.json` | the owner | keep (config) | stays |
| `node-status.json` | node-watch (outside the plugin) | keep (external producer) | stays |
| `tick-state.json` | `tick.py` | keep (tick runtime); fetch stamps move to `repos` | stays |
| `dash.json`, `dash-port.json` | dashboard | keep (dashboard runtime) | stays |
| `STATUS.md` | `status.write_status` | keep (output) | stays |
| `PAUSE`, `cap-until` | operator, tick | keep (flags) | none |
| `tick.lock`, `dash.lock`, `admit.lock` | tick, dashboard, dispatcher | keep | stay |
| vault `OWNER-QUESTIONS.md` | the owner, `notes.ask` | keep as the source of truth; copied to `questions` | its page lock stays |

S7 archives exactly the rows marked "archived" and their lock files, and nothing else.
`devloop-state check` warns when an archived file appears again.

### 8.3 Questions: one source of truth

The vault page is the source of truth for questions and answers, before and after the
flip. The owner answers there, and the owner's workflow does not change. The
`questions` table is a copy:

- Keep the vault parser `notes.load_questions(path)` and its four-part return value.
  A defaulted question has the recommended option in applied and can have a null
  answer (notes.py:410). Preserve that distinction in the copy and its tests.
- `notes.ask` writes the page first, then the copy, under the page lock.
- `answers-sync` (each tick, and at the start of any command that reads questions)
  keeps the existing receipt and interactive authorization rules. Only an authorized
  writer applies an answer. Refresh the parsed page snapshot in one DB transaction.
  Read-only dashboard requests never apply an answer or open a write connection.
  If the snapshot is stale, read the authoritative page or report unchecked.
- Readers (I4, I11, the dashboard) read the copy after that sync. If the page cannot be
  read, they report `unchecked`.
- Nothing writes an answer into the table directly. A test changes an answer in the
  table and checks that a reconcile detects the mismatch and a refresh restores the page record.

### 8.4 Crash safety

- A command killed during a write leaves no partial row (SQLite atomic commit). A runner
  killed during its work stops beating and reads as silent when its lease ends.
- A reboot leaves the `-wal` and `-shm` files. The next connection recovers. The state
  directory is never copied or cleaned without its `-wal` file.
- `db backup` uses `sqlite3.Connection.backup` to write `backups/devloop-YYYYMMDD.db`.
  The tick runs it once a day and keeps 7 copies. `devloop-state check` adds
  `PRAGMA quick_check`.
- Schema newer than the code (after a code rollback past a migration): every command
  using the store refuses before writing. Existing commands return their documented
  refusal code 3. New run and db commands return 5 with "schema newer than this code". The rollback steps in 8.6 cover this.

### 8.5 Migration (phase 2)

Expand, migrate, verify, contract (data-and-migrations rulebook):

1. **Expand (S5).** Migration 2 creates the phase 2 tables. `db import --dry-run` prints
   counts and source names with fixed reason codes. Raw payloads stay private. `db import --apply`
   imports in one transaction per source. **Every record goes in; nothing is skipped:**
   - `slices/*.json`: each file is checked with `state/fields.py`. Known fields go to
     columns, the rest go to `extra`, and the `history` entries go to `slice_moves`. A
     file that breaks a rule (a plan slice in VERIFY, a shared PR) is imported with
     `legacy=1` and its reason, and gets an `import-legacy:<key>` problem. A file that
     does not parse as JSON goes to `import_raw` with its text, and gets an
     `import-raw:<name>` problem.
   - `events.jsonl`: every line goes to `events`, with the raw object in `fields` and the
     line number in `source_line`. A line that does not parse goes to `import_raw`.
   - `runs/*`: every directory becomes a `runs` row with `origin='headless'`, its own id,
     `run_dir` set, and applicable `run_slices` rows from `request.json`. The state comes from the
     `exit`, `applied`, `rejected`, `released` and `discarded` files; a run that has none
     is checked for liveness as described below. Preserve a representable violation as legacy.
   - `problems.json`, `lessons.json`, `alerts.json`, `merges.json`, `closures.json`,
     `seq.json` and `runs.index.json` go to their tables (8.2). `OWNER-QUESTIONS.md` goes
     to `questions` through `notes.load_questions`.
   - Stage source identities before enforcing foreign keys. Populate slices first,
     then rebuild phase 1 run_slices with the FK in a final transaction. Restore its
     indexes and close trigger before releasing writes. Check foreign_key_check.
     If an existing live link has no valid slice parent, leave that link and its
     phase 1 schema intact, report the key and defer finalization until repaired.
     For a duty request without a slice (dispatch.py:294), keep the request key as
     metadata without creating a slice link. For batch requests use the keys of
     request.batch. For a no-exit run, check wrapper/payload liveness under the
     admission lock before recording lost. Preserve runs.iter_runs and holds_slot
     behavior, including writers awaiting apply. Never feed live rows to collect.
   - When a record cannot satisfy even the relaxed schema, preserve its original
     bytes in import_raw (BLOB), keyed by source and record position. This includes
     invalid UTF-8, missing parents and unknown types. Emit a fixed reason code.
     Test interrupted re-import with malformed bytes and existing S3b links intact.
   - Each source's sha256 and its row, legacy and raw counts go to `import_log`.

   For each shadow refresh, read a consistent source under its existing lock. A
   snapshot can advance several legal moves between imports. Apply it as an import
   after validation. Preserve the ordered history, including repeated identical
   entries, and record each imported entry once. The import is idempotent. Events key on `source_line`, slices on the key plus the
   canonical digest, and runs on the run id. A second run adds zero rows.
2. **Shadow (S5, at least 2 days of live use).** JSON stays the source of truth. Each tick
   runs an incremental import and then `db reconcile`. The reconcile compares per-slice
   canonical digests, ordered events, full ledger and question records, counters and
   headless request hashes against the source. Compare live runs separately. Preserve
   the original line position and bytes for raw records on export. A difference
   opens problem `inv:db-drift:<subject>`. The flip needs 2 days in a row with zero drift.
3. **Flip (S6).** Quiesce state writers, finish the last import, verify parity and switch
   `project.json "store": "sqlite"` before releasing writers. The read contracts
   (`slices.load_all`, `status.events`, `runs.iter_runs`, `notes.load_questions`) and
   the owners of the files in 8.2 keep their public signatures. Route single-slice
   reads and edits (slices.py:266, 286), counters, request hashes and ledger access
   through the selected backend. Replace direct problem reads in dash/view.py:1004
   and 1033, and the problem and lesson reads in status.py:144 and 153. Persist
   closed_at and other existing ledger fields for the dashboard's recent history.
   Keep scanning headless request, process and exit files. Refresh their index on new
   dispatch and state changes, including after S7. Test dispatch, collect and apply
   with the archived JSON files absent.

   **Seven-day mirror.** Serialize migrated writes and backend switches under one project
   lock using store.locked. Recheck the backend after taking the lock. Before committing
   SQL, durably mark the mirror dirty on disk. Commit slice, move, event and ledger
   changes for one logical operation together. Publish the affected private JSON files
   and events in commit order. Clear the marker only after all files and directory
   entries are durable. Report success after publication. After interruption or a mirror
   error, the next writer or rollback first rebuilds the dirty mirror from a consistent
   SQLite snapshot, including exact event order, without duplicate appends. A clean
   marker permits rollback with an unreadable database. If dirty and the database is
   unreadable, stop for recovery. Never activate a stale mirror.
4. **Contract (S7).** After 7 days with no rollback, the mirror writes stop. The files
   marked "archived" in 8.2 and their locks move (they are not deleted) to
   `archive-YYYYMMDD/` in the state directory. Deleting the archive later is a separate
   R3 step that needs the owner's authorization, and it is not in this plan.

### 8.6 Rollback

- **Before the flip:** JSON remains authoritative for migrated state. Keep the live-run
  database. Before rolling back S3b, stop writers and take a verified private backup.
- **Within seven days of the flip:** under the project lock, check the mirror marker,
  recover if necessary, then switch to JSON. A corrupt run database still requires
  recovery for live-run commands. Keep it and its WAL for recovery.
- **Code rollback past a migration:** stop writers, back up and verify the current
  database before a downgrade. Preserve live-run history and restore the prior schema,
  including the phase 1 run_slices table and its indexes.
- **After S7:** export current SQLite data under the project lock and verify it before
  switching back. The archive alone cannot recover writes made after S7.

### 8.7 Keeping old commands working

Every existing `devloop-state` command keeps its name, arguments, output and exit codes.
A parity test runs the state CLI tests against both backends through a `store_backend`
fixture, including `append_event(once=...)`. The dashboard's own tests also run against
both backends. `devloop-state event KIND k=v` stays, and its values go through the text
rule.

## 9. Value-free rule

**Rule:** no secrets, data values or long paths in stored text. It is enforced where the
text enters (the command boundary), and again at the database write.

- **Choke point:** `state/text.py` holds one registry,
  `COLUMNS = {(table, column): TextRule(limit, kind)}`, with the kinds `note`, `summary`,
  `reason`, `token`, `path`, `ref` and `json`. Two places use it:
  1. Parse command syntax first. Validate free-text fields with `text.arg(table, column)`
     under the CLI refusal handler (exit 3 with the field name). An argparse type
     failure normally exits 2 and may echo the input. Use fixed usage messages on
     the new command parsers, including malformed integers, choices and extra args.
  2. `db.write(table, row)` runs the same rule on every TEXT column, so a code path that
     skips the CLI still cannot store bad text.
- **What is refused (kinds `note`, `summary`, `reason`):** text over the limit (note 120,
  summary 200, reason 300, next_action 300); newlines and control characters; the secret
  shapes in `scrub.py` (`_TOKEN`, `_URL_CRED`, `_LONG`); absolute or home paths with
  three or more parts (`_PATH`); email addresses; runs of 9 or more digits; quoted spans
  over 20 characters. The text is refused, not scrubbed, so the record stays true and
  the agent learns the rule.
- **Kind `ref`.** Parse run wait_on as KIND:REF. Check KIND against WAIT_KINDS and
  fullmatch the reference with store.TOKEN. Slice waiting_ref holds just the reference
  because waiting_kind already holds the kind.
- **Kind `path`** (`worktree`, `plan_path`, `run_dir`): the value must resolve under an
  allowed root (the repos in `project.json`, the state directory, the vault). Path
  values are never shown on public pages.
- **Imported history:** old text is stored as it was, because an import must not change
  the record. Preserve raw input in private storage. Use fixed words, counts and typed IDs for
  public run output. Allowlist fields before serialization. Omit arbitrary notes, summaries, wait text and raw JSON even
  if they pass pattern checks. A short data value can look like an ordinary word.
  Test a short marker as well as known secret shapes across renderers and refusals.
- **Guard test (fitness function):** it walks the built argparse tree of `devloop-state`
  and fails on any `str` free-text input without a registered validator. It also walks the reflected
  schema (`PRAGMA table_info` for every table and view) and fails on any TEXT column
  missing from `COLUMNS`. **Mutation:** remove the text validator from
  `run end --summary`; the guard must go red. Adding a TEXT column to `runs` without a
  registry entry must also turn it red.

## 10. Slices, branches, acceptance tests and mutations

### 10.1 Branch rules (owner, 2026-11-27)

- Every slice branches from the plugin's `origin/main` and merges back to `main` when it
  is done. Nothing stays on a side branch.
- One slice is open at a time, in order. S1 defines the pure move and kind rules used by I1 and I10. S2 installs those
  same rules on the write path. A later slice branches from `main` after the earlier one merges.
- Merges go through PRs, under the standing merge authority (double gate GO for R2).
  `devloop-install` from `main` happens only after slice 0 is done (section 10.2).
- Commits use Conventional Commits with the final line `cam`. T0 runs per edit, T1 per
  commit and on the slice head, T2 at each review or gate. A full plugin suite runs on
  the merge head for S0 (merge of long branches, test config), S3a (schema), S5
  (migration) and S6 (serialization). Tests run through `~/bin/pytest-one`.
- Close-out per slice (local operations profile): the plugin has no CHANGELOG or shipped
  log, so each slice updates the README command docs and this plan's status line before
  the merge. The plugin is not a standing graphify corpus, so there is no graph refresh.
  The section-complete Slack message goes out after each merge.

### 10.2 Slice 0: land the install source and the dashboard on main

**What lands, re-derived from the real install tree.** The installed `devloop/` equals
`origin/feat/push-gate-s1` at `a753f7f` (checked with `diff -r` against `git archive`:
0 differences). That branch already contains `feat/docs-skip-qa` (the PR-create guard),
`feat/t1-batch` and the restore-slice-runner docs, and it has `devloop-state merged`.
The dashboard runs from `feat/dash-mission-control`. So slice 0 lands exactly two
branches: **push-gate-s1** and **dash-mc**.

**What does not land: `feat/full-suite-batches`.** Its `batch.py` lets a full run test a
pair of slices. The installed `batch.py` (from t1-batch) does not do that. Landing it
would change live behavior, so it is left out of slice 0. It is not part of RUN-STATUS.
It can land later as its own slice, with its own plan review. The owner rule ("nothing
stays on a side branch") is about RUN-STATUS slices. The loop's branch list records the
open branch.

**Steps:**

- **S0a. Re-sync the rules.** Branch from `origin/main` in a fresh worktree. Read the
  five local-main commits and re-sync `rules/development-loop.md` from
  `/home/cam/dev-rules/development-loop.md`. Check that the result includes their
  decisions and main's rules-gate requirements. Land the sync by PR after the suite.
- **S0b. Land push-gate-s1.** Start the integration branch at the install source
  `a753f7f`. Merge the new main into it. Resolve the two trial-merge conflicts in
  `config.json` (keep both sets of keys) and `tests/test_rules_gate.py` (keep both
  sets of tests). Read the auto-merged live hook, `hooks/rules_gate.py`, for meaning.
  Run the full suite on the merge head and land by PR with a merge commit.
- **S0c. Land dash-mc.** Review and push its nine local commits. Merge the new main
  into dash-mc in a fresh worktree. The trial merge conflicts only in
  `skills/dev-loop/SKILL.md`. Preserve the runner procedure and dashboard instructions.
  Check the auto-merged hook, project, run and CLI code, playbook, tick instructions
  and mutations script. Run the full suite and the dashboard against a read-only copy
  of the state. Land by PR with a merge commit.
- **Done when:**
  1. Fetch origin successfully in the plugin repo, then check that
     `git merge-base --is-ancestor a753f7f origin/main` and
     `git merge-base --is-ancestor <dash-mc head> origin/main` both succeed.
  2. The reviewed adds list (below) is in the slice-0 PR description.
  3. Reinstall from main. `devloop-install` writes the source sha to a `SOURCE` file in
     the version directory before activation. The check fetches origin in the plugin
     repo first. If the fetch, source SHA or ref is unavailable, report `unchecked`.
     Test a stale tracking ref with a failed fetch. Only a fresh ancestor result
     satisfies slice 0. Then the next tick and
     `devloop-state decoy check` pass, and `devloop-state decoy merged --help` works.

**The reviewed adds list.** For each landed branch, the PR lists what it adds over the
install, by commit subject and file:

- dash-mc's 44 commits that are not in push-gate-s1, which are mostly `devloop/dash/`
  plus `canonical_digest`, `append_event(once=, at=)` and `set_paused` in `store.py`;
- main's rules-gate commit;
- the merge resolutions.

Each item is marked "expected" or "needs a look". An item marked "needs a look" is
reviewed before the merge.

**Why keep history:** `feat/full-suite-batches` and `feat/dash-mission-control` share
the 131 headless-loop commits with push-gate-s1. A squash gives main one new commit with
none of those commits as parents, so each later branch would conflict over about 34,600
lines. Merge commits let them land cleanly. Old subjects that do not follow
Conventional Commits are history and are not rewritten. The new merge commits follow the
rule.

**Review scope (owner decision, 2026-11-27):** the merge resolutions, the unpushed
commits (the 5 rules-sync commits and dash-mc's 9), a full suite on each merge head, and
the ancestor check plus the reviewed adds list. The reviewer is Codex.

**Size, re-estimated:** about 290 lines of conflict resolution in 3 regions; two semantic
reads of the live `hooks/rules_gate.py`; one rules re-sync covering five local commits; 3 PRs and 3 full-suite
runs; and the reviewed adds list over 44 dash-mc commits. About one working day.

### 10.3 Build slices

| Slice | Phase | Risk | Size (source + tests) | Depends on |
|---|---|---|---|---|
| S1 Read-only invariants on today's store | 1 | R1 | 380 + 380 | S0 |
| S2 Live move table, slice kind, phases and fields moved out of `slices.py` | 1 | R2 | 220 + 260 | S1 |
| S3a Storage: db, schema, text registry, guards | 1 | R2 | 320 + 320 | S1, S2 |
| S3b Run commands, leases, waits, sweep, run invariants | 1 | R2 | 480 + 420 | S3a |
| S4 Briefs, skill, STATUS.md and dashboard readers for runs | 1 | R1 | 250 + 150 | S3b |
| S5 Phase 2 schema, import, export, shadow reconcile | 2 | R2 | 650 + 550 | S4, and 2 days of S3b data |
| S6 Flip to SQLite, write-through mirror | 2 | R2 | 550 + 450 | S5, with 2 clean shadow days |
| S7 Contract: stop the mirror, archive the listed files | 2 | R2 | 80 + 80 | S6, plus 7 days |

#### S1. Read-only invariants (R1)

**Behavior:** `state/invariants.py` with I1, I2, I4 to I7, I10 to I12 and I14 to I16
(I3, I8, I9 and I13 need run rows and come in S3b; I17 comes in S5); `state/probes.py`
(fetch, `git show`, `merge-base`, the GraphQL call with its fallback, and caching); the
command `devloop-state <p> invariants`; a tick hook (`tick._pass` runs the checks after
`collect`, in every mode, live included); a STATUS.md section "Invariants"; `FAIL` lines
on the tick's stdout; problems with `inv:` keys; the contract snapshot tests (3.5); and
`docs/CODEMAP.md`.

**Failure modes:** `gh` is missing or fails, so I6, I7 and I14 report `unchecked`, never
pass. A fetch fails, so I2 and I5 report `unchecked`. A check raises an error: it
reports `invariant-error`, and the other checks still run. The questions page is
missing: I4 and I11 report `unchecked`. Without configured names, I14 still checks GitHub requirements and BLOCKED.

**Acceptance tests:**

- `test_inv_plan_in_build_phase_fires` (fixture: an `X--plan` slice in VERIFY)
- `test_inv_document_without_shipped_entry_fires` and
  `test_inv_document_with_shipped_entry_passes` (fixture: a git repo with
  `docs/RECENTLY-SHIPPED.md`)
- `test_inv_waiting_on_answered_question_fires` (the NONFK-OOM text and an answered
  question)
- `test_inv_gate_branch_lacks_main_fires` (a temp repo where the branch is behind main)
- `test_inv_pr_state_mismatch_fires`, `test_inv_head_mismatch_fires`,
  `test_inv_required_check_red_fires` and `test_inv_required_check_red_ignores_optional`
  (fake gh: #809 fires for required compat-preflight, also when isolated from BLOCKED.
  #789 fires on an UNSTABLE PR by configured name. Test gate_status alone too. A red
  unlisted check with isRequired false passes on UNSTABLE and fires on BLOCKED.
  Repeat with CheckRun and StatusContext and vary next_action without changing the result.)
- `test_inv_orphaned_headless_run_fires` (a run directory with `exit` and no `applied`,
  mode live)
- `test_inv_pr_shared_fires` (two open slices with one PR)
- `test_graphql_failure_falls_back_per_pr` and `test_github_failure_reads_unchecked_not_pass`
- `test_check_exception_does_not_stop_others`
- `test_problem_closes_when_invariant_passes`
- `test_every_invariant_has_a_firing_test` (guard: walks `INVARIANTS` and requires a
  `test_inv_<name>_fires` test for each entry that is turned on)
- Add firing fixtures for I10, I11 and I12 now and I17 in S5. Normalize registry
  names from hyphens to underscores for test discovery. For I12, use headless run
  files in S1 and add live run logs in S3b. Compare abbreviated heads with the
  existing merge head comparison for I7.
- `test_live_state_copy_finds_todays_cases` (golden test on a scrubbed copy of the
  2026-11-27 slices, questions and run-directory names, checked into `tests/fixtures/`;
  include synthetic rollups and git refs, fix the clock and replay from a fixed
  event offset. Assert the known findings for enabled checks. The 100 historical
  moves outside the headless table are input for I10, not its expected failure count.)
- `test_contract_snapshots` (3.5)

**Mutations that must go red:** I5 returns no failures; the GitHub error path returns
pass; ignore isRequired; ignore BLOCKED; treat every optional red check as required; I15 treats `rejected` as not
applied; one test named by the registry guard is deleted.

#### S2. Live move table, slice kind, phases out of slices.py (R2)

**Behavior:** `state/phases.py` takes `PHASES`, `ACTIVE`, `TRANSITIONS`, `kind_for_key`
and `LIVE_KINDS`. `state/fields.py` takes `_FIELDS` and `_validate`. `slices.py`
imports them and re-exports the old names. `state/phases.py` defines
`LIVE_MOVES[kind]`. It holds the headless `TRANSITIONS` plus the moves the live process
really uses:

- REMEDIATE to REVIEW, VERIFY to GATE, and REMEDIATE to GATE (re-review after a fix);
- GATE to VERIFY and GATE to REMEDIATE (main moved, test again);
- FRAME to DEVELOP (an R0 or R1 slice with an inline plan);
- for plan slices only: PLAN to REVIEW, REVIEW to REMEDIATE, REMEDIATE to REVIEW, and
  REVIEW to DONE.

Plan slices are refused DEVELOP, SELF_CHECK, VERIFY, GATE and DOCUMENT. A plan slice
that is already in one of those phases may move out to PLAN, REVIEW, DONE or PARKED, so
today's 3 plan slices in VERIFY can be fixed. DOCUMENT to DOCUMENT is refused, because it
records no fact; use `slice set` for that. In live mode, `_move` checks `LIVE_MOVES`
instead of skipping the table. It still skips the run-evidence checks. A refused move
that really happened goes through `slice move --override "reason"`. That records the
move with `override=1` and an event, and I10 reports it. The slice JSON goes to
`schema_version` 2 with `kind`. Version 1 files load with the derived kind.
`slice set --kind` corrects it.

**Failure modes:** the table misses a real move, and the orchestrator is refused. That is
what `--override` is for, and the override stays visible. A version 1 file appears after
the upgrade: it loads with the derived kind. A kind is guessed wrong: `slice set --kind`
fixes it. An old caller imports `slices.PHASES`: the re-export keeps it working.

**Acceptance tests:**

- `test_live_plan_slice_refused_develop` (today's case)
- `test_plan_slice_in_verify_may_leave` (the fix path for today's 3 slices)
- `test_live_moves_cover_observed_history` (replays the 722 moves from the fixture copy;
  each one is accepted, or is on the list of intended refusals)
- `test_override_records_event_and_flag`
- `test_v1_file_loads_with_derived_kind`
- `test_every_pair_outside_table_refused` (enumerates `PHASES x PHASES` for each kind)
- `test_headless_tables_unchanged` (pins `TRANSITIONS`, `runs.KINDS` and
  `runs.KIND_PHASES`)
- `test_slices_reexports_moved_names`

**Mutations:** add DEVELOP to the plan-kind set; put the live bypass back in `_move`;
make `--override` skip the event; change one headless transition.

#### S3a. Storage: db, schema, text registry, guards (R2)

**Behavior:** `state/db.py` (connect, pragmas, `write()`, `read()`, migrations by
`user_version`, and the legacy transition rule in section 4), `state/schema.py`
(migration 1: `migrations`, `runs`, `run_slices`, `run_log`, `run_seq`, `runs_now`),
`state/text.py` (the registry), and the four guards. No command uses the database yet,
except `db status`.

**Failure modes:** the database is locked for more than 5 s (exit 5); the disk is full
(exit 5); the schema is newer than the code (exit 5); a connection without
`foreign_keys=ON` (prevented by having one `connect`).

**Acceptance tests:**

- `test_concurrent_writers_no_busy`: 16 processes, each doing 200 iterations of a
  read-then-write transaction (read the row and check the token, then update it, the same
  path as `run end` and `run beat`), plus inserts, in parallel. Zero exit-5 results, and
  the row counts match. With a deferred `BEGIN`, this read-then-write gets
  `SQLITE_BUSY_SNAPSHOT`, so the mutation below is caught.
- `test_reader_cannot_write` (a write on a `mode=ro` connection raises an error)
- `test_crash_mid_write_leaves_no_partial_row` (a test hook kills a writer with SIGKILL
  inside `BEGIN IMMEDIATE`; after reopening, the row is absent and `quick_check` passes)
- `test_schema_newer_than_code_exit_5`
- `test_runs_now_marks_expired_silent` and `test_timestamp_order_matches_python`
- `test_one_live_writer_per_slice` and `test_batched_run_covers_many_slices`
- `test_headless_id_accepted`, `test_live_id_shape_enforced`
- `test_text_guard_walks_parser_and_schema`, plus one refusal test per text rule,
  parametrized
- `test_no_sqlite_connect_outside_db_module` (AST walk of `devloop/`)
- `test_queries_bind_parameters` (AST walk: no f-string, `%` or `+` building in an
  `execute` call)
- `test_import_direction` (AST walk: no module in `devloop.state` imports `slices`,
  `runs`, `notes`, `tick` or `state_cli`, and rules modules import no storage or adapter
  module)

**Mutations:** use `BEGIN` instead of `BEGIN IMMEDIATE`; remove `foreign_keys=ON`; drop
the `WHERE writer = 1 AND open = 1` index; add `from devloop import slices` to
`state/phases.py`.

#### S3b. Run commands, leases, waits, sweep, run invariants (R2)

**Behavior:** `state/runlog.py` and `state/run_cli.py` (every `run` command in 5.2),
`run sweep` in the tick, the `--head` check with a fetch, `exec` with process-group
cleanup on lease loss, and invariants I3, I8, I9 and I13.

**Failure modes:** the token is wrong (exit 4); a writer already holds a slice (exit 3 with its run id);
a signal kills the `exec` child (its exit code passes through as 128+n, the beater
stops, and the run stays open until `end` or the lease); the beater outlives the parent
(it beats only while the child pid lives, checked by pid and start time as
`runs.proc_start` does); a beat gets exit 4 during `exec` (the group is terminated and
`exec` exits 4); a beat gets exit 5 during `exec` (the child keeps running); the head is
not on the devbox (stored unverified, exit 0).

**Acceptance tests:**

- `test_start_prints_id_and_token`, `test_start_refuses_kind_phase_mismatch`,
  `test_start_refuses_second_writer`, `test_start_many_slices`
- `test_start_slice_runner_in_verify_and_gate` replays scrubbed runner slices
  24p--E2 in VERIFY and DATE-SHIFT-TYPED in GATE. Check the six-hour deadline,
  the writer slot and a refusal naming the silent holder. Move the slice into REVIEW
  during the run and check that the runner can still log and end.
- `test_beat_extends_lease`, `test_wrong_token_exit_4`, `test_abandoned_run_beat_exit_4`
- `test_silent_shows_without_sweep` (lease passes, no tick: `run list` shows silent)
- `test_sweep_logs_silent_and_opens_problem`, `test_late_beat_revives_and_closes_problem`
- `test_wait_declared_not_silent_until_end`, `test_wait_capped_at_deadline`
- `test_exec_beats_while_child_runs` (the child sleeps 3 s with a 1 s beat interval set by
  a test hook; at least 2 beats are recorded), `test_exec_passes_exit_code`
- `test_exec_kills_group_on_lease_loss` (abandon the run while `exec` runs a child that
  starts a grandchild; both are gone within 31 s, and `exec` exits 4)
- `test_end_requires_verdict_for_reviewer`, `test_end_is_idempotent`
- `test_end_unknown_head_stored_unverified`
- `test_inv_claim_without_run_fires` (the 18:37 text and no open run)
- `test_inv_runner_silent_fires`, `test_inv_runner_overdue_fires`,
  `test_inv_ended_run_not_applied_fires`
- `test_live_kinds_have_deadlines` (every `LIVE_KINDS` entry has a deadline and phases)

**Mutations:** make `runs_now` ignore `lease_until`; remove the token check in `beat`;
make `exec` skip the group kill; make `--head` return 3 for an unknown commit; remove
the text type on `--summary`.

#### S4. Briefs, skill, STATUS.md and dashboard (R1)

**Behavior:**

- The block in 5.4 goes into `skills/dev-loop/runner-header.md`, `skills/dev-loop/brief-template.md` and the decoy
  `LOCAL-BUILDER-BRIEF.md`.
- `dev-loop-ui/SKILL.md` gets a "Runs" section: start before you spawn, use `exec` for
  Codex and tests, declare waits, run `run show` when a completion notice arrives, and
  handle silent runs.
- STATUS.md gets the sections "Runner silent" and "Live runs", read from `runs_now`.
- dash `live.py` adds live-run cards from `run list --live --json`, next to the headless
  run cards. dash `view.py` adds silent runs to "Needs you".
- `orchestrator-loop.md` gets one line: act on the `SILENT` and `FAIL` lines the tick
  prints.

**Failure modes:** the database is missing (a project that has not run S3a yet): the
sections say "no run store yet" and the page still renders. The runs table cannot be
read: the existing dash `ctx.bad` path shows "Runs unreadable".

**Acceptance tests:**

- `test_status_lists_silent_runs`
- `test_dash_live_card_from_run_row`
- `test_dash_renders_without_db`
- `test_brief_template_carries_run_block` (both brief paths include the run id, token and reporting commands)
- `test_skill_mentions_every_runner_command` (guard: every `run` subcommand that an agent
  uses appears in SKILL.md and in the brief)
- One live slice end to end before S5 starts: start, log, exec, wait, end, recorded in
  the slice's review evidence.

**Mutations:** remove the silent section from the STATUS render; remove `exec` from the
brief template.

#### S5. Phase 2 schema, import, export, shadow (R2)

**Behavior:** migration 2 (every phase 2 table, the `phase_moves` rows generated from
`phases.py`, the triggers and the partial unique index on PRs); the commands
`db import`, `db export`, `db reconcile`, `db backup` and `db downgrade`; I17; and the
tick's incremental import and reconcile while `store` is `json`.

**Failure modes:** an event line or slice file that does not parse goes to `import_raw`,
never dropped. A slice or run that breaks a rule is imported with `legacy=1`. A
reconcile difference opens `inv:db-drift`. A second import adds nothing. An interrupted
import is completed by a rerun, because each source is one transaction.

**Acceptance tests:**

- `test_import_keeps_every_record` (on the fixture copy: 77 of 77 slices, every run
  directory in the fixture, and every event line are present afterwards, as normal rows,
  legacy rows or raw rows; the counts in `import_log` add up to the source counts)
- `test_import_plan_slice_in_verify_is_legacy` and `test_import_shared_pr_is_legacy`
- `test_import_headless_run_ids`
- `test_import_roundtrip_is_lossless` (import, then export; the canonical digests are
  equal for every slice, events are byte-equal after `sort_keys`, and the counts per kind
  are equal; the fixture includes every event shape in the real 2,606 events)
- `test_import_is_idempotent`
- `test_unparsed_event_kept_raw`
- `test_new_write_cannot_set_legacy` covers insertion and 0 to 1 updates.
- `test_legacy_plan_repair` edits next_action on a flagged plan in VERIFY, checks it
  stays flagged, then moves to PLAN and checks that the flag and reason clear together.
- `test_constraints_refuse_plan_in_develop`, `test_trigger_refuses_illegal_move`,
  `test_fk_refuses_unknown_repo`, `test_unique_pr_refuses_second_open_slice`
- `test_append_only_triggers`
- `test_downgrade_each_migration` (upgrade, then downgrade; the schema equals the
  earlier version)
- `test_reconcile_detects_drift` (change one JSON field after the import; drift is
  reported)
- `test_question_copy_follows_page` (8.3)

**Mutations:** make the importer skip rows that break a rule; remove the plan-kind CHECK;
make the reconcile compare counts only; permit a normal 0 to 1 update; refuse the legal repair move.

#### S6. Flip to SQLite, write-through mirror (R2)

**Behavior:** `project.json "store": "sqlite"` switches the migrated state readers and every migrated
writer. For 7 days every write command also writes its JSON mirror and appends
`events.jsonl` (8.5). `slice move` runs an `UPDATE` under the trigger. `slice set` gets
`--waiting-kind` and `--waiting-ref`.

**Failure modes:** the database is unavailable after the flip (use the command-specific exit code in 8.4). A mirror write failure leaves the durable dirty marker for recovery in 8.5. A writer still writes JSON only (guard: an AST walk finds no
`write_json` to a state path outside `state/export.py` and the mirror function).

**Acceptance tests:**

- `test_cli_parity_both_backends` (every state CLI test, parametrized by backend,
  including `append_event(once=...)` against an imported or ordinary prior event)
- `test_dashboard_parity_both_backends` (the dash view built from the fixture is equal,
  field by field, on both backends)
- `test_mirror_written_per_command` (one `slice set` on sqlite; the JSON file and the
  event line exist before the command returns)
- `test_flip_back_with_corrupt_db` (corrupt the database file, flip to json; the JSON has
  every write)
- `test_mirror_commit_order_and_recovery` interleaves two writers, kills one after
  its SQL commit and retries. Check slice contents and event order exactly once.
  Refuse rollback from a dirty mirror when the database cannot be read.
- `test_post_archive_dispatch_and_ledger_readers` and `test_rollback_keeps_post_archive_writes`
- `test_no_json_only_state_writes_when_sqlite`
- `test_status_md_identical_both_backends`

**Mutations:** leave one writer (`notes.problem`) on JSON only; move the mirror write to
the tick.

#### S7. Contract (R2)

**Behavior:** after 7 days with no rollback, take the project lock, drain writers,
verify a clean mirror and stop mirror writes. Exactly the
files marked "archived" in 8.2 and their lock files move to `archive-YYYYMMDD/`.
`devloop-state check` warns when an archived file appears again.

**Acceptance tests:** `test_archive_moves_not_deletes`, `test_archive_list_matches_plan`
(the moved set equals the 8.2 list), `test_check_warns_on_stray_json`.

**Mutation:** make the archive step `unlink` the files; add `node-status.json` to the
archive list.

### 10.4 Enumerations

Line citations are to the installed lib (`~/.local/share/devloop/lib/devloop/`, equal to
`origin/feat/push-gate-s1`) on 2026-11-27.

#### Exit paths

- Refused by a rule: exit 3, from the `REFUSALS` handler at state_cli.py:295.
- Lease lost (abandoned, ended by the orchestrator, wrong token): exit 4. Only new
  commands. The runner stops as in 5.1.
- Store unavailable (busy past 5 s, disk full, schema too new): exit 5. Only new commands.
- `run exec` child exits or is killed: its own code, or 128 plus the signal. On lease
  loss: exit 4 after the group is terminated.
- A runner that crashes with no `end`: no exit to the store. It reads as silent when its
  lease ends, and the orchestrator ends it with `--as orchestrator`.
- The tick fails part way: each sweep and each invariant check is its own transaction, so
  a failure leaves earlier results committed.

#### Routes

- Headless: the dispatcher writes run directories, `apply.collect` (apply.py:43) reads
  them, and the phase 2 import turns them into rows.
- Live: the orchestrator runs `run start`, and the subagent reports through `run log`,
  `exec`, `wait` and `end`.
- Orchestrator-owned work (Codex reviews, `vm-run` tests) goes through `run exec` or
  `run wait` on the devbox.
- Phase 1 slice moves go through `slices._move` (slices.py:880), whose live bypass is at
  slices.py:909. Phase 2 moves go through the same function into the `UPDATE` and the
  trigger.

#### FK shapes

- `run_log.run_id` to `runs.run_id` (many to one, phase 1).
- `run_slices.run_id` to `runs.run_id` (many to one, phase 1), and `run_slices.slice_key`
  to `slices.key` (many to one, from phase 2). Together they make runs and slices many to
  many.
- `slices.repo` to `repos.name`; `slice_moves.key` to `slices.key`;
  `slice_moves.run_id` to `runs.run_id` (nullable).
- `questions.slice_key` is not a foreign key, because a question may name a row or a key
  that no longer exists. I4 joins it by value.

#### Fields

- Run fields: section 4 `runs`. Each TEXT column is in the `text.py` registry, and the
  guard test fails on one that is not.
- Slice fields: `_FIELDS` (moved to `state/fields.py`) plus `kind`, `waiting_kind`,
  `waiting_ref`, `waiting_note` and `legacy`. The other fields go to `extra` unchanged.
- Event fields: the raw object in `fields`, with `kind`, `at`, `slice_key`, `run_id` and
  the source line as columns.

#### Output surfaces

- STATUS.md, built in `status.render_status` (status.py:110) from counts, IDs and fixed
  words only, and scrubbed per line.
- The tick's stdout (`SILENT` and `FAIL` lines): IDs, role names and minutes only.
- The dashboard: `view.py` and `live.py` on dash-mc. New run cards use the public projection in section 9.
- `run show --json` and `run list --json`: never print `token`.
- `REFUSED:` messages on stderr: use fixed reasons, field names and the holding run id for writer conflicts.
- Slack: headless mode only, through `tick._alert` (tick.py:52) and its fixed words.

### 10.5 Defect classes

| # | Class | Where it can happen here | Structure that closes it | Sentry or property test | Mutation that must go red |
|---|---|---|---|---|---|
| 1 | Value-bearing output | notes, summaries, wait refs and `REFUSED:` text | `text.py` registry used by argparse and `db.write` (section 9) | guard walks the parser and the schema; marker corpus through every output surface | remove `type=text.arg` from `run end --summary` |
| 2 | Cleanup missing on an exit path | the `run exec` beater and the child group; partial imports | `exec` owns the child group in one context manager; one transaction per import source | exit-path matrix: child exit, signal, lease loss, parent kill, store busy, double end | make `exec` skip the group kill on lease loss |
| 3 | State machine and concurrency | run states, slice phases, many writers | written tables (`LIVE_MOVES`, run state CHECKs, `runs_now`), `BEGIN IMMEDIATE`, the fencing token | `test_every_pair_outside_table_refused`; `test_concurrent_writers_no_busy` with read-then-write | `BEGIN` instead of `BEGIN IMMEDIATE`; drop the token check |
| 4 | Route parity and silent fallback | headless against live runs; JSON against SQLite backend; GraphQL against per-PR `gh` | the backend read contracts; `gh` failure reads `unchecked` | `test_cli_parity_both_backends`; `test_github_failure_reads_unchecked_not_pass` | make the GitHub error path return pass |
| 5 | Referential integrity | runs against slices (many to many); run_log against runs; slice repo; PR ownership | foreign keys with `foreign_keys=ON`; `run_slices`; the unique PR index | `test_fk_refuses_unknown_repo`; `test_unique_pr_refuses_second_open_slice` | remove `foreign_keys=ON` from `db.connect` |
| 6 | Untrusted producer | subagent text, imported history, `gh` JSON | bounded text rules; legacy and raw rows for import; `gh` output parsed into typed fields only | hostile corpus per text kind; a malformed `gh` reply gives `unchecked` | let the importer drop rows that break a rule |
| 7 | Check then use on a path (TOCTOU) | the beater checks a pid then beats; db file path | the beater checks pid and start time together (`runs.proc_start`); one db path from `store.state_path` | `test_no_sqlite_connect_outside_db_module`; pid reuse test with a fake start time | check the pid only, not the start time |
| 8 | Weak tests and overclaimed evidence | golden tests on live copies; parity and ancestor claims | every invariant has a firing test; the full suite on the exact merge head per 10.1; slice 0 proves landing with `merge-base --is-ancestor` | `test_every_invariant_has_a_firing_test` | delete one firing test |
| 9 | Behavior-preserving refactor drift | phases and fields moved out of `slices.py`; the S6 flip | re-exports plus pinned headless tables; characterization tests on JSON first | `test_headless_tables_unchanged`; `test_dashboard_parity_both_backends` | change one headless transition |
| 10 | Frozen contract drift | the CLI (incl. `merged`), the event line, the slice file, STATUS.md, the headless tables | the list in section 3.5, additive only | snapshot tests from S1 | change an existing command's stdout format |

## 11. Failure modes across the plan

- Subagents forget to beat, false silent alerts pile up, and the orchestrator stops
  reading them. Mitigations: `exec` for long commands, declared waits, a 20-minute lease,
  one alert per run, and a `revived` beat closes the alert.
- A beat fails because the store is busy, and a builder stops its work over
  bookkeeping. Mitigation: exit 5 means go on. Only exit 4 stops work.
- The move table is wrong, and the orchestrator is refused in the middle of an iteration.
  Mitigation: `--override`, which stays visible through I10.
- A long dashboard read blocks the checkpoint and the WAL grows. Mitigations: one short
  read per request, and a TRUNCATE checkpoint above 8 MB.
- The import loses history. Mitigations: legacy and raw rows instead of skips, the
  `source_line` unique key, the keep-every-record and round-trip tests, and the 2-day
  shadow.
- The value-free rule refuses an honest note. The agent gets the field name and writes
  the note again. A real pattern miss is a follow-up change to `text.py`.
- Slice 0 lands the wrong code. Mitigation: the install tree was matched byte for byte to
  `a753f7f`, and the done-when is an ancestor check, not a diff.

## 12. Pre-mortem

It is January and RUN-STATUS has failed. The likely reasons:

1. **The run block was in the brief, but subagents ignored it.** Every run went silent,
   and the alerts became noise. Check: S4 acceptance includes one live slice end to end
   before S5 starts. If more than 1 in 5 runs end with `--as orchestrator` in the first 2
   days, the brief is fixed before phase 2 starts.
2. **Main and the install drifted apart again.** A feature was built on a side branch, and
   the install came from that branch. Check: the branch rules in 10.1, and
   `devloop-install` records its source sha, which `devloop-state check` tests for being
   an ancestor of freshly fetched `origin/main`, or reports `unchecked` if unavailable.
3. **Phase 2 changed what the dashboard shows.** Check: the parity tests in S6, and the
   write-through mirror.
4. **The invariants raised false alarms.** Before phase 2, I3 and I4 read free text, and
   they could fire on normal wording. Check: I3 waits for run rows (S3b), and the S1
   golden test on the real copy must show only the known cases. A false positive is
   fixed by a tighter pattern, never by removing the check.
5. **GitHub limits or `gh` auth broke I6, I7 and I14.** Check: "unchecked" shows in
   STATUS.md, and the cost is one call per repo per 10 minutes.

## 13. Open questions

**Owner questions:** none open. Slice 0's review scope was decided on 2026-11-27 (see
the top of this plan).

**Technical choices made in this plan, open to reviewer challenge:**

- Run rows go to SQLite in phase 1 (section 3.1).
- `synchronous=FULL`, not NORMAL. The write rate is low, and FULL keeps today's
  durability.
- Silent is judged at read time and is not terminal. The loop never kills a subagent by
  itself.
- The vault questions page stays the source of truth, and the database holds a copy.
- `feat/full-suite-batches` stays out of slice 0, because it changes live behavior.
- `evidence` and `plan_rounds` stay JSON columns.

## 14. V2 log

Logged from Dennis round 1. These are not built in this plan.

- **V2-LOG 1. `slices.py` size.** It is 1,063 lines. S2 already moves the phases, fields and
  kind derivation out, which shrinks it. The rest of the split waits for the later
  structure review (memory "structure review later").
- **DESIGN follow-up.** dash/view.py already has 1,295 lines on 47ee2e4. Keep the
  run-card code in dash/live.py and use the existing view composition point.
- **V2-LOG 2. I5 on VERIFY.** Extend `gate-branch-lacks-main` to VERIFY, so a test run on a
  head behind main is flagged before the gate.
- **V2-LOG 3. Golden-fixture scrub step.** S1 checks a copy of live slice text into the repo.
  The scrub step for that copy should become a script (`tests/fixtures/make_fixture.py`)
  that rebuilds the fixture automatically. For S1, hand-replace free text with synthetic
  examples and retain only the structure needed for the tests. Review before check-in.
