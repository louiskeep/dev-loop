---
Status: plan
updated: 2026-11-27
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

Owner rule (2026-11-27): every RUN-STATUS slice branches from the plugin's `main` and
merges back to `main` when done. Nothing stays on a side branch.

## 1. Frame

### 1.1 Where the code is today

- `origin/main` of the plugin has **no `devloop/` package**. All loop code is on
  unmerged branches that stack on `origin/feat/headless-loop` (131 commits past main).
- `feat/dash-mission-control` (dash-mc) is headless-loop plus 44 commits (175 past
  main). The local branch is 9 commits ahead of its origin copy. The live dashboard runs
  from the `.worktrees/dash-mc` worktree.
- The installed library (`~/.local/share/devloop/lib`, which `~/bin/devloop-state` and
  `~/bin/devloop-tick` load) is **not** dash-mc. Its `store.py`, `runs.py`, `slices.py`,
  `tick.py` and `status.py` match `feat/docs-skip-qa` exactly. It also has `batch.py`
  and `state_cli.py` changes from `feat/full-suite-batches`. dash-mc differs from it in
  34 files.
- The PR-create guard ("scope standalone PR creation to guarded repos") is on
  `feat/docs-skip-qa`, not on main. Main has one commit that dash-mc lacks: the
  rules-gate change `174096a`.

So the build cannot branch from main as it is now. Slice 0 (section 10.2) lands the loop
code on main first. Every build slice then branches from main. This plan cites code as
it is on dash-mc, and the installed lib where the two differ.

### 1.2 How state works today

- **Slices:** one JSON file per slice in `slices/`, written only by `slices.py` under an
  `fcntl.flock` on `<file>.lock` (`store.locked`). Each file holds a 200-entry `history`
  list and JSON blobs for evidence and plan rounds.
- **Events:** `events.jsonl`, appended under `events.jsonl.lock` with an fsync per line.
  2606 events from 2026-11-25 21:02 UTC to 2026-11-27 06:35 UTC.
- **Runs:** one directory per run in `runs/` (527 directories, 223 MB). Each holds a frozen
  `request.json`, `proc.json`, `exit`, `applied` and the result file. **Only the headless
  dispatcher creates runs.** The last `run-dispatched` event is 2026-11-26 23:14 UTC
  (17:14 CST). Since the project went to live mode, subagents leave no run record. Their
  only trace is the free text in a slice's `next_action`.
- **Liveness for headless runs:** the wrapper's pid, start time and boot id, plus an idle
  limit on output growth (`wrap.py`). Live subagents have none of this.
- **Locks:** 22 `.lock` files in the state directory, one per state file.
- **Owner questions:** the vault page `OWNER-QUESTIONS.md`. The owner answers by editing
  that page, and `notes.read_answers` applies the answers. The page is the owner's input
  surface.
- **Live mode bypass:** `slices._move` in live mode skips both the transition table and
  the evidence checks (`if to not in TRANSITIONS[frm] and not (live and ...)`). The
  live-mode skill (`~/.claude/skills/dev-loop-ui/SKILL.md`) says that moves "skip the
  headless run-evidence checks".

### 1.3 The failure cases, with evidence from the live state

Read from `~/.local/state/devloop/decoy/` on 2026-11-27, read only.

1. **Plan slices in DEVELOP.** 11 `slice-moved` events put 9 different plan slices into
   DEVELOP. Examples: `RUN-STREAM--plan` (twice), `PROP-FALLBACK--plan` (twice),
   `PER-COLUMN-ROUTING--plan` and `EXPERIMENTAL-TIER--plan` (2026-11-26 15:55 UTC).
   Plan slices also use REVIEW and REMEDIATE for plan-review rounds (`58--G7-2-plan` and
   `58--G7-3-plan` made 8 such moves between 04:09 and 04:28 UTC). In total, 100 of the
   722 recorded moves are not in `slices.TRANSITIONS`. Examples: VERIFY to GATE (10),
   REMEDIATE to REVIEW (9), FRAME to DEVELOP (8), DOCUMENT to DOCUMENT (6), FRAME to GATE.
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
   main (#822)". The gate passed on a head that was already behind main. The state model
   cannot see this.

Smaller signs of the same drift: `RUN-STATUS--plan` and `PUSH-GATE--redesign` record
`repo=engine` for plugin work. There are 302 `board-mirror-failed` events (42% of the
moves) in the journal, which hide the useful events.

### 1.4 Definition of done

- Every subagent and every long command the orchestrator starts has a run row: role,
  slice, lease, last beat, an ordered step log, end status, verdict, head and test
  counts. A reader gets these from a typed command or from SQL, never by parsing prose.
- A run that stops beating past its lease shows as "runner silent" within one tick: in
  STATUS.md, on the dashboard, and in the tick's own output. The orchestrator sees it
  without asking.
- Each tick runs the invariant list (section 7). Each of the four cases above is caught
  by a named invariant, on today's data.
- Illegal phases and moves are refused when they are written. After phase 2, the
  database's own constraints refuse them too.
- After phase 2, all slice, event, run, question and problem state is in one SQLite file.
  The import of the existing history is proven lossless, and a rollback path is tested.

### 1.5 Guarantee and non-goals

**Guarantee:** state written through `devloop-state` is typed, value-free, checked
against the phase rules, and durable when many writers run at once. A run that stops
reporting is visible within one tick. The invariants in section 7 run every tick.

**Non-goals:**

- No change to how the owner answers questions. The vault page stays the input surface,
  and the database holds a copy.
- No raw SQL for agents. The owner and the orchestrator may read the file with the
  `sqlite3` CLI or DuckDB for reports, read only.
- No move of run artifacts (result files, transcripts, worktrees: 223 MB) into the
  database. Rows point at the run directory.
- No automatic fixes from invariants. They report. The one exception is the lease state
  change to `silent`, which is bookkeeping only.
- No defense against a hostile agent that forges a token or writes SQL directly. The
  threat model is local, single user, accidents only (owner decision, memory
  "devloop-local-threat-model").
- No remote writers over a network filesystem. A remote node reports through a command
  that runs on the devbox (over ssh), never through a shared mount of the database file.
- No redesign of the headless dispatcher. Its `runs/` directories keep working, and
  phase 2 indexes them as rows.

### 1.6 Expected size

About 3,800 changed lines over 7 build slices, tests included, plus the slice 0 merge.
New public names: the `devloop-state <project> run ...`, `invariants` and `db ...`
command families (section 5), the `devloop.state` sub-package, the slice field `kind`,
and the project config keys `store` and `invariants`. Each slice's size is in
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
  transaction that upgrades from read to write can fail with `SQLITE_BUSY` at once, and
  the busy timeout does not help it. `BEGIN IMMEDIATE` takes the write lock at the start,
  so `busy_timeout` applies, and nothing later in that transaction fails as busy. The
  common setting is a `busy_timeout` of about 5000 ms.
- **Schema versions:** `PRAGMA user_version`
  ([pragma](https://www.sqlite.org/pragma.html#pragma_user_version)) plus an ordered list
  of migrations, each run once in one transaction. This is the usual pattern for small
  stdlib projects. A `migrations` table records the name and checksum of each step.
- **Constraints:** `STRICT` tables ([stricttables](https://www.sqlite.org/stricttables.html),
  SQLite 3.37 or later; the devbox has 3.40.1 and the plugin venv has 3.53.1), `CHECK`
  constraints, foreign keys (off by default, so `PRAGMA foreign_keys=ON` on every
  connection, see [foreignkeys](https://www.sqlite.org/foreignkeys.html)), partial unique
  indexes, and `BEFORE UPDATE` triggers that `RAISE(ABORT)`.
- **Leases and heartbeats:** Temporal's activity heartbeat timeout
  ([docs](https://docs.temporal.io/encyclopedia/detecting-activity-failures)) is
  separate from the whole-task timeout. It catches a worker that stopped in the middle of
  a task. Heartbeats are sent at most every 0.8 of the timeout. Job queues on SQLite and
  Postgres (go-flywheel, forge-jobs, the
  [heartbeat-lease write-up](https://webdock.io/en/docs/how-guides/programming-guides/handling-worker-crashes-with-heartbeat-based-leases))
  extend a lease from a background heartbeat while the work runs. They sweep expired
  leases with a timestamp check, keep the lease longer than the beat interval, and use a
  fencing (claim) token on final writes so a stale worker cannot finish a job it lost.
- **In the codebase:** `store.locked` and atomic replace (today's model), `runs.KINDS`
  and `runs.KIND_PHASES` (the role-to-phase table), `wrap.py` (deadline plus idle
  limit), `tick._alert` (one alert per reason per six hours), `scrub.py` (secret, path
  and hex patterns), `store.canonical_digest` on dash-mc (a canonical JSON digest), and
  `check.run_checks` (the doctor checks).

**What the design follows:** WAL with `synchronous=FULL` (today's code fsyncs every
write, so durability stays the same), `busy_timeout=5000`, `BEGIN IMMEDIATE` for every
write, `user_version` migrations, STRICT tables with CHECK constraints and foreign keys,
and a Temporal-style heartbeat lease with a fencing token. A background beater keeps a
long command alive. Everything uses the stdlib `sqlite3` module, with no new dependency.
Every query binds its values as parameters; no value is put into SQL text (security
rulebook).

**Custom code that remains, and why:** the run commands and the invariants use this
loop's own vocabulary (roles, phases, slices). No queue library fits a "worker" that is
a Claude subagent able only to run shell commands. A library would add a daemon and a
dependency to support five small commands.

## 3. Design

### 3.1 Phase order, checked against the code

The owner's order is phase 1 (commands, leases and invariants on today's store), then
phase 2 (the move to SQLite with an import of the history). The code supports this
order with one change: **run rows go into SQLite already in phase 1.**

Why:

- Live runs have no store today, so there is no run history to import. A JSON format for
  live runs, with its own lock files, would be built in phase 1 and thrown away in
  phase 2.
- Heartbeats are the highest write rate in the system: up to 16 runs, each beating every
  few minutes. These writes need WAL and `BEGIN IMMEDIATE` the most, and they suit one
  flock-guarded JSON file per run the least.
- The invariants only read, so they work on the JSON store as it is. They come first,
  because they catch today's four cases with no change to the write path.
- Slices, events, questions and problems carry history, and the dashboard and STATUS.md
  read them. Their move is the risky part, so it stays in phase 2, with an import, a
  shadow period and a rollback.

So in phase 1 the state is in two places: the JSON files, and `devloop.db` with only the
runs and run-log tables. The dashboard gets one new reader for runs. Phase 2 moves the
rest into the same file.

### 3.2 Design notes

- **New structure:** a sub-package `devloop/state/`. The top-level `devloop/` package
  already has 27 modules, over the fan-out limit of about 25 in the architecture
  rulebook, so this plan adds no top-level modules.
- **Principles, each tied to a concrete pain:**
  - One choke point per rule (cross-cutting concerns). Today a slice file, an event or a
    problem can be written from several modules, and live mode bypasses the move rules.
    In this design, all database access goes through `state/db.py`. All free text goes
    through one column registry in `state/text.py`. The phase rules are in one table,
    and the phase 2 trigger data is generated from that table.
  - Dependency inversion, only where a second backend really exists. The phase 2 switch
    needs `slices.load_all`, `status.events`, `runs.iter_runs` and
    `notes.load_questions` to keep their signatures while the storage under them
    changes. Those four functions are the seam. No new abstract layer is added.
- **Pattern sources:** the SQLite docs (WAL, STRICT, `user_version`), the Temporal
  heartbeat lease, and the queue fencing token (section 2).
- **One place per fact:** roles and phases are in `runs.KIND_PHASES`, extended with the
  live roles. Phases are in `slices.PHASES`. Legal moves are in `state/phases.py`,
  derived from `slices.TRANSITIONS` plus the live additions. Text limits are in
  `state/text.py`. Invariants are in `state/invariants.py`. The SQL CHECK value lists
  are generated from these Python tuples when the schema is built, never typed twice.
- **House style:** typed context objects (`RunCtx`, `InvariantCtx`), no flag arguments
  that switch behavior, and comments that explain why.

### 3.3 Placement

The plugin has no CODEMAP. Slice 1 adds `docs/CODEMAP.md` with a short "Where things go"
section for `devloop/`, `devloop/state/` and `devloop/dash/` only. That map change is
stated here, so the placement rule can be checked.

| New file | Directory | Map line (added in S1) | Sits beside |
|---|---|---|---|
| `__init__.py` | `devloop/state/` | "state storage, run records and invariants go in `devloop/state/`" | `devloop/store.py` |
| `invariants.py` | `devloop/state/` | same | `devloop/check.py` |
| `phases.py` | `devloop/state/` | same | `devloop/slices.py` |
| `db.py` | `devloop/state/` | same | `devloop/store.py` |
| `schema.py` | `devloop/state/` | same | `devloop/state/db.py` |
| `text.py` | `devloop/state/` | same | `devloop/scrub.py` |
| `runlog.py` | `devloop/state/` | same | `devloop/runs.py` |
| `run_cli.py` | `devloop/state/` | same | `devloop/state_cli.py` |
| `importer.py` | `devloop/state/` | same | `devloop/state/db.py` |
| `export.py` | `devloop/state/` | same | `devloop/state/importer.py` |
| tests | `tests/devloop/` | "tests mirror the package" | the existing `tests/devloop/test_*.py` |

`state_cli.py` stays the one entry point. It adds subparsers and hands off to
`state/run_cli.py`, so it stays under the 800-line cap.

### 3.4 Reuse inventory

| Need | Existing code that does it | Use |
|---|---|---|
| Refusal exit code | `state_cli.REFUSALS` and the `REFUSED:` line with exit 3 | Reuse as is; new errors join the tuple |
| Name checks | `store.NAME`, `store.TOKEN`, `store.check_name` | Reuse for slice keys, run ids and steps |
| Secret and path shapes | `scrub._TOKEN`, `_URL_CRED`, `_LONG`, `_PATH` | `text.py` imports them; no second copy |
| Role deadlines and role-to-phase table | `runs.KINDS`, `runs.KIND_PHASES` | Extend with live kinds; one table |
| Process liveness | `runs.proc_start` (pid plus start time) | `run exec` uses it for the beater |
| Alert dedupe | `tick._alert` and `alerts.json` | Reuse for silent-run alerts |
| Canonical digest | `store.canonical_digest` (dash-mc) | Import idempotency and reconcile |
| Slice validation | `slices._validate`, `_FIELDS` | The importer runs every file through it |
| Questions parser | `notes.load_questions`, `notes.read_answers` | The vault page stays the input; the db copies it |
| Doctor checks | `check.run_checks` | Adds `quick_check` and the stray-JSON warning |

New code is only what has no owner today: the SQLite connection module, the run
commands, the invariant registry and the importer.

### 3.5 Frozen contracts

These must not change, or may change only by adding:

- Every existing `devloop-state` command: name, arguments, stdout format and exit codes
  (0, 2 and 3). New exit codes 4 and 5 apply only to the new `run` and `db` commands.
- The `events.jsonl` line format while `store` is `json`, and the export format after the
  flip, because the dashboard on dash-mc and `status.summarize` parse it.
- The headless run directory layout (`request.json`, `proc.json`, `exit`, `applied`,
  result header), read by `apply.collect` and the dashboard.
- The slice JSON fields. `schema_version` 2 only adds `kind`, and version 1 files still
  load.
- The owner questions page format in the vault.
- The STATUS.md sections that exist today. New sections are added after them.

A snapshot test per contract (CLI help output, an event line, a slice file, a STATUS.md
render) is part of S1 and is kept for every later slice.

## 4. Schema

One file per project: `~/.local/state/devloop/<project>/devloop.db`, mode 0600, on local
disk. Every table is `STRICT`. The CHECK value lists are generated from the Python
tuples named in section 3.2.

Phase 1 (slice 3) creates `migrations`, `runs` and `run_log` only. Phase 2 (slice 5)
adds the rest.

```sql
-- migration bookkeeping (user_version holds the latest id)
CREATE TABLE migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  sha TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT;

CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY CHECK (run_id GLOB 'r-[0-9]*'),  -- r-20261127-0042
  seq          INTEGER NOT NULL UNIQUE,
  slice_key    TEXT,                 -- REFERENCES slices(key) from phase 2; NULL for duties
  role         TEXT NOT NULL CHECK (role IN ('planner','builder','reviewer','tester',
                                             'tech-writer','duty')),
  kind         TEXT NOT NULL,        -- a runs.KINDS name or a live kind, e.g. 'codex-review'
  node         TEXT NOT NULL CHECK (node IN ('devbox','mac','dell','gcp')),
  state        TEXT NOT NULL CHECK (state IN ('running','silent','done','blocked',
                                              'failed','abandoned')),
  token        TEXT NOT NULL,        -- fencing token; never rendered on any page
  lease_s      INTEGER NOT NULL CHECK (lease_s BETWEEN 300 AND 14400),
  deadline_at  TEXT NOT NULL,        -- start plus the role deadline from runs.KINDS
  started_at   TEXT NOT NULL,
  last_beat_at TEXT NOT NULL,
  lease_until  TEXT NOT NULL,
  ended_at     TEXT,
  ended_by     TEXT CHECK (ended_by IN ('runner','orchestrator')),
  verdict      TEXT CHECK (verdict IN ('GO','GO-WITH-FIXES','NO-GO')),
  head         TEXT CHECK (head IS NULL OR (length(head) BETWEEN 7 AND 64
                                            AND head NOT GLOB '*[^0-9a-f]*')),
  pr           INTEGER CHECK (pr IS NULL OR pr > 0),
  passed       INTEGER CHECK (passed IS NULL OR passed >= 0),
  failed       INTEGER CHECK (failed IS NULL OR failed >= 0),
  summary      TEXT,                 -- value-free, at most 200 characters
  run_dir      TEXT,                 -- headless runs only (phase 2 import)
  CHECK ((state IN ('running','silent')) = (ended_at IS NULL)),
  CHECK ((ended_at IS NULL) = (ended_by IS NULL)),
  CHECK (state <> 'done' OR role NOT IN ('reviewer','tester') OR verdict IS NOT NULL)
) STRICT;
-- Two writers in one worktree is a standing ban (memory: no-concurrent-builders).
CREATE UNIQUE INDEX one_live_writer ON runs(slice_key)
  WHERE state IN ('running','silent') AND role IN ('builder','tech-writer');
CREATE INDEX runs_live ON runs(state, lease_until);

CREATE TABLE run_log (              -- append-only: the record of everything a run did
  id     INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  at     TEXT NOT NULL,
  step   TEXT NOT NULL CHECK (step IN ('started','note','plan-read','worktree-ready',
          'build-started','commit','pushed','pr-opened','test-started','test-done',
          'review-started','review-done','docs-written','blocked','ended','silent',
          'revived','abandoned','lease-held')),
  note   TEXT                       -- value-free, at most 120 characters
) STRICT;
CREATE TRIGGER run_log_no_update BEFORE UPDATE ON run_log
  BEGIN SELECT RAISE(ABORT, 'run_log is append-only'); END;
CREATE TRIGGER run_log_no_delete BEFORE DELETE ON run_log
  BEGIN SELECT RAISE(ABORT, 'run_log is append-only'); END;
CREATE TABLE run_seq (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL) STRICT;
```

Phase 2 adds these tables. Columns are shortened here, and every TEXT column is in the
`text.py` registry.

```sql
CREATE TABLE repos (name TEXT PRIMARY KEY) STRICT;   -- filled from project.json repos
CREATE TABLE slices (
  key TEXT PRIMARY KEY, row TEXT NOT NULL, slice TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('build','plan','docs','duty','release')),
  repo TEXT NOT NULL REFERENCES repos(name),
  phase TEXT NOT NULL CHECK (phase IN (/* slices.PHASES */)),
  risk TEXT CHECK (risk IN ('R0','R1','R2','R3')),
  branch TEXT, pr INTEGER CHECK (pr IS NULL OR pr > 0), head TEXT /* hex rule as runs */,
  worktree TEXT, plan_path TEXT, purpose TEXT, next_action TEXT,
  waiting_kind TEXT NOT NULL DEFAULT 'none' CHECK (waiting_kind IN
    ('none','owner-question','slice','run','external','slot')),
  waiting_ref TEXT, waiting_note TEXT,
  round INTEGER NOT NULL CHECK (round >= 0), plan_lineage INTEGER NOT NULL,
  parked_from TEXT CHECK (parked_from IS NULL OR parked_from IN (/* ACTIVE */)),
  park_reason TEXT, owner_answered INTEGER NOT NULL CHECK (owner_answered IN (0,1)),
  evidence TEXT NOT NULL CHECK (json_valid(evidence)),   -- stays JSON in this plan
  plan_rounds TEXT NOT NULL CHECK (json_valid(plan_rounds)),
  extra TEXT NOT NULL CHECK (json_valid(extra)),         -- the other slice fields
  text_ok INTEGER NOT NULL DEFAULT 1,                    -- 0 for imported text that fails the rule
  last_moved_at TEXT NOT NULL,
  -- a plan slice never enters a build phase; plan-review rounds may use REVIEW and REMEDIATE
  CHECK (kind <> 'plan' OR phase IN ('FRAME','PLAN','REVIEW','REMEDIATE','DONE',
                                     'PARKED','HELD')),
  CHECK ((phase IN ('PARKED','HELD')) = (parked_from IS NOT NULL)),
  CHECK (phase NOT IN ('GATE','DOCUMENT') OR kind IN ('plan','duty') OR pr IS NOT NULL)
) STRICT;
CREATE TABLE phase_moves (mode TEXT, kind TEXT, from_phase TEXT, to_phase TEXT,
  PRIMARY KEY (mode, kind, from_phase, to_phase)) STRICT;   -- generated from phases.py
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1),
  mode TEXT NOT NULL CHECK (mode IN ('headless','live')),
  allow_override INTEGER NOT NULL DEFAULT 0) STRICT;       -- set for one statement by --override
CREATE TRIGGER slice_phase_legal BEFORE UPDATE OF phase ON slices
  WHEN (SELECT allow_override FROM settings) = 0 AND NOT EXISTS (
    SELECT 1 FROM phase_moves m WHERE m.mode = (SELECT mode FROM settings)
      AND m.kind = NEW.kind AND m.from_phase = OLD.phase AND m.to_phase = NEW.phase)
  BEGIN SELECT RAISE(ABORT, 'illegal phase move'); END;
CREATE TABLE slice_moves (id INTEGER PRIMARY KEY, key TEXT NOT NULL REFERENCES slices(key),
  at TEXT NOT NULL, from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, reason TEXT,
  run_id TEXT REFERENCES runs(run_id), override INTEGER NOT NULL DEFAULT 0) STRICT;
CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  slice_key TEXT, run_id TEXT, fields TEXT NOT NULL CHECK (json_valid(fields)),
  source_line INTEGER UNIQUE) STRICT;                       -- line number in events.jsonl
-- events and slice_moves get the same append-only triggers as run_log
CREATE TABLE questions (key TEXT PRIMARY KEY, slice_key TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('wait','default')),
  status TEXT NOT NULL CHECK (status IN ('open','answered','defaulted','withdrawn')),
  options TEXT NOT NULL CHECK (json_valid(options)), recommended TEXT, answer TEXT,
  applied TEXT, asked_at TEXT, answered_at TEXT,
  CHECK ((status IN ('answered','defaulted')) = (answer IS NOT NULL))) STRICT;
CREATE TABLE problems (key TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('agent','invariant','lease')),
  open INTEGER NOT NULL CHECK (open IN (0,1)), impact TEXT NOT NULL, fix TEXT NOT NULL,
  detail TEXT, count INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)
  STRICT;
CREATE TABLE lessons (key TEXT PRIMARY KEY, area TEXT NOT NULL, wrong TEXT NOT NULL,
  instead TEXT NOT NULL, at TEXT NOT NULL) STRICT;
CREATE TABLE alerts (reason TEXT PRIMARY KEY, last_at TEXT NOT NULL) STRICT;
CREATE TABLE import_log (id INTEGER PRIMARY KEY, source TEXT NOT NULL, sha TEXT NOT NULL,
  rows INTEGER NOT NULL, at TEXT NOT NULL) STRICT;
```

Notes:

- `waiting_on` today is free text. It becomes `waiting_kind`, `waiting_ref` and
  `waiting_note`, so case 3 becomes a join: `waiting_kind='owner-question'` and the
  question is answered. The import maps old text to `waiting_kind='external'`, with the
  text in `waiting_note`. Invariant I4 still scans that text.
- `kind` is new on slices. Slice 2 adds it to the JSON files as `schema_version` 2. The
  default comes from the key: a `--plan` or `-plan` suffix gives `plan`, `--docs` gives
  `docs`, `DUTY-RELEASE` gives `release`, another `DUTY-` prefix gives `duty`, and
  everything else gives `build`. `slice set --kind` corrects a wrong guess.
- `evidence` and `plan_rounds` stay JSON columns, checked with `json_valid`. Normalizing
  them is a follow-up. Nothing in this ask queries them by field.
- A headless run row (from the phase 2 import) keeps `run_dir`. Its result file stays on
  disk.

## 5. Agent-facing commands

All commands are `devloop-state <project> ...`, the existing entry point. Agents never
run SQL.

### 5.1 Exit codes (all run, invariant and db commands)

| Code | Meaning | What the caller does |
|---|---|---|
| 0 | Done | Go on |
| 2 | Usage error (argparse) | Fix the command |
| 3 | Refused by a rule (`REFUSED: <reason>` on stderr; the reason names fields only) | Fix the record; never work around it |
| 4 | Lease lost: the run is ended or abandoned, or the token does not match | **Stop work**, and say so in the final message |
| 5 | Store unavailable (busy past the timeout, disk full, schema newer than the code) | Retry once after 10 s. If it fails again, keep working and say so in the final message |

Code 3 keeps its current meaning, so existing callers do not change.

### 5.2 Commands

| Command | Who | Effect | Refuses (code) |
|---|---|---|---|
| `run start --slice KEY --role ROLE --kind KIND [--node N] [--lease 20m]` | orchestrator | Inserts a `running` row and a `started` log row. Prints `RUN=r-YYYYMMDD-NNNN TOKEN=xxxxxxxx`. | unknown slice (3); the role does not fit the slice's phase in `runs.KIND_PHASES` (3); a live writer already on the slice (3, from the unique index); lease out of range (3); store (5) |
| `run beat RUN --token T` | runner | Sets `last_beat_at` to now and `lease_until` to now plus `lease_s`. A `silent` run goes back to `running` with a `revived` log row. | not running or silent (4); wrong token (4) |
| `run log RUN --token T STEP [--note TEXT]` | runner | Appends a log row, then beats. | unknown step (2); the note fails the text rule (3); lease (4) |
| `run hold RUN --token T --for 90m --why STEP` | runner | One lease extension for a known long step, capped at `deadline_at`. Logs `lease-held`. | past the deadline (3); lease (4) |
| `run exec RUN --token T -- CMD ...` | runner or orchestrator | Runs `CMD` as a child. Beats every 60 s while the child lives. Passes the child's exit code and its stdout and stderr through unchanged. | the first beat as for `beat` (4); after that, the child's own code |
| `run end RUN --token T --status done\|blocked\|failed [--verdict V] [--head SHA] [--pr N] [--passed N --failed N] --summary TEXT [--as orchestrator]` | runner or orchestrator | Sets the end fields and logs `ended`. The same end sent twice is a no-op that exits 0. | reviewer or tester `done` with no verdict (3); head not hex, or not in the slice's repo (`git cat-file -e`, 3); text rule (3); a different end on an ended run (4); wrong token (4) |
| `run abandon RUN --reason TEXT` | orchestrator | Sets `state=abandoned` and `ended_by=orchestrator`. Later beats get code 4. | already ended (3) |
| `run show RUN [--json]` | anyone | One line, or the row and its log as JSON | unknown run (3) |
| `run list [--live] [--silent] [--slice KEY] [--json]` | anyone | Rows | none |
| `run watch [--timeout 30m]` | orchestrator | Blocks. Prints one line when a run goes silent, ends or passes its deadline, then exits 0. Exits 1 on timeout. Made for the Monitor tool. | none |
| `run sweep` | tick | Marks expired leases `silent`, opens `lease:<run>` problems, prints `SILENT ...` lines | none |
| `invariants [--json] [--no-github]` | tick, orchestrator | Runs section 7, prints `FAIL <name> <subject>` lines, upserts problems | exits 3 when any check fails |
| `db status`, `db import --dry-run\|--apply`, `db export DIR`, `db reconcile`, `db backup`, `db downgrade --to N` | owner, tick (phase 2) | Section 8 | |

Live kinds added to `runs.KIND_PHASES`: `tech-writer` (DOCUMENT), `codex-low` and
`codex` (PLAN, REVIEW, GATE), `t1` and `full` (VERIFY, REVIEW, GATE), and `planner`
(FRAME, PLAN, REVIEW, REMEDIATE on plan slices). It stays one table, read by both the
headless path and the live path.

### 5.3 How a subagent learns its run id

The orchestrator runs `run start` **before** it spawns the subagent, and pastes the two
printed values into the brief. The Agent tool cannot pass environment variables, so the
brief is the only channel. Because the row exists before the spawn, a subagent that
crashes before its first command still has a row, and it goes silent at its lease.

For work the orchestrator runs itself (Codex reviews, `vm-run` tests), the orchestrator
is the runner and wraps the command, for example:
`devloop-state decoy run exec RUN --token T -- ~/bin/codex-review ...`.

When the subagent's completion notice arrives, the orchestrator runs `run show RUN`. If
the row still says `running`, the orchestrator ends it with `--as orchestrator --status
failed --summary "ended without a report"`.

### 5.4 Text for every builder, reviewer, tester and tech-writer brief

`skills/dev-loop/brief-template.md` and the decoy `LOCAL-BUILDER-BRIEF.md` get this
block, which the orchestrator fills in:

```text
Run record (required):
  RUN=r-20261127-0042  TOKEN=k3v9q2xa  PROJECT=decoy
- After each step, run: devloop-state decoy run log RUN --token TOKEN STEP --note "short plain note"
  Steps: plan-read, worktree-ready, build-started, commit, pushed, pr-opened, test-started,
  test-done, review-started, review-done, docs-written, blocked.
- Wrap any command that can run longer than 5 minutes (tests, vm-run, builds, codex):
  devloop-state decoy run exec RUN --token TOKEN -- <command>
- If no step has happened for 10 minutes, run: devloop-state decoy run beat RUN --token TOKEN
- Last action before your final message:
  devloop-state decoy run end RUN --token TOKEN --status done|blocked|failed
    [--verdict GO|GO-WITH-FIXES|NO-GO] [--head SHA] [--pr N] [--passed N --failed N]
    --summary "one plain sentence"
- Exit 4 means the run is no longer yours: stop and say so. Exit 5: retry once, then go on.
- Notes and summaries: plain words. No secrets, data values, file paths or command output.
```

## 6. Leases

- **Default lease:** 20 minutes for every role. That is 2 times the 10-minute idle beat in
  the brief, and 20 times the `exec` beat interval. `run start` sets it from
  `project.json invariants.lease_default_s`. The orchestrator may pass `--lease` from
  5 minutes to 4 hours.
- **Deadline:** the start time plus `runs.KINDS[kind].deadline_s` (builder 4 h, reviewer
  1.5 h, tester 2 h, docs 1 h). After the deadline the run stays `running`, and
  invariant I9 reports it as overdue. The loop never kills a subagent by itself.
- **What extends a lease:** `beat`, `log`, `hold` (one long step, capped at the
  deadline), and `exec`, which beats every 60 s while its child lives. A 40-minute t1 on
  the Mac under `exec` never looks dead. When the subagent's Bash call returns at its
  10-minute limit and the command keeps running in the background, `exec` keeps beating.
- **On expiry:** `run sweep`, which runs inside every tick, does all of this in one
  transaction: it sets `state=silent`, logs `silent`, opens problem `lease:<run>` with
  source `lease`, and records the alert. `silent` is not terminal. A late beat revives the
  run, logs `revived` and closes the problem. Only `run abandon` (orchestrator) ends a
  silent run, and after that its beats get code 4. That is the fencing rule.
- **How the orchestrator is told, in live mode:**
  1. `devloop-tick decoy`, which the orchestrator runs at the start of each iteration,
     prints `SILENT run=<id> role=<role> slice=<key> last_beat=<n>m` lines to stdout.
     Today the live pass prints nothing.
  2. STATUS.md gets a "Runner silent" section at the top.
  3. The dashboard shows silent runs under "Needs you". dash `view.py` reads
     `run list --silent`.
  4. Optionally, the orchestrator keeps `run watch` running under the Monitor tool, so
     a silent run interrupts it between ticks.

  Slack gets nothing, because the owner asked for the orchestrator to be alerted. In
  headless mode the tick's `_alert` path posts once per run, with its existing six-hour
  dedupe.

## 7. Invariants

Each invariant is one entry in `state/invariants.py: INVARIANTS`: a name, a check
function, the data it needs, and `needs_github`. A failure upserts problem
`inv:<name>:<subject>` with source `invariant`. A pass closes it. The tick prints each
failure as `FAIL <name> <subject>`, STATUS.md lists the failures, and the first sighting
of a new failure alerts once.

| ID | Name | Catches | Data | GitHub | Hits on 2026-11-27 |
|---|---|---|---|---|---|
| I1 | `plan-in-build-phase` | a plan slice in DEVELOP, SELF_CHECK, VERIFY, GATE or DOCUMENT | slices | no | 9 slices over 2 days |
| I2 | `document-without-shipped-entry` | DOCUMENT or DONE with a merged PR, more than 2 h after the merge, and no `<repo label> #<pr>` line in `shipped_doc` on the goal repo's `origin/main` | slices, `git show` | no | #817, #791, #832 |
| I3 | `claim-without-run` | `next_action` names a role "(subagent)", no `running` run exists on the slice, and the claim is older than the default lease | slices, runs | no | the five 18:37 tech-writer slices |
| I4 | `waiting-on-answered-question` | `waiting_kind='owner-question'` and the question is answered or defaulted. Before phase 2: `waiting_on` or `next_action` says "owner" or "question" and names a key that is the prefix of an answered question | slices, questions | no | NONFK-OOM--C1-docs |
| I5 | `gate-branch-lacks-main` | GATE, or REVIEW with a GO, where `origin/<base>` is not an ancestor of `origin/<branch>` (`git merge-base --is-ancestor`) | slices, local git after a fetch | no | DATE-SHIFT-TYPED--S1b-1 |
| I6 | `pr-state-mismatch` | a slice before DOCUMENT whose PR is merged or closed, or a slice in DOCUMENT or DONE whose PR is still open | slices, GitHub | yes | not checked yet |
| I7 | `head-mismatch` | the slice `head` differs from the PR's `headRefOid`, so reviews were recorded on an old head | slices, GitHub | yes | not checked yet |
| I8 | `runner-silent` | lease expired (the same check as `run sweep`) | runs | no | starts with S3 |
| I9 | `runner-overdue` | `running` past `deadline_at` | runs | no | starts with S3 |
| I10 | `illegal-move` | a `slice-moved` event since the last tick that is not in the live move table, or a move recorded with `override` | events | no | 100 of 722 |
| I11 | `held-without-question` | HELD, or PARKED for an owner reason, with no open `hold:<key>` question; or a hold that was answered but is still held after one tick | slices, questions | no | not checked yet |
| I12 | `stale-slice` | an active slice that is not waiting, with no move, no run and no log row for 12 h | slices, runs | no | not checked yet |
| I13 | `ended-run-not-applied` | a run ended `done` more than 30 minutes ago and its slice is still in the phase the run started in | runs, slices | no | starts with S3 |

**How often:** every tick. In live mode that is every orchestrator iteration, because
`devloop-tick` already runs first. In headless mode it is the cron tick.
`devloop-state decoy invariants` runs them on demand.

**Cost:**

- Local checks: reading about 77 slice files and about 2,600 events takes under 1 s.
- I2 and I5 need one `git fetch` per repo per tick, 1 to 3 s each. A fetch younger than
  5 minutes is reused (stamped in `tick-state.json`, and in `settings` after phase 2).
  After the fetch, `git show` and `merge-base` take about 20 ms per slice.
- I6 and I7 make **one** GraphQL call per repo per tick. It uses aliases, one
  `pullRequest(number: N) { state merged headRefOid baseRefName }` per open slice PR, and
  costs about 1 point of the 5,000-point hourly budget. Results are cached for 10
  minutes, so a busy hour costs about 12 calls per repo. With `--no-github`, or when `gh`
  fails, I6 and I7 report "unchecked", never "pass".
- A check that raises an error is reported as `FAIL invariant-error <name>`, and the
  other checks still run.

## 8. Concurrency, crash safety and migration

### 8.1 Concurrency

- **Writers:** up to 16 runs, the orchestrator, the tick and an operator shell. Each
  command opens its own connection with `isolation_level=None`, then sets
  `journal_mode=WAL` (persistent), `busy_timeout=5000`, `foreign_keys=ON` and
  `synchronous=FULL`. Every write is one `BEGIN IMMEDIATE ... COMMIT` inside
  `db.write()`. Each transaction holds the lock for well under 10 ms. At about 1 write
  per second at peak, waits are rare and short.
- **Readers:** the dashboard and STATUS.md open the file with a `file:...?mode=ro` URI,
  so a write through a reader fails. They read in one short transaction per request and
  never keep a read open while a page builds. This prevents checkpoint starvation. The
  dashboard server opens one connection per thread.
- **Checkpoints:** the auto-checkpoint stays at its default. The tick runs
  `PRAGMA wal_checkpoint(TRUNCATE)` when the WAL is over 8 MB.
- **What replaces the lock files** (phase 2, slice 6): the database's own lock replaces
  the locks for slices, events, problems, lessons, alerts, `seq.json` and
  `runs.index.json`. These locks stay: `tick.lock` (one tick at a time; dash-mc's
  `tick.running` reads it from `/proc/locks`), `dash.lock`, `admit.lock` (headless
  admission covers a process launch), and the locks for the vault pages
  (`questions.md.lock`, the STATUS page), because those files stay files.
- **Remote nodes:** WAL needs all writers on one host, and all writers run on the devbox.
  Mac and Dell jobs start from the devbox through `vm-run`, under `run exec` on the
  devbox. A future remote worker reports with `ssh devbox devloop-state ...`.

### 8.2 Crash safety

- A command killed during a write leaves no partial row (SQLite atomic commit). A runner
  killed during its work stops beating and goes silent at its lease.
- A reboot leaves the `-wal` and `-shm` files. The next connection recovers. The state
  directory is never copied or cleaned without its `-wal` file.
- `db backup` uses `sqlite3.Connection.backup` to write `backups/devloop-YYYYMMDD.db`.
  The tick runs it once a day and keeps 7 copies. `devloop-state check` adds
  `PRAGMA quick_check`.
- Schema newer than the code (after a code rollback past a migration): every command
  exits 5 with "schema newer than this code". The rollback steps in 8.4 cover this.

### 8.3 Migration (phase 2)

Expand, migrate, verify, contract (data-and-migrations rulebook):

1. **Expand (S5).** Migration 2 creates the phase 2 tables. `db import --dry-run` prints
   counts and the first difference. `db import --apply` imports in one transaction per
   source:
   - `slices/*.json`: each file is checked with `slices._validate`. Known fields go to
     columns, the rest go to `extra`, and the `history` entries go to `slice_moves`.
   - `events.jsonl`: every line goes to `events`, with the raw object in `fields` and the
     line number in `source_line`. A line that does not parse is stored with
     `kind='unparsed'` and its text, so nothing is dropped. The file's sha256 goes to
     `import_log`.
   - `problems.json`, `lessons.json` and `alerts.json` go to their tables.
     `OWNER-QUESTIONS.md` goes to `questions` through `notes.load_questions`.
   - `runs/*` go to `runs` rows with `run_dir` set. The state comes from the `exit`,
     `applied`, `rejected` and `released` files. Their tokens are random, and these rows
     never beat.

   The import is idempotent. Events key on `source_line`, and slices on the key plus the
   canonical digest. A second run adds zero rows.
2. **Shadow (S5, at least 2 days of live use).** JSON stays the source of truth. Each tick
   runs an incremental import and then `db reconcile`. The reconcile compares per-slice
   canonical digests (the JSON against the database rendered back to JSON), event counts
   per kind, and the sets of problems and questions. A difference opens problem
   `inv:db-drift:<subject>`. The flip needs 2 days in a row with zero drift.
3. **Flip (S6).** `project.json "store": "sqlite"`. The four read seams
   (`slices.load_all`, `status.events`, `runs.iter_runs`, `notes.load_questions`) and
   every writer in `slices.py`, `notes.py` and `store.append_event` switch to the
   database. The dashboard and STATUS.md do not change, because they call the seams. For
   7 days, `db export` rewrites the JSON files after each tick, as a read-only mirror.
4. **Contract (S7).** After 7 days with no rollback, the export stops. The JSON files and
   their locks move (they are not deleted) to `archive-YYYYMMDD/` in the state directory.
   Deleting the archive later is a separate R3 step that needs the owner's
   authorization, and it is not in this plan.

### 8.4 Rollback

- **Before the flip:** delete nothing. Removing `devloop.db*` returns the system to its
  earlier state, except for the run rows written since S3. `db export` first writes a
  copy of them to `runs-export.jsonl`.
- **After the flip, within 7 days:** set `"store": "json"`. The mirror is at most one tick
  old, and `db export` runs once more during the flip back, so writes since the last tick
  are included. The runs table stays in use, as in phase 1.
- **Code rollback past a migration:** run `db backup`, then `db downgrade --to N`, then
  install the older lib. Each migration has one downgrade step, which drops only the
  tables that migration added. Each downgrade has a test.
- **After the contract step:** rollback means restoring the files from
  `archive-YYYYMMDD/`. The plan states this path but tests it only through the S7 archive
  test.

### 8.5 Keeping old commands working

Every existing `devloop-state` command keeps its name, arguments, output and exit codes.
A parity test runs the state CLI tests against both backends through a `store_backend`
fixture. The dashboard's own tests also run against both backends.
`devloop-state event KIND k=v` stays, and its values go through the text rule.

## 9. Value-free rule

**Rule:** no secrets, data values or long paths in stored text. It is enforced where the
text enters (the command boundary), and again at the database write.

- **Choke point:** `state/text.py` holds one registry,
  `COLUMNS = {(table, column): TextRule(limit, kind)}`, with the kinds `note`, `summary`,
  `reason`, `token`, `path` and `json`. Two places use it:
  1. Every free-text CLI argument has `type=text.arg(table, column)`, so argparse rejects
     bad text before any other code runs (exit 3, and the message names the field only).
  2. `db.write(table, row)` runs the same rule on every TEXT column, so a code path that
     skips the CLI still cannot store bad text.
- **What is refused (kinds `note`, `summary`, `reason`):** text over the limit (note 120,
  summary 200, reason 300, next_action 300); newlines and control characters; the secret
  shapes in `scrub.py` (`_TOKEN`, `_URL_CRED`, `_LONG`); absolute or home paths with
  three or more parts (`_PATH`); email addresses; runs of 9 or more digits (IDs, card and
  phone numbers); quoted spans over 20 characters (pasted values). The text is refused,
  not scrubbed, so the record stays true and the agent learns the rule.
- **Kind `path`** (`worktree`, `plan_path`, `run_dir`): the value must resolve under an
  allowed root (the repos in `project.json`, the state directory, the vault). Path
  values are never shown on public pages. `status.py` already reads no free text.
- **Imported history:** old text is stored as it was, because an import must not change
  the record. A row whose text fails the rule gets `text_ok=0`, and readers pass that
  text through `scrub()`.
- **Guard test (fitness function):** it walks the built argparse tree of `devloop-state`
  and fails on any `str` argument without a `text.arg` type. It also walks the reflected
  schema (`PRAGMA table_info` for every table) and fails on any TEXT column missing from
  `COLUMNS`. **Mutation:** remove `type=text.arg(...)` from `run end --summary`; the guard
  must go red. Adding a TEXT column to `runs` without a registry entry must also turn it
  red.

## 10. Slices, branches, acceptance tests and mutations

### 10.1 Branch rules (owner, 2026-11-27)

- Every slice branches from the plugin's `origin/main` and merges back to `main` when it
  is done. Nothing stays on a side branch.
- One slice is open at a time, in order. S1 and S2 may run in parallel, because they
  touch different files. A later slice branches from `main` after the earlier one merges.
- Merges go through PRs, under the standing merge authority (double gate GO for R2).
  `devloop-install` from `main` happens only after slice 0b has landed (section 10.2),
  because before that, main lacks code that the running install depends on.
- Commits use Conventional Commits with the final line `cam`. T0 runs per edit, T1 per
  commit and on the slice head, T2 at each review or gate. A full plugin suite runs on
  the merge head for S0 (merge of a long branch, test config), S3 (schema), S5 (migration)
  and S6 (serialization). Tests run through `~/bin/pytest-one`.
- Close-out per slice (local operations profile): the plugin has no CHANGELOG or shipped
  log, so each slice updates the README command docs and this plan's status line before
  the merge. The plugin is not a standing graphify corpus, so there is no graph refresh.
  The section-complete Slack message goes out after each merge.

### 10.2 Slice 0: land the loop code on main

**What:** merge `feat/dash-mission-control` into `main`.

**Is it safe to land as is? Not on its own.** The merge itself has few conflicts. A trial
merge of `origin/main` into dash-mc (2026-11-27, in a scratch worktree) conflicts in only
two files: `config.json` and `tests/test_rules_gate.py`. `README.md` and
`hooks/rules_gate.py` merge automatically but need a read. The risk is elsewhere: dash-mc
is **not** the code that runs today. The installed lib also carries `feat/docs-skip-qa`
(22 commits, including the PR-create guard) and the `feat/full-suite-batches` work. If
main gets only dash-mc and someone reinstalls from main, the live loop loses those
features.

**Smallest safe path:**

- **S0a. Land dash-mc.**
  1. Push the 9 unpushed dash-mc commits to `origin/feat/dash-mission-control`.
  2. In a fresh worktree of dash-mc, merge `origin/main`. Resolve `config.json` (keep both
     sets of keys) and `tests/test_rules_gate.py` (keep both sets of tests). Commit as
     `chore(merge): bring main's rules-gate change into dash-mc`.
  3. Run the full plugin suite and the rules-gate tests on that head. Run the dashboard
     against a read-only copy of the live state directory (`DEVLOOP_HOME` set to the
     copy).
  4. Open a PR to `main` and merge it with a **merge commit: keep the history, do not
     squash**.
- **S0b. Land the branches the install depends on.** In order: `feat/docs-skip-qa`, then
  `feat/full-suite-batches`, then `feat/t1-batch` only if a diff shows the installed lib
  uses it. Each one merges `main` in, runs its T1 and the full suite, and merges through a
  PR with a merge commit.
- **Done when:** `diff -r` of `devloop/` on `origin/main` against the installed lib shows
  only the expected dashboard files (`dash/actions.py`, `dash/live.py`,
  `dash/render_*.py`). Then reinstall from main. The next tick and
  `devloop-state decoy check` must pass.

**Why keep the history:** `feat/docs-skip-qa`, `feat/full-suite-batches`,
`feat/t1-batch`, `restore-slice-runner` and `feat/push-gate-s1` all share the 131
headless-loop commits. A squash gives main one new commit with none of those commits as
parents, so each of those branches would conflict over about 34,600 lines when it lands.
A merge commit lets them merge cleanly. Some old subjects (for example "board: refresh
...") do not follow Conventional Commits. They are history and are not rewritten. The new
merge commits follow the rule.

**Review scope:** the full 175-commit diff (136 files, about 34,600 lines) was built
slice by slice under the loop's own gates on that branch, and it runs live today. A fresh
full review is not proportionate. This slice reviews instead:

- (a) the merge resolution diff;
- (b) the 9 unpushed commits, as a delta;
- (c) a T2 with the full suite on the exact merge head;
- (d) the install-parity diff above.

The reviewer is Codex. This narrows the review of an R2 artifact, so it needs an
exception record with the owner as approver: owner question Q1.

**Acceptance:** the full suite has no new failing id on the merge head. `git merge-base
--is-ancestor` shows both parents are in the merge. The parity diff is clean. The live
dashboard keeps running from its worktree during and after the merge, because it reads
state files, not git.

**Size:** about 50 lines of conflict resolution, plus the S0b merges.

### 10.3 Build slices

| Slice | Phase | Risk | Size (source + tests) | Depends on |
|---|---|---|---|---|
| S1 Invariants, report-only, on today's store | 1 | R1 | 300 + 300 | S0 |
| S2 Live move table and slice `kind` | 1 | R2 | 150 + 220 | S0 |
| S3 Run store, run commands, leases | 1 | R2 | 650 + 600 | S1 |
| S4 Briefs, skill, STATUS.md and dashboard readers for runs | 1 | R1 | 250 + 150 | S3 |
| S5 Phase 2 schema, import, export, shadow reconcile | 2 | R2 | 600 + 500 | S4, and 2 days of S3 data |
| S6 Flip to SQLite, constraints, retire the database-table locks | 2 | R2 | 500 + 400 | S5, with 2 clean shadow days |
| S7 Contract: stop the mirror, archive the JSON | 2 | R2 | 80 + 80 | S6, plus 7 days |

#### S1. Invariants, report-only (R1)

**Behavior:** `state/invariants.py` with I1 to I7 and I10 to I12 (I8, I9 and I13 come in
S3); the command `devloop-state <p> invariants`; a tick hook (`tick._pass` runs the
checks after `collect`, in every mode, live included); a STATUS.md section "Invariants";
`FAIL` lines on the tick's stdout; problems with `inv:` keys; and `docs/CODEMAP.md`.

**Failure modes:** `gh` is missing or fails, so I6 and I7 report `unchecked`, never pass.
A fetch fails, so I2 and I5 report `unchecked`. A check raises an error: it reports
`invariant-error`, and the other checks still run. The questions page is missing: I4 and
I11 report `unchecked`.

**Acceptance tests:**

- `test_inv_plan_in_build_phase_fires` (fixture: an `X--plan` slice in DEVELOP)
- `test_inv_document_without_shipped_entry_fires` and
  `test_inv_document_with_shipped_entry_passes` (fixture: a git repo with
  `docs/RECENTLY-SHIPPED.md`)
- `test_inv_claim_without_run_fires` (the 18:37 text and no live run)
- `test_inv_waiting_on_answered_question_fires` (the NONFK-OOM text and an answered
  question)
- `test_inv_gate_branch_lacks_main_fires` (a temp repo where the branch is behind main)
- `test_inv_pr_state_mismatch_fires` and `test_inv_head_mismatch_fires` (a fake `gh` on
  PATH)
- `test_github_failure_reads_unchecked_not_pass`
- `test_check_exception_does_not_stop_others`
- `test_problem_closes_when_invariant_passes`
- `test_every_invariant_has_a_firing_test` (guard: walks `INVARIANTS` and requires a
  `test_inv_<name>_fires` test for each entry)
- `test_live_state_copy_finds_todays_four_cases` (golden test on a value-free copy of the
  2026-11-27 slices and questions, checked into `tests/fixtures/`)

**Mutations that must go red:** I5 returns no failures; the GitHub error path returns
pass; one test named by the registry guard is deleted.

#### S2. Live move table and slice kind (R2)

**Behavior:** `state/phases.py` defines `LIVE_MOVES[kind]`. It holds the headless
`TRANSITIONS` plus the moves the live process really uses:

- REMEDIATE to REVIEW, VERIFY to GATE, and REMEDIATE to GATE (re-review after a fix);
- GATE to VERIFY and GATE to REMEDIATE (main moved, test again);
- FRAME to DEVELOP (an R0 or R1 slice with an inline plan);
- for plan slices only: PLAN to REVIEW, REVIEW to REMEDIATE, REMEDIATE to REVIEW, and
  REVIEW to DONE.

Plan slices are refused DEVELOP, SELF_CHECK, VERIFY, GATE and DOCUMENT. DOCUMENT to
DOCUMENT is refused, because it records no fact; use `slice set` for that. In live mode,
`_move` checks `LIVE_MOVES` instead of skipping the table. It still skips the
run-evidence checks. A refused move that really happened goes through
`slice move --override "reason"`. That records the move with `override=1` and an event,
and I10 reports it. A gap in the table is then visible, and is fixed by a PR to
`phases.py`. The slice JSON goes to `schema_version` 2 with `kind` (default from the key,
section 4). Version 1 files load with the derived kind. `slice set --kind` corrects it.

**Failure modes:** the table misses a real move, and the orchestrator is refused. That is
what `--override` is for, and the override stays visible. A version 1 file appears after
the upgrade: it loads with the derived kind. A kind is guessed wrong: `slice set --kind`
fixes it.

**Acceptance tests:**

- `test_live_plan_slice_refused_develop` (today's case)
- `test_live_moves_cover_observed_history` (replays the 722 moves from the fixture copy;
  each one is accepted, or is on the list of intended refusals)
- `test_override_records_event_and_flag`
- `test_v1_file_loads_with_derived_kind`
- `test_every_pair_outside_table_refused` (enumerates `PHASES x PHASES` for each kind)
- `test_headless_table_unchanged`

**Mutations:** add DEVELOP to the plan-kind set; put the live bypass back in `_move`;
make `--override` skip the event.

#### S3. Run store, run commands, leases (R2)

**Behavior:** `state/db.py` (connect, pragmas, `write()`, `read()`, migrations by
`user_version`), `state/schema.py` (migration 1: `migrations`, `runs`, `run_log`,
`run_seq`), `state/text.py` (the registry and its guard), `state/runlog.py` and
`state/run_cli.py` (every command in 5.2 except `db ...`), `run sweep` in the tick, and
invariants I8, I9 and I13. Run ids come from `run_seq`.

**Failure modes:** the database is locked for more than 5 s (exit 5; the runner goes on);
the disk is full (exit 5); the schema is newer than the code (exit 5); the token is wrong
(exit 4); a writer already holds the slice (exit 3); a signal kills the `exec` child (its
exit code passes through as 128+n, the beater stops, and the run stays `running` until
`end` or the lease); the beater outlives the parent (it beats only while the child pid
lives, checked by pid and start time as `runs.proc_start` does).

**Acceptance tests:**

- `test_start_prints_id_and_token`, `test_start_refuses_role_phase_mismatch`,
  `test_start_refuses_second_writer`
- `test_beat_extends_lease`, `test_wrong_token_exit_4`, `test_abandoned_run_beat_exit_4`
- `test_sweep_marks_silent_and_opens_problem`,
  `test_late_beat_revives_and_closes_problem`
- `test_exec_beats_while_child_runs` (the child sleeps 3 s with a 1 s beat interval set by
  a test hook; at least 2 beats are recorded), `test_exec_passes_exit_code`
- `test_end_requires_verdict_for_reviewer`, `test_end_is_idempotent`
- `test_concurrent_writers_no_busy` (16 processes, 200 beats and logs each, in parallel;
  zero exit-5 results, and the row count matches)
- `test_reader_cannot_write` (a write on a `mode=ro` connection raises an error)
- `test_crash_mid_write_leaves_no_partial_row` (a test hook kills a writer with SIGKILL
  inside `BEGIN IMMEDIATE`; after reopening, the row is absent and `quick_check` passes)
- `test_schema_newer_than_code_exit_5`
- `test_text_guard_walks_parser_and_schema`, plus one refusal test per text rule,
  parametrized
- `test_no_sqlite_connect_outside_db_module` (AST walk of `devloop/`)
- `test_queries_bind_parameters` (AST walk: no f-string or `%` formatting in an
  `execute` call)

**Mutations:** use `BEGIN` instead of `BEGIN IMMEDIATE` (the concurrency test must fail);
remove `foreign_keys=ON`; make the sweep skip rows past their lease; remove the token
check in `beat`; remove the text type on `--summary`.

#### S4. Briefs, skill, STATUS.md and dashboard (R1)

**Behavior:**

- The block in 5.4 goes into `skills/dev-loop/brief-template.md` and the decoy
  `LOCAL-BUILDER-BRIEF.md`.
- `dev-loop-ui/SKILL.md` gets a "Runs" section: start before you spawn, use `exec` for
  Codex and tests, run `run show` when a completion notice arrives, and handle silent
  runs.
- STATUS.md gets the sections "Runner silent" and "Live runs".
- dash `live.py` adds live-run cards from `run list --live --json`, next to the headless
  run cards. dash `view.py` adds silent runs to "Needs you".
- `orchestrator-loop.md` gets one line: act on the `SILENT` and `FAIL` lines the tick
  prints.

**Failure modes:** the database is missing (a project that has not run S3 yet): the
sections say "no run store yet" and the page still renders. The runs table cannot be
read: the existing dash `ctx.bad` path shows "Runs unreadable".

**Acceptance tests:**

- `test_status_lists_silent_runs`
- `test_dash_live_card_from_run_row`
- `test_dash_renders_without_db`
- `test_brief_template_carries_run_block` (the template renders with a run id and a
  token)
- `test_skill_mentions_every_runner_command` (guard: every `run` subcommand that an agent
  uses appears in SKILL.md and in the brief)
- One live slice end to end before S5 starts: start, log, exec, end, recorded in the
  slice's review evidence.

**Mutations:** remove the silent section from the STATUS render; remove `exec` from the
brief template.

#### S5. Phase 2 schema, import, export, shadow (R2)

**Behavior:** migration 2 (every phase 2 table, the `phase_moves` rows generated from
`phases.py`, and the triggers); the commands `db import`, `db export`, `db reconcile`,
`db backup` and `db downgrade`; and the tick's incremental import and reconcile while
`store` is `json`.

**Failure modes:** an event line that does not parse is stored as `unparsed`, never
dropped. An invalid slice file is skipped with problem `import-bad-slice:<stem>`, and the
dry run lists it. A reconcile difference opens `inv:db-drift`. A second import adds
nothing. An interrupted import is completed by a rerun, because each source is one
transaction.

**Acceptance tests:**

- `test_import_roundtrip_is_lossless` (on the fixture copy: import, then export; the
  canonical digests are equal for every slice, events are byte-equal after `sort_keys`,
  and the counts per kind are equal; the fixture includes every event shape in the real
  2606 events)
- `test_import_is_idempotent`
- `test_unparsed_event_kept`
- `test_constraints_refuse_plan_in_develop`, `test_trigger_refuses_illegal_move`,
  `test_fk_refuses_unknown_repo`
- `test_append_only_triggers`
- `test_downgrade_each_migration` (upgrade, then downgrade; the schema equals the
  earlier version)
- `test_reconcile_detects_drift` (change one JSON field after the import; drift is
  reported)

**Mutations:** make the importer skip `unparsed` lines; remove the plan-kind CHECK; make
the reconcile compare counts only (the field-change test must fail).

#### S6. Flip to SQLite (R2)

**Behavior:** `project.json "store": "sqlite"` switches the four read seams and every
writer. The JSON mirror is exported after each tick for 7 days. The locks for the
database tables are no longer taken. `store.append_event` writes rows. `slice move` runs
an `UPDATE` under the trigger. `slice set` gets `--waiting-kind` and `--waiting-ref`.

**Failure modes:** the database is unavailable after the flip (exit 5; the tick reports
it; rollback is a flag change). A mirror export fails (problem `db-mirror`; the flip-back
path still has the last good mirror). A writer still writes JSON (guard: an AST walk
finds no `write_json` to a state path outside `state/export.py`).

**Acceptance tests:**

- `test_cli_parity_both_backends` (every state CLI test, parametrized by backend)
- `test_dashboard_parity_both_backends` (the dash view built from the fixture is equal,
  field by field, on both backends)
- `test_flip_back_restores_json_current` (write on sqlite, flip to json; the JSON has the
  write)
- `test_no_json_state_writes_when_sqlite`
- `test_status_md_identical_both_backends`

**Mutations:** leave one writer (`notes.problem`) on JSON; skip the final export on the
flip back.

#### S7. Contract (R2)

**Behavior:** after 7 days with no rollback, `db export` stops, and the JSON files and
their lock files move to `archive-YYYYMMDD/`. `devloop-state check` warns when JSON state
files appear again.

**Acceptance tests:** `test_archive_moves_not_deletes`, `test_check_warns_on_stray_json`.

**Mutation:** make the archive step `unlink` the files (the test must fail).

### 10.4 Enumerations

Line citations are to the installed lib (`~/.local/share/devloop/lib/devloop/`) on
2026-11-27.

#### Exit paths

- Refused by a rule: exit 3, from the `REFUSALS` handler at state_cli.py:295.
- Lease lost (ended, abandoned, wrong token): exit 4. Only new commands.
- Store unavailable (busy past 5 s, disk full, schema too new): exit 5. Only new commands.
- `run exec` child exits or is killed: its own code, or 128 plus the signal.
- A runner that crashes with no `end`: no exit to the store. The lease sweep marks it
  silent, and the orchestrator ends it with `--as orchestrator`.
- The tick fails part way: each sweep and each invariant check is its own transaction, so
  a failure leaves earlier results committed.

#### Routes

- Headless: the dispatcher writes run directories, `apply.collect` (apply.py:43) reads
  them, and the phase 2 import turns them into rows.
- Live: the orchestrator runs `run start`, and the subagent reports through `run log`,
  `exec` and `end`.
- Orchestrator-owned work (Codex reviews, `vm-run` tests) goes through `run exec` on the
  devbox.
- Phase 1 slice moves go through `slices._move` (slices.py:880), whose live bypass is at
  slices.py:909. Phase 2 moves go through the same function into the `UPDATE` and the
  trigger.

#### FK shapes

- `run_log.run_id` to `runs.run_id` (many to one, phase 1).
- `runs.slice_key` to `slices.key` (many to one, nullable for duties, from phase 2).
- `slices.repo` to `repos.name`; `slice_moves.key` to `slices.key`;
  `slice_moves.run_id` to `runs.run_id` (nullable).
- `questions.slice_key` is not a foreign key, because a question may name a row or a key
  that no longer exists. Invariant I4 joins it by value.

#### Fields

- Run fields: section 4 `runs`. Each TEXT column is in the `text.py` registry, and the
  guard test fails on one that is not.
- Slice fields: today's `_FIELDS` in slices.py plus `kind`, `waiting_kind`,
  `waiting_ref` and `waiting_note`. The other fields go to `extra` unchanged.
- Event fields: the raw object in `fields`, with `kind`, `at`, `slice_key` and `run_id`
  as columns.

#### Output surfaces

- STATUS.md, built in `status.render_status` (status.py:110) from counts, IDs and fixed
  words only, and scrubbed per line.
- The tick's stdout (`SILENT` and `FAIL` lines): IDs, role names and minutes only.
- The dashboard: `view.py` and `live.py` on dash-mc. Free text shown there passes
  `scrub()`, and rows with `text_ok=0` always do.
- `run show --json` and `run list --json`: never print `token`.
- `REFUSED:` messages on stderr: they name the field and rule only, never the value.
- Slack: headless mode only, through `tick._alert` (tick.py:52) and its fixed words.

### 10.5 Defect classes

| # | Class | Where it can happen here | Structure that closes it | Sentry or property test | Mutation that must go red |
|---|---|---|---|---|---|
| 1 | Value-bearing output | notes, summaries and `REFUSED:` text | `text.py` registry used by argparse and `db.write` (section 9) | guard walks the parser and the schema; marker corpus through every output surface | remove `type=text.arg` from `run end --summary` |
| 2 | Cleanup missing on an exit path | the `run exec` beater thread and the child; partial imports | `exec` owns the child in one context manager; one transaction per import source | exit-path matrix: child exit, signal, parent kill, store busy, double end | skip stopping the beater on child exit |
| 3 | State machine and concurrency | run states, slice phases, many writers | written tables (`LIVE_MOVES`, run state CHECKs), `BEGIN IMMEDIATE`, the fencing token | `test_every_pair_outside_table_refused`; `test_concurrent_writers_no_busy`; beat against end interleavings | `BEGIN` instead of `BEGIN IMMEDIATE`; drop the token check |
| 4 | Route parity and silent fallback | headless against live runs; JSON against SQLite backend | the four read seams; `gh` failure reads `unchecked` | `test_cli_parity_both_backends`; `test_github_failure_reads_unchecked_not_pass` | make the GitHub error path return pass |
| 5 | Referential integrity | runs against slices; run_log against runs; slice repo | foreign keys with `foreign_keys=ON` on every connection | `test_fk_refuses_unknown_repo`; a log row for an unknown run is refused | remove `foreign_keys=ON` from `db.connect` |
| 6 | Untrusted producer | subagent text, imported history, `gh` JSON | bounded text rules; `text_ok=0` for import; `gh` output parsed into typed fields only | hostile corpus per text kind; a malformed `gh` reply gives `unchecked` | let the importer skip the `text_ok` check |
| 7 | Check then use on a path (TOCTOU) | the beater checks a pid then beats; db file path | the beater checks pid and start time together (`runs.proc_start`); one db path from `store.state_path` | `test_no_sqlite_connect_outside_db_module`; pid reuse test with a fake start time | check the pid only, not the start time |
| 8 | Weak tests and overclaimed evidence | golden tests on live copies; parity claims | every invariant has a firing test; the full suite on the exact merge head per 10.1 | `test_every_invariant_has_a_firing_test` | delete one firing test |
| 9 | Behavior-preserving refactor drift | the S6 flip of every reader and writer | characterization tests on the JSON backend first, then the same tests on SQLite | `test_dashboard_parity_both_backends`; `test_status_md_identical_both_backends` | leave `notes.problem` writing JSON |
| 10 | Frozen contract drift | the CLI, the event line, the slice file, STATUS.md | the list in section 3.5, additive only | snapshot tests from S1 | change an existing command's stdout format |

## 11. Failure modes across the plan

- Subagents forget to beat, false silent alerts pile up, and the orchestrator stops
  reading them. Mitigations: `exec` for long commands, a 20-minute lease, one alert per
  run, and a `revived` beat closes the alert.
- A beat fails because the store is busy, and a builder stops its work over
  bookkeeping. Mitigation: exit 5 means go on. Only exit 4 stops work.
- The move table is wrong, and the orchestrator is refused in the middle of an iteration.
  Mitigation: `--override`, which stays visible through I10.
- A long dashboard read blocks the checkpoint and the WAL grows. Mitigations: one short
  read per request, and a TRUNCATE checkpoint above 8 MB.
- The import silently drops history. Mitigations: `unparsed` rows, the `source_line`
  unique key, the round-trip digest test, and the 2-day shadow.
- The value-free rule refuses an honest note. The agent gets the field name and writes
  the note again. A real pattern miss is a follow-up change to `text.py`.

## 12. Pre-mortem

It is January and RUN-STATUS has failed. The likely reasons:

1. **The run block was in the brief, but subagents ignored it.** Every run went silent,
   and the alerts became noise. Check: S4 acceptance includes one live slice end to end
   before S5 starts. If more than 1 in 5 runs end with `--as orchestrator` in the first 2
   days, the brief is fixed before phase 2 starts.
2. **Main and the install drifted apart again.** A feature was built on a side branch, and
   the install came from that branch. Check: the branch rules in 10.1, and the S0 parity
   diff run again at each slice merge.
3. **Phase 2 changed what the dashboard shows.** Check: the parity tests in S6, and the
   7-day mirror.
4. **The invariants raised false alarms.** Before phase 2, I3 and I4 read free text, and
   they could fire on normal wording. Check: the S1 golden test on the real copy must
   show only the known cases. A false positive is fixed by a tighter pattern, never by
   removing the check.
5. **GitHub limits or `gh` auth broke I6 and I7.** Check: "unchecked" shows in STATUS.md,
   and the cost is one call per repo per 10 minutes.

## 13. Open questions

**Owner question (a process call):**

- **Q1. Slice 0 review scope.** Land dash-mc, then docs-skip-qa and full-suite-batches,
  with a review of the merge resolution, the 9 unpushed commits, a full-suite T2 and the
  install-parity diff, instead of a fresh review of all 175 commits (about 34,600
  lines). This narrows an R2 review, so the risk-and-exceptions rulebook needs the owner
  as approver.
  - (a) Approve the narrowed scope as stated. **Recommended:** the code was built and
    gated slice by slice, it runs live today, and a fresh full review would hold every
    RUN-STATUS slice for days.
  - (b) Require a fresh Codex review of the full diff before S0 merges.

**Technical choices made in this plan, open to reviewer challenge:**

- Run rows go to SQLite in phase 1 (section 3.1).
- `synchronous=FULL`, not NORMAL. The write rate is low, and FULL keeps today's
  durability.
- `silent` is not terminal, and the loop never kills a subagent by itself.
- The vault questions page stays the owner's input surface, and the database holds a
  copy.
- `evidence` and `plan_rounds` stay JSON columns.
