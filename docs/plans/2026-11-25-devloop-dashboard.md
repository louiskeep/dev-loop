---
Status: plan
updated: 2026-11-25
---

# Dev Loop Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop, reuse-first,
proportionality, architecture, cross-cutting-concerns, security, observability-and-resilience,
api-and-compatibility, testing, workspace-and-vcs, profiles/local-operations.

Risk: **R1** (read-only, local, reversible; no state writes except its own pid file). Plan
review: one round. Code review: Codex on the exact head. Repo `/home/cam/dev-loop-plugin`,
branch `feat/headless-loop`, planned from head `fff2419`.

**Goal (owner, 2026-11-25):** one clean, local, read-only web dashboard that every project
using the plugin gets as part of the standard loop. It shows recently shipped work, rows in
progress, the status of each slice, workers, capacity and everything else in progress.
Single user, LAN, no auth, binds `0.0.0.0`. Threat model: accidents only.

**Owner addition (2026-11-25, role names):** everything people read uses role labels, not
kind or persona names. Code kind names (`runs.KINDS`) stay the internal contract; only labels
change. Folded in as Task 1 (labels and config) and Task 7 (STATUS and Slack outside the
dashboard).

**Architecture:** a sub-package `devloop/dash/`. `view.py` holds `build_view(project) ->
dict`, the only reader of state for the dashboard, built from existing functions. `render.py`
turns that dict into HTML; it is the only renderer, used for `/` and for the `/fragment` the
page polls. `server.py` is a `ThreadingHTTPServer` with a fixed route table, ETags, and the
pid-file lifecycle. Static `dash.css` and `dash.js` sit in `devloop/dash/web/`, so the
existing `devloop-install` copy carries them. No framework, no build step.

**Tech stack:** Python 3.11 stdlib (`http.server`, `html`, `json`, `hashlib`, `socket`),
vanilla JS, CSS custom properties. Tests: pytest with the existing `home`/`proj` fixtures.

## Guarantee and non-goals

Guarantee: the dashboard never writes loop state, never shows an absolute path, a secret or a
commit id, shows the same counts STATUS.md shows, and keeps working (with an error item, not
a 500) when one state file is damaged.

Non-goals (leave out): auth, write actions, charts, drag and drop, logs and diffs,
websockets, a JS framework or build step, notifications, DNS-rebinding or hostile-LAN
defences (accidents-only threat model), server-side filtering, multi-project pages.

## Design notes

- Single choke point for public text: `view._seal(tree)` walks the finished view once. Each
  key has one declared kind in `VIEW_SCHEMA`: `id` (must fullmatch `store.TOKEN`, else
  `"?"`), `text` (through `scrub` with the declared limit), `at` (ISO timestamp or null),
  `url` (must fullmatch `https://github.com/<SLUG>/pull/<n>`, else null), `int`, `bool`,
  `label` (a role label from `project.role_label`, then `scrub`). An undeclared key raises
  `ViewError`. Sites never call `scrub` themselves, so a new field cannot skip it.
- One renderer (Python). The page polls `/fragment`, not the JSON, so there is no second
  renderer in JS to drift. `/api/state` carries the same version and ETag for scripts and
  tests. This departs from "page polls `/api/state`" in the research note on purpose.
- One place per fact: slot usage moves to `runs.slot_usage` (STATUS, snapshot and view read
  it); the event-field rule moves to `scrub.plain`; role and pool labels live only in
  `project.role_label` / `project.pool_label`; the stuck threshold is
  `snapshot.STALE_SECS`.
- Read-only by construction: the view never calls a function that creates a directory when
  the target is missing (`store.state_dir`, `runs._runs_dir` and `slices._slices_dir` all
  `mkdir`). It checks `is_dir()` first and reads slice files one by one through
  `slices.load`, catching `SliceError` per file.
- House style: typed context over long parameter lists, single-purpose functions, comments
  that explain why.

## Placement

The repo has no CODEMAP. `devloop/` holds 23 modules and the architecture fan-out cap is
about 25, so the new modules go in a named sub-package, not beside the others. This is why
the read model is `devloop/dash/view.py` and not `devloop/view.py`.

| New file | Directory | Why there | Sits beside |
|---|---|---|---|
| `devloop/dash/__init__.py`, `view.py`, `render.py`, `server.py` | `devloop/dash/` | fan-out cap; one concern per module like the parent | `devloop/status.py` |
| `devloop/dash/web/dash.css`, `dash.js` | `devloop/dash/web/` | shipped by `install_package`'s `devloop/` copy | `skills/dev-loop/` assets |
| `bin/devloop-dash` | `bin/` | launcher pattern of `bin/devloop-state` | `bin/devloop-state` |
| `tests/devloop/test_dash_*.py`, `dash_fixture.py` | `tests/devloop/` | existing test home | `test_snapshot_status.py` |

Expected size: about 8 new files (view about 260 lines, render 260, server 230, JS 180, CSS
160, tests 450), edits to `scrub.py`, `runs.py`, `status.py`, `snapshot.py`, `project.py`,
`check.py`, `SKILL.md`, `project.example.json`, `test_shipped_files.py`, `mutations.sh`. New
public names: `build_view`, `VIEW_SCHEMA`, `render_page`, `render_fragment`, `serve`,
`dash_status`, `lan_ip`, `main`, `runs.slot_usage`, `scrub.plain`, `project.role_label`,
`project.pool_label`.

## Established solutions

The owner's research note (Buildkite, Argo CD, GitHub Actions, Vercel, Linear, Blue Ocean,
Conductor, OpenHands, Grafana) sets the layout, card shape, colours, refresh and filter
rules; this plan follows it except the one-renderer change above. Conditional GET with
`ETag`/`If-None-Match` and `304` follows RFC 9110 section 13. Pausing polls on hidden tabs
uses the Page Visibility API. The LAN address uses the stdlib UDP `connect` trick (no packet
is sent). Liveness of the pid file reuses `runs.proc_start` and `runs.boot_id`, the same
check the run wrapper uses. No custom code beyond the view, renderer and route table.

## Reuse inventory

- `slices.load`, `slices.PHASES`, `slices.ACTIVE`, `slices.FORWARD`, `slices.OWNER_PARKS`.
- `evidence.exit_checklist(project, key)` for the "3/5 met" block.
- `runs.iter_runs`, `runs.holds_slot`, `runs.live_runs`, `runs.KINDS` for workers and slots.
- `notes.questions_path`, `notes.load_questions`, `notes.read_ticklog`, `problems.json`.
- `board.merge_records`, `board.closure_records` for recently shipped.
- `status.counts` for merged today, this week and rows closed.
- `scrub.scrub`, `scrub.scrub_lines`, `store.TOKEN`, `project.SLUG`.
- `snapshot.STALE_SECS` for "stuck".
- `install.install_package` and `install_bins`: unchanged, they already copy `devloop/**`
  and `bin/devloop-*`.
- Not reused: `snapshot._command_outputs` runs every command for 60 s. The dashboard runs
  only commands flagged `"dash": true`, at most once a minute, in a background thread.

## Frozen contracts

- STATUS.md section list, `snapshot` text and hash, `KINDS` names, event kinds: unchanged
  except the role-label wording in Task 7 (STATUS "Running" lines and the Slack tick line).
- `devloop-state check` output format `ok|FAIL: message`; the new line follows it.
- New contract, frozen by this plan: `/api/state` JSON with `schema: 1` and the keys in
  Fields below; routes `/`, `/fragment`, `/api/state`, `/static/dash.css`,
  `/static/dash.js`; CLI `devloop-dash PROJECT [--port N] [--host H] [--detach|--stop]`;
  pid file `dash.json` in the state dir.

## Enumerations

### Exit paths

- Foreground server stops on Ctrl-C or SIGTERM: the `finally` in `serve` closes the socket
  and removes `dash.json` only when its pid is ours.
- Bind fails (port in use, bad host): exit 3 with `port N in use`, no pid file written. The
  pid file is written after `bind`, never before.
- Project missing (no `project.json` under the state root): exit 2 before any `store` call,
  because `store.state_dir` creates the directory (`devloop/store.py:38`).
- SIGKILL or reboot: `dash.json` stays; `dash_status` reads it dead by pid, start time and
  boot id (`devloop/runs.py:133`, `devloop/runs.py:143`), and `--detach` replaces it.
- `--detach` child dies before writing the pid file within 5 s: exit 3, "see dash.log in
  the state dir".
- A request whose view build raises: 500 with fixed text "state unreadable"; the log line
  holds the exception class name only.
- Damaged slice file or questions page: no exit; a `needs_you` item of kind `unreadable`
  (`slices.load` raises per file, `devloop/slices.py:247`; markers raise in
  `devloop/notes.py:287`).
- Client: a failed poll increments a counter; two failures or 60 s without a good poll shows
  the stale banner; polling resumes on the next success.

### Routes

One table, `server.ROUTES`, maps exact paths to handlers. `do_GET` and `do_HEAD` are the
only methods; others get 501 from `BaseHTTPRequestHandler`.
- `GET /`: full HTML page, works without JS.
- `GET /fragment`: the sections only, with `ETag`; `304` on `If-None-Match` match.
- `GET /api/state`: the view JSON, same `ETag`.
- `GET /static/dash.css`, `GET /static/dash.js`: fixed bytes read at start, never a path
  join from the request.
- Anything else, including `/static/../x` and query-only variants of unknown paths: 404.
  Query strings are ignored by the server (filters are client side).

### FK shapes

References the view joins, each checked rather than trusted:
- run `request.key` to a slice file: a run whose slice is gone is listed under workers with
  no card link.
- merge ledger id `slug#pr@head` to `project.repos[].slug` (`devloop/board.py:181`): a slug
  not in config, or a non-integer PR, gets no URL.
- slice `repo` to `project.repos[].name`: unknown repo gets no PR URL.
- question key `hold:KEY` to a HELD slice: shown once, on the hold item.

### Fields

`VIEW_SCHEMA` (kind in brackets; text limits in characters):
- top: `schema`[int], `project`[id], `version`[id], `tick`, `counts`, `needs_you`, `rows`,
  `workers`, `nodes`[text 2000, nullable], `slots`, `shipped`, `ticks`, `parked`.
- `tick`: `last_pass_at`[at] (STATUS.md mtime; `write_status` runs on every pass outcome),
  `last_model_at`[at], `paused`[bool], `capped`[bool].
- `counts`: `needs_you`, `running`, `free`, `merged_today`, `merged_week`,
  `rows_closed`[int].
- `needs_you[]`: `kind`[id: question, hold, stuck, problem, unreadable], `key`[id],
  `owner`[label: "Product owner (you)" or the stuck step's role], `text`[text 200],
  `since`[at].
- `rows[]`: `row`[id], `oldest_at`[at], `stuck`[bool], `cards[]`.
- card: `key`, `row`, `slice`[id], `purpose`[text 120], `pr`[int], `url`[url], `risk`[id],
  `round`[int], `phase`[id], `phase_index`[int], `held`[bool], `since`[at], `owner`[label],
  `stuck`[bool], `checklist{met, total[int], items[{name[text 120], ok[bool]}]}` with
  missing items first.
- `workers[]`: `run`[id], `kind`[id], `role`[label], `key`[id], `node`[id, request host
  or "local"], `started_at`[at].
- `slots[]`: `pool`[id], `label`[label], `cap`, `used`[int].
- `shipped[]` (last 20, newest first): `kind`[id: merge, row-close], `row`, `slice`[id],
  `pr`[int], `url`[url], `at`[at].
- `ticks[]` (last 10): `at`[at], `text`[text 400]. `parked[]`: cards.

Not in the view at all: `worktree`, `plan_path`, `branch`, `head`, `repo` paths, run dirs,
briefs, `next_action`, `waiting_on`, `history`, `evidence` run ids.

### Output surfaces

- `/` HTML, `/fragment` HTML, `/api/state` JSON.
- CLI stdout: the Network URL `http://<lan-ip>:<port>/` and fixed-word errors.
- `dash.log` in the state dir: method, path without query, status code. No query, no body.
- `dash.json` pid file (0600, atomic `store.write_json`).
- `devloop-state check` line: `ok: dash: running at http://<lan-ip>:<port>/` or
  `ok: dash: not running`.
- STATUS.md and the Slack tick line (role labels only, Task 7).

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | `_seal` walk over `VIEW_SCHEMA`; free text only through `scrub`, ids through `scrub.plain` | `test_api_state_has_no_absolute_paths`, `test_view_scrubs_planted_secret` | return the tree from `build_view` without `_seal` |
| 2 | cleanup on every exit path | `serve` `finally` removes its own pid file; liveness by pid, start time and boot id | `test_sigterm_removes_pid_file`, `test_stale_pid_file_reads_dead` | delete the `finally` block in `serve` |
| 3 | state consistency (read-only) | view checks `is_dir()` before any `mkdir`-ing helper; no lock held past one read | `test_build_view_writes_nothing` (tree listing and mtimes equal, with `runs/` and `slices/` absent) | call `runs.iter_runs` without the `is_dir` guard |
| 4 | route completeness | `ROUTES` table; one `_send` sets CSP, nosniff, no-cache, no-referrer on every response | `test_only_known_routes_and_methods`, `test_every_route_sends_security_headers`, `test_static_traversal_404` | drop the CSP header line in `_send` |
| 5 | referential integrity | PR URL built only from config slug plus int PR; orphan runs keep no card | `test_merge_with_unknown_slug_has_no_url`, `test_orphan_run_listed_without_card` | build the URL from the ledger slug without the config check |
| 6 | untrusted input | `html.escape` in every render helper; damaged files become `unreadable` items; JS swaps parsed fragment nodes, never sets `innerHTML` from JSON | `test_html_escapes_planted_markup`, `test_corrupt_slice_file_is_an_item_not_500` | remove `html.escape` from `render._t` |
| 7 | TOCTOU (check then use) | pid file written after `bind`; a second server fails bind and leaves the first pid file; atomic writes mean reads see whole files | `test_second_server_same_port_keeps_first_pid`, `test_slice_deleted_mid_read_is_skipped` | write `dash.json` before `bind` |
| 8 | weak tests | every row's mutation added to `tests/devloop/mutations.sh` | `mutations.sh` reports each listed mutation killed | revert any one assertion in `test_dash_*.py` |
| 9 | refactor drift | `runs.slot_usage` and `scrub.plain` extracted; STATUS and snapshot call them | existing `test_snapshot_status.py` unchanged and green, `test_slot_usage_matches_status` | make `slot_usage` skip the `tester` slot |
| 10 | frozen contracts | `VIEW_SCHEMA` with `schema: 1`; `KINDS` names untouched, labels only via `role_label` | `test_view_keys_match_schema`, `test_kind_names_unchanged` | rename `phase_index` in `view.py` only |

## Acceptance tests

All in `tests/devloop/`, written red first. `dash_fixture.py` builds one state dir through
the public APIs (`slices.new_slice`, `slices.set_fields`, runs written with
`runs_support`, `board.record_merge`, `notes.ask`, `notes.ticklog`, `notes.problem`): two
rows, five slices across FRAME, DEVELOP, REVIEW, HELD and PARKED, one stuck, one live builder,
one test run, three merges, one closed row, one waiting question, one problem.

- `test_page_renders_from_fixture_offline`: `socket.socket.connect` and `getaddrinfo`
  patched to raise; `render_page(build_view("p"))` contains every section heading, the
  Needs you items, the row lanes, phase chips with `aria-current` on the current phase,
  "3/5 met"-style counts, PR links, and the shipped list.
- `test_api_state_has_no_absolute_paths`: fixture with absolute paths and a `ghp_` token
  planted in `purpose`, `next_action`, `waiting_on`, a question, a problem impact and a tick
  log entry; serve on `127.0.0.1:0`, GET `/api/state`; assert no `str(tmp_path)`, no match
  of `(?<![\w:/])/[\w.-]+/[\w.-]+` outside `https://` URLs, no `ghp_`, no 40-char hex.
- `test_etag_304_on_unchanged_poll`, `test_etag_changes_after_slice_move`.
- `test_needs_you_present_with_empty_state` ("Nothing needs you" when empty).
- `test_stuck_flag_after_stale_secs`, `test_cards_sorted_stuck_then_oldest`.
- `test_checklist_lists_missing_first`.
- `test_shipped_last_20_newest_first_with_urls`.
- `test_card_owner_is_role_label` (live builder shows "Developer"; HELD shows "Product owner
  (you)"; idle shows "Tech lead"), `test_slots_read_per_role_pool` ("Developers 1/8").
- `test_roles_config_overrides_and_validates` (unknown kind or empty label refused with the
  key path `roles.<kind>`).
- `test_build_view_writes_nothing`, `test_html_escapes_planted_markup`.
- `test_only_known_routes_and_methods`, `test_static_traversal_404`,
  `test_every_route_sends_security_headers`.
- `test_detach_writes_pid_and_prints_network_url`, `test_stop_kills_only_own_pid` (pid file
  pointing at an unrelated live process with a different start time is not signalled),
  `test_check_reports_dash_running_and_not_running`.
- `test_install_ships_dash_assets` (installed version holds `devloop/dash/web/dash.js` and
  `bin/devloop-dash`).
- `test_css_tokens_meet_aa`: parse `:root` and dark tokens, every text token on its
  background has contrast at least 4.5.
- `test_status_and_tick_line_use_role_labels`.
- `test_no_private_strings_in_shipped_files` extended to `devloop/dash/web/*`.
- JS behaviour (5 s poll, pause while hidden, refetch on focus, details and scroll kept,
  stale banner, filters in the URL, `/` focuses search) has no pytest runner. VERIFY runs it
  in a browser against the fixture server and records the steps and results; residual risk
  stated in the review packet.

## Slices

| Slice | Tasks | Ships | Gate |
|---|---|---|---|
| S1 read model and page | 1, 2, 3, 4 | role labels, `build_view`, renderer, assets, server | Codex on the S1 head; T1 = `pytest tests/devloop -k "dash or status or snapshot or project"` |
| S2 lifecycle and labels everywhere | 5, 6, 7 | CLI, detach and stop, check line, skill, install, STATUS and Slack labels | Codex on the S2 head; T1 = `pytest tests/devloop`; `mutations.sh` |

Low risk, so preparation folds into S1 and docs into S2. One PR is fine if review is short.

## Pre-mortem

- **A planted path or token reaches the LAN page.** `_seal` is the single walk; the leak
  test plants in every free-text source; undeclared keys raise.
- **Dashboard and STATUS disagree on running or free counts.** Both read
  `runs.slot_usage` and `status.counts`; `test_slot_usage_matches_status`.
- **Five-second polls from two tabs load the state dir.** The server caches the view for 2 s
  behind a lock; unchanged polls answer 304 with no body.
- **Port taken or old server left after a reboot.** Bind fails cleanly with exit 3;
  liveness by boot id; `--detach` is idempotent when a live server already runs.
- **Clock skew between devbox and viewer.** The client takes skew from the `Date` header of
  every response before computing time-ago.
- **A damaged file blanks the page.** Per-file catch turns it into an `unreadable` item.
- **JS regressions go unseen.** The JS stays small (swap fragment, timers, filters); the
  browser check at VERIFY is a named step, not optional.
- **Role labels drift between surfaces.** One function, `project.role_label`, used by view,
  STATUS and Slack.

## Task 1: Role labels and shared helpers

**Files:** modify `devloop/project.py`, `devloop/scrub.py`, `devloop/runs.py`,
`devloop/status.py`, `devloop/snapshot.py`, `skills/dev-loop/project.example.json`. Test:
`tests/devloop/test_dash_labels.py`.

**Interfaces:**
- `project.ROLE_DEFAULTS = {"planner": "Architect", "builder": "Developer",
  "codex-builder": "Developer (Codex)", "dennis": "Senior reviewer", "codex-review":
  "Outside reviewer", "codex-fix": "Outside reviewer", "test": "QA", "docs": "Tech writer",
  "duty": "Duty", "tick": "Tech lead", "owner": "Product owner (you)"}`.
- `project.POOL_DEFAULTS = {"builder": "Developers", "tester": "QA", "reviewer":
  "Reviewers", "planner": "Architects"}`.
- `role_label(cfg, kind) -> str`, `pool_label(cfg, slot) -> str`. Optional `roles` map in
  `project.json`: keys must be in `ROLE_DEFAULTS`, values non-empty single-line str of at
  most 40 characters; refused with `ConfigError("roles.<key>: ...")`.
- `scrub.plain(value) -> str` (moved from `status._plain`). `runs.slot_usage(project) ->
  dict[slot, int]` (moved from `status._running` and the loop at `snapshot.py:190`).
- Optional `dash: {port: int 1024-65535, host: str}` validated here too.

- [ ] Step 1: failing tests `test_roles_config_overrides_and_validates`,
  `test_slot_usage_matches_status`, `test_kind_names_unchanged`.
- [ ] Step 2: run `pytest tests/devloop/test_dash_labels.py -v`, expect FAIL.
- [ ] Step 3: implement; status and snapshot call the moved helpers with no output change.
- [ ] Step 4: run the file plus `test_snapshot_status.py`, expect PASS.
- [ ] Step 5: commit `refactor(devloop): share slot usage and add role labels`.

## Task 2: Read model `devloop/dash/view.py`

**Files:** create `devloop/dash/__init__.py`, `devloop/dash/view.py`,
`tests/devloop/dash_fixture.py`, `tests/devloop/test_dash_view.py`.

**Interfaces:** `build_view(project, *, node_text=None) -> dict`; `VIEW_SCHEMA`;
`ViewError`; `version_of(view) -> str` (first 16 hex of sha256 over canonical JSON without
`version`). Sources and rules exactly as in Fields. Needs you order: questions, holds
(HELD, owner parks, `merge_unknown`), stuck, problems, unreadable. Rows sorted by stuck,
then oldest `last_moved_at`; cards likewise. Phase chips use `PHASES[:10]`; a HELD or PARKED
card shows `parked_from` as current with `held: true`. Owner of the step: live run's role,
else "Product owner (you)" for HELD and owner parks, else the tick's role.

- [ ] Step 1: failing tests `test_page_renders_from_fixture_offline` (view part),
  `test_view_scrubs_planted_secret`, `test_build_view_writes_nothing`,
  `test_view_keys_match_schema`, `test_stuck_flag_after_stale_secs`,
  `test_cards_sorted_stuck_then_oldest`, `test_checklist_lists_missing_first`,
  `test_shipped_last_20_newest_first_with_urls`, `test_merge_with_unknown_slug_has_no_url`,
  `test_orphan_run_listed_without_card`, `test_corrupt_slice_file_is_an_item_not_500`,
  `test_slice_deleted_mid_read_is_skipped`, `test_card_owner_is_role_label`,
  `test_slots_read_per_role_pool`.
- [ ] Step 2: run, expect FAIL. Step 3: implement. Step 4: run, expect PASS.
- [ ] Step 5: commit `feat(devloop): add dashboard read model`.

## Task 3: Renderer and assets

**Files:** create `devloop/dash/render.py`, `devloop/dash/web/dash.css`,
`devloop/dash/web/dash.js`, `tests/devloop/test_dash_render.py`.

**Interfaces:** `render_page(view) -> str`, `render_fragment(view) -> str`. Sections top to
bottom: header strip, Needs you, In progress (lanes and two-line cards, checklist in
`<details>`), Workers and nodes, Capacity slots, Recently shipped, Tick log (collapsed),
Parked and held (collapsed). Times as `<time datetime>` with server text fallback. Each
state colours only its own chip and carries an icon and a word. Every `<details>` has a
stable `id` so JS can keep it open. Filters: one search box plus phase, needs-you, stuck and
hide-parked toggles, matched on `data-*` attributes, kept in the query string. CSS tokens on
`:root`, dark under `prefers-color-scheme`. JS: poll `/fragment` every 5 s with
`If-None-Match`, pause on `visibilitychange` hidden, refetch on focus, swap parsed sections
keeping open `details` ids and `scrollY`, stale banner rule, `Date`-header skew,
time-ago tick each second.

- [ ] Step 1: failing tests `test_html_escapes_planted_markup`, `test_css_tokens_meet_aa`,
  page assertions of `test_page_renders_from_fixture_offline`.
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): render the dashboard page`.

## Task 4: Server

**Files:** create `devloop/dash/server.py`, `tests/devloop/test_dash_server.py`.

**Interfaces:** `ROUTES`; handler with `do_GET`, `do_HEAD`, one `_send(code, body, ctype,
etag=None)`; `serve(project, host, port, *, ready=None)` (binds, writes `dash.json` after
bind, installs a SIGTERM handler that raises `SystemExit`, removes its own pid file in
`finally`); a 2 s view cache under a `threading.Lock`; a node thread that runs `"dash":
true` snapshot commands every 60 s with a 10 s timeout and passes `scrub_lines` output as
`node_text`. `log_message` writes method, path without query, and status only.

- [ ] Step 1: failing tests `test_api_state_has_no_absolute_paths`,
  `test_etag_304_on_unchanged_poll`, `test_etag_changes_after_slice_move`,
  `test_only_known_routes_and_methods`, `test_static_traversal_404`,
  `test_every_route_sends_security_headers`, `test_sigterm_removes_pid_file`,
  `test_second_server_same_port_keeps_first_pid`.
- [ ] Step 2 to 4: red, implement, green (server on `127.0.0.1:0` in a thread).
- [ ] Step 5: commit `feat(devloop): serve the dashboard`.

## Task 5: CLI and lifecycle

**Files:** create `bin/devloop-dash`; modify `devloop/dash/server.py` (`main`, `lan_ip`,
`dash_status`), `devloop/check.py`; test `tests/devloop/test_dash_cli.py`.

**Interfaces:** `devloop-dash PROJECT [--port 8791] [--host 0.0.0.0] [--detach|--stop]`.
Defaults from `project.json` `dash`, then flags. `--detach` starts the launcher with
`start_new_session=True`, output to `dash.log`, waits up to 5 s for a live pid file, prints
`Dashboard: http://<lan-ip>:<port>/`; already running prints the URL and exits 0. `--stop`
sends SIGTERM only when pid, start time and boot id match, waits 5 s, prints `stopped` or
`not running`. `lan_ip(host)` returns `host` unless it is `0.0.0.0`, else the UDP-connect
address, else the hostname address. `dash_status(project) -> (state, url|None)`. `check`
adds one `ok:` line.

- [ ] Step 1: failing tests `test_detach_writes_pid_and_prints_network_url`,
  `test_stop_kills_only_own_pid`, `test_stale_pid_file_reads_dead`,
  `test_check_reports_dash_running_and_not_running`, project-missing exit 2 with no new
  directory.
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): add devloop-dash start and stop`.

## Task 6: Skill, install, shipped-files, mutations

**Files:** modify `skills/dev-loop/SKILL.md`, `tests/devloop/test_shipped_files.py`,
`tests/devloop/mutations.sh`; test `tests/devloop/test_dash_cli.py`
(`test_install_ships_dash_assets`).

- start mode: after the cron step, run `devloop-dash PROJECT --detach` and tell the user the
  Network URL it printed. stop mode: `devloop-dash PROJECT --stop`. status mode: name the
  dashboard URL from `devloop-state PROJECT check`.
- `shipped()` adds `devloop/**/*.js`, `*.css`; the private-string and em-dash tests cover
  them.
- One `mutate` entry per defect-class mutation above.
- [ ] Steps: red, implement, green; run `tests/devloop/mutations.sh`, every dash mutation
  killed. Commit `feat(devloop): wire the dashboard into the skill`.

## Task 7: Role labels in STATUS and Slack (outside the dashboard)

**Files:** modify `devloop/status.py`; test `tests/devloop/test_snapshot_status.py`.

STATUS "Running" lines read per pool label ("Developers: 3"); the tick line tail reads
"Running: Developers 3/8, QA 1/4, Reviewers 2/4, Architects 1/3." Labels come from
`role_label` and `pool_label` only. The model-facing snapshot keeps kind names, since the
tick runs commands by kind. `_WORDS` "started {kind}" becomes "started {role}".

- [ ] Step 1: failing `test_status_and_tick_line_use_role_labels`; update the existing
  assertions that pin "builders" wording (an approved contract change, recorded here).
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): use role labels in status and tick posts`.
