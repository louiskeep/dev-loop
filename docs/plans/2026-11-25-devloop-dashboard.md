---
Status: plan
updated: 2026-11-25
---

# Dev Loop Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop, reuse-first,
proportionality, architecture, cross-cutting-concerns, security, observability-and-resilience,
api-and-compatibility, testing, workspace-and-vcs, profiles/local-operations.

Risk: **R1** (read-only, local, reversible; the only files it writes are its own pid and
port files). Plan review: one round. Code review: Codex on the exact head. Repo
`/home/cam/dev-loop-plugin`, branch `feat/headless-loop`, planned from head `fff2419`.

**Goal (owner, 2026-11-25):** one clean, local, read-only web dashboard that every project
using the plugin gets as part of the standard loop. It shows recently shipped work, rows in
progress, the status of each slice, workers, capacity and everything else in progress.
Single user, LAN, no auth, binds `0.0.0.0`. Threat model: accidents only.

**Owner addition (2026-11-25, role names):** everything people read uses role labels, not
kind or persona names. Code kind names (`runs.KINDS`) stay the internal contract; only labels
change (Task 1, and Task 7 for STATUS and Slack outside the dashboard).

## Review record

- Codex plan R1 (head 3258f6c): GO-WITH-FIXES, 11 findings, all folded with the owner's
  scope notes (structured facts only, no command runs, one server and a stable port per
  project, both builder kinds read "Developer"). Map in "Codex R1 findings" below.

**Architecture:** a sub-package `devloop/dash/`. `view.py` holds `build_view(project) ->
dict`, the only reader of state for the dashboard, built from existing functions. `render.py`
turns that dict into HTML; it is the only renderer, used for `/` and for the `/fragment` the
page polls. `server.py` holds the route table, ETags and the lifecycle. Static `dash.css` and
`dash.js` sit in `devloop/dash/web/`, so the existing `devloop-install` copy carries them.

**Tech stack:** Python 3.11 stdlib (`http.server`, `html`, `json`, `hashlib`, `socket`,
`fcntl`), vanilla JS, CSS custom properties. Tests: pytest with the `home`/`proj` fixtures.

## Guarantee and non-goals

Guarantee: the dashboard never writes loop state and never runs a configured command. Public
output holds only fixed wording and structured facts: event kinds, row and slice IDs that
pass the ID check, counts, phases, timestamps, role labels and PR URLs built from config. It
shows no free text a model or tool wrote, so no path, secret or commit id can reach it. A
damaged state file becomes an `unreadable` item; healthy cards still render.

Non-goals: auth, write actions, charts, drag and drop, logs and diffs, websockets, a JS
framework or build step, notifications, a stronger secret scanner, hostile-LAN defences,
server-side filtering, multi-project pages, node load text.

## Design notes

- No free text in the view. Slice `purpose`, `next_action`, `waiting_on`, question and
  option text, problem impact, tick-log bodies and command output are left out. Line 1 of a
  card therefore drops "purpose" from the research layout; it shows slice id, row, risk, PR
  link and round badge.
- One ID rule, `scrub.plain(value)`: fullmatch `store.TOKEN`, and no secret prefix
  (`scrub._TOKEN`), no 7-to-40 hex run, no 40-char run; else `"?"`. STATUS and Slack use the
  same function, so the rule tightens everywhere at once.
- One choke point for the view: `view._seal(tree)` walks the finished view. Each key has a
  declared kind in `VIEW_SCHEMA`: `id` (through `plain`), `word` (must be in the fixed
  wording table `view.WORDS`), `at` (ISO timestamp or null), `url` (fullmatch
  `https://github.com/<SLUG>/pull/<n>` or null), `int`, `bool`, `label` (from
  `project.role_label` or `pool_label`). Nullable keys are declared nullable. An undeclared
  key raises `ViewError`.
- One renderer (Python). The page polls `/fragment`; `/api/state` carries the same version
  and ETag for scripts and tests. This departs on purpose from "page polls `/api/state`".
- One place per fact: slot use in `runs.slot_usage` (from `holds_slot`); cap expiry in
  `store.cap_active`; role and pool labels in `project.role_label` and `pool_label`;
  checklist meaning in `evidence.exit_items`; the stuck threshold is `snapshot.STALE_SECS`.
- Read-only by construction: reader paths never create directories. `runs.iter_runs` and
  `slices.list_slices` return nothing when their directory is missing; only writers
  (`dispatch`, `slices._create`, `_save`, admission) create it.
- House style: typed context over long parameter lists, single-purpose functions, no flag
  arguments, comments that explain why.

## Placement

The repo has no CODEMAP. `devloop/` holds 23 modules and the fan-out cap is about 25, so new
modules go in a named sub-package; the read model is `devloop/dash/view.py`.

| New file | Directory | Why there | Sits beside |
|---|---|---|---|
| `devloop/dash/__init__.py`, `view.py`, `render.py`, `server.py` | `devloop/dash/` | fan-out cap; one concern per module | `devloop/status.py` |
| `devloop/dash/web/dash.css`, `dash.js` | `devloop/dash/web/` | shipped by `install_package`'s `devloop/` copy | `skills/dev-loop/` assets |
| `bin/devloop-dash` | `bin/` | launcher pattern of `bin/devloop-state` | `bin/devloop-state` |
| `tests/devloop/test_dash_*.py`, `dash_fixture.py` | `tests/devloop/` | existing test home | `test_snapshot_status.py` |

Expected size: 8 new files (view about 280 lines, render 260, server 260, JS 180, CSS 160,
tests 550); edits to `scrub.py`, `store.py`, `runs.py`, `slices.py`, `evidence.py`,
`status.py`, `snapshot.py`, `tick.py`, `dispatch.py`, `project.py`, `check.py`, `SKILL.md`,
`project.example.json`, `test_shipped_files.py`, `mutations.sh`. New public names:
`build_view`, `VIEW_SCHEMA`, `render_page`, `render_fragment`, `make_server`, `main`,
`dash_status`, `default_port`, `lan_ip`, `runs.slot_usage`, `runs.run_state`, `scrub.plain`,
`store.cap_active`, `evidence.exit_items`, `status.counts_from`, `status.event_words`,
`snapshot.command_outputs`, `project.role_label`, `project.pool_label`.

## Established solutions

The owner's research note (Buildkite, Argo CD, GitHub Actions, Vercel, Linear, Blue Ocean,
Conductor, OpenHands, Grafana) sets the layout, card shape, colours, refresh and filters.
Conditional GET follows RFC 9110 section 13 (`ETag`, `If-None-Match`, 304). Hidden-tab
pausing uses the Page Visibility API. One server per project uses an `fcntl.flock` held for
the process lifetime, the pattern `tick.py` already uses for `tick.lock`. The LAN address
uses the stdlib UDP `connect` trick (no packet sent). No custom code beyond view, renderer
and route table.

## Codex R1 findings

1. Private text: free text removed from the view; `plain` adds secret and hex checks to IDs;
   tests on all three routes (Acceptance tests).
2. `runs/` created by a PLAN card: readers never `mkdir`; test with no `runs/` and
   `store.locked` and `write_text` patched to raise.
3. Dashboard runs commands: it never does; it reads `node-status.json`, which the tick writes
   from the outputs it already collected (names, exit ok, time; no text).
4. One damaged file hides healthy work: each ledger read isolated, counts null when their
   ledger is unreadable; a checklist that raises reads `available: false`; tests for a bad
   `merges.json` and a bad sibling beside a healthy GATE card.
5. Reserved slots shown as running: `runs.run_state` splits executing, awaiting apply and
   awaiting release; card owner and stuck use executing only; capacity uses `holds_slot`.
6. Schema misfit: nullable card fields; `shipped` shapes split by `kind`; only `closed` row
   closures with `at` are shipped.
7. Pid and port scopes: one server per project by `dash.lock`; stable default port
   `8791 + sha256(project) % 20`, stored in `dash-port.json` and printed.
8. Agent names survive: both builder kinds read "Developer"; checklist items carry a fixed
   code, the reviewer's role and a fixed reason; run IDs leave the view.
9. SIGTERM in a thread and `send_error`: signals only in the CLI main thread; route tests use
   the signal-free `make_server`; SIGTERM tested in a subprocess; `send_error` goes through
   `_send`.
10. Install and mutation checks: assert at the real destinations; class 8 gets a concrete
    production mutation.
11. Third cap reader: `store.cap_active` shared by tick, dispatch and view, tested on absent,
    expired, future, naive and malformed files.

## Reuse inventory

- `slices.load`, `PHASES`, `ACTIVE`, `FORWARD`, `OWNER_PARKS`; `evidence.exit_checklist`
  (kept, now derived from `exit_items`).
- `runs.iter_runs`, `holds_slot`, `wrapper_alive`, `payload_alive`, `proc_start`,
  `boot_id`, `KINDS`.
- `notes.questions_path`, `load_questions` (keys and wait flags only), `problems.json` (keys
  and counts only).
- `board.merge_records`, `closure_records`; `status.counts` (rebuilt on `counts_from`);
  `status._WORDS` (made public as `event_words` for Recent activity).
- `snapshot.STALE_SECS`; snapshot command outputs (the tick's run, recorded as status only).
- `install.install_package`, `install_bins`: unchanged; they copy `devloop/**` and
  `bin/devloop-*`.

## Frozen contracts

- `KINDS` names, event kinds, slice and run file shapes, snapshot text and hash: unchanged.
- `exit_checklist` return shape `[(label, ok)]` and its labels: unchanged (`move` and the
  snapshot read them; PLAYBOOK `[code]` items match them).
- `devloop-state check` format `ok|FAIL: message`; one new line.
- STATUS.md and the Slack tick line change wording only for role labels (Task 7, approved).
- New, frozen here: `/api/state` JSON `schema: 1` with the Fields below; the five routes;
  CLI `devloop-dash PROJECT [--port N] [--host H] [--detach|--stop]`; state files
  `dash.json`, `dash.lock`, `dash-port.json`, `node-status.json`.

## Enumerations

### Exit paths

- Ctrl-C or SIGTERM: the CLI main thread installs the handler; `main`'s `finally` shuts the
  server, removes `dash.json` when its pid is ours, and releases `dash.lock` by closing it.
- Project already served: `dash.lock` is held, so a second start prints the running URL;
  `--detach` exits 0, a foreground start exits 3. `dash.json` is untouched.
- Bind fails on every port in the range, or on an explicit `--port`: exit 3 with
  `port N in use`; no `dash.json`.
- Project missing (no `project.json` under the state root): exit 2 before any `store` call,
  because `store.state_dir` creates the directory (`devloop/store.py:38`).
- SIGKILL or reboot: the flock dies with the process; `dash.json` is read dead by pid, start
  time and boot id (`devloop/runs.py:133`, `devloop/runs.py:143`) and replaced.
- `--detach` child not live within 5 s: exit 3, "see dash.log in the state dir".
- View build raises: 500 with fixed text "state unreadable"; the log holds the exception
  class name only. Unsupported methods: 501 through `_send` with fixed text.
- Damaged slice, ledger or questions page: no exit; an `unreadable` item names the file kind
  (`slices.load` raises per file, `devloop/slices.py:247`; ledgers at `devloop/board.py:181`;
  questions at `devloop/notes.py:287`).
- Client: a failed poll increments a counter; two failures or 60 s without a good poll shows
  the stale banner; it clears on the next success.

### Routes

One table, `server.ROUTES`, maps exact paths to handlers; `do_GET` and `do_HEAD` only.
- `GET /`: full HTML page, works without JS.
- `GET /fragment`: sections only, with `ETag`; 304 on `If-None-Match` match.
- `GET /api/state`: view JSON, same `ETag`.
- `GET /static/dash.css`, `GET /static/dash.js`: bytes read once at start, never a path join.
- Anything else, including `/static/../x`: 404. Query strings are ignored by the server.

### FK shapes

References the view joins, each checked:
- run `row` and `slice` to a slice file: a run whose slice is gone shows under workers
  without a card.
- merge ledger id `slug#pr@head` to `project.repos[].slug` (`devloop/board.py:181`): unknown
  slug or non-integer PR gets no URL.
- slice `repo` to `project.repos[].name`: unknown repo gets no URL.
- question `hold:KEY` to a HELD slice: shown once, on the hold item.
- checklist reviewer name to `project.reviewers[name].kind`, then to its role label.

### Fields

`VIEW_SCHEMA` (kind in brackets; `?` means nullable):
- top: `schema`[int], `project`[id], `version`[id], `tick`, `counts`, `needs_you`, `rows`,
  `workers`, `nodes`, `slots`, `shipped`, `activity`, `parked`.
- `tick`: `last_pass_at`[at?] (STATUS.md mtime; `write_status` runs on every pass),
  `last_model_at`[at?], `paused`[bool], `capped`[bool] (`store.cap_active`).
- `counts`: `needs_you`, `executing`, `free`[int]; `merged_today`, `merged_week`,
  `rows_closed`[int?] (null when a ledger is unreadable).
- `needs_you[]`: `kind`[word: question, hold, stuck, problem, unreadable], `key`[id?],
  `owner`[label], `count`[int?], `since`[at?].
- `rows[]`: `row`[id], `oldest_at`[at], `stuck`[bool], `cards[]`.
- card: `key`, `row`, `slice`[id], `phase`[word], `phase_index`[int], `held`[bool],
  `risk`[word?], `pr`[int?], `url`[url?], `round`[int], `since`[at], `owner`[label],
  `stuck`[bool], `checklist{available[bool], met[int], total[int], items[{code[word],
  role[label?], ok[bool], reason[word?]}]}`, missing items first.
- `workers[]`: `role`[label], `key`[id], `node`[id: request host or "local"],
  `state`[word: executing, awaiting-apply, awaiting-release], `started_at`[at].
- `nodes[]`: `name`[id], `ok`[bool], `at`[at] from `node-status.json`.
- `slots[]`: `pool`[id], `label`[label], `cap`, `used`[int].
- `shipped[]` (last 20, newest first): `kind`=`merge` with `row`, `slice`[id], `pr`[int],
  `url`[url?], `at`[at]; or `kind`=`row-close` with `row`[id], `at`[at].
- `activity[]` (last 20 events): `at`[at], `kind`[word], `words`[word-built line from
  `event_words`, IDs through `plain`]. `parked[]`: cards.

Not in the view: run IDs, reviewer and agent names, `worktree`, `plan_path`, `branch`,
`head`, repo paths, `purpose`, `next_action`, `waiting_on`, question text, problem impact,
tick-log bodies, command output, `history`, `evidence`.

### Output surfaces

- `/` HTML, `/fragment` HTML, `/api/state` JSON.
- CLI stdout: `Dashboard: http://<lan-ip>:<port>/` and fixed-word errors.
- `dash.log`: method, path without query, status code.
- `dash.json` (pid, start, boot id, port, host), `dash-port.json`, `node-status.json`: 0600,
  atomic `store.write_json`.
- `devloop-state check` line: `ok: dash: running at http://<lan-ip>:<port>/` or
  `ok: dash: not running (port N)`.
- STATUS.md and the Slack tick line (role labels, Task 7).

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | no free-text source in `build_view`; `_seal` walk over `VIEW_SCHEMA`; `plain` checks secrets and hex in IDs | `test_routes_leak_nothing_planted`, `test_token_in_id_is_redacted` | add `purpose` to the card dict and to `VIEW_SCHEMA` as `word` without the `WORDS` check |
| 2 | cleanup on every exit path | `main` `finally` removes its own pid file; flock released by close; liveness by pid, start and boot id | `test_sigterm_removes_pid_file` (subprocess), `test_stale_pid_file_reads_dead` | delete the `finally` block in `main` |
| 3 | state consistency (read-only) | readers never `mkdir`; view takes no lock | `test_build_view_writes_nothing` (PLAN slice, no `runs/`, `store.locked` and `write_text` raise) | restore `_runs_dir(project)` in `iter_runs` |
| 4 | route completeness | `ROUTES` table; `_send` sets CSP, nosniff, no-cache, no-referrer; `send_error` calls `_send` | `test_only_known_routes_and_methods`, `test_every_response_sends_security_headers` (incl. 404 and 501) | drop the `send_error` override |
| 5 | referential integrity | PR URL only from config slug plus int PR; orphan runs keep no card; reviewer role via config | `test_merge_with_unknown_slug_has_no_url`, `test_orphan_run_listed_without_card` | build the URL from the ledger slug without the config check |
| 6 | untrusted input | per-file catch to `unreadable`; ledgers read one by one; checklist failure reads unavailable; `html.escape` in render | `test_bad_merge_ledger_keeps_cards`, `test_bad_sibling_beside_gate_card`, `test_html_escapes_planted_markup` | let `exit_items` errors propagate from `view._card` |
| 7 | TOCTOU (check then use) | `dash.lock` taken before bind and pid write; pid written after bind | `test_second_start_same_project_other_port_refused`, `test_two_projects_get_distinct_ports` | write `dash.json` before taking `dash.lock` |
| 8 | weak tests | each row's mutation in `tests/devloop/mutations.sh`, run at the gate | `mutations.sh` reports each dash mutation killed | make `run_state` return `executing` for an exited builder (caught by `test_exited_builder_is_awaiting_apply`) |
| 9 | refactor drift | `slot_usage`, `cap_active`, `counts_from`, `event_words`, `exit_items`, `plain` extracted; old callers use them | existing `test_snapshot_status.py`, `test_tick.py`, `test_slices.py` green, `test_cap_active_cases`, `test_exit_checklist_labels_unchanged` | make `cap_active` return False for a malformed file |
| 10 | frozen contracts | `VIEW_SCHEMA` with `schema: 1`; `KINDS` names and `exit_checklist` labels unchanged | `test_view_keys_match_schema`, `test_kind_names_unchanged` | rename `phase_index` in `view.py` only |

## Acceptance tests

All in `tests/devloop/`, red first. `dash_fixture.py` builds one state dir through public
APIs: two rows; slices in FRAME (fresh, nulls), PLAN, REVIEW, GATE, HELD and PARKED; one stuck;
one executing builder, one exited builder not applied, one exited unconfirmed test; reviewers
named `zed-agent` and `qux-agent`; three merges, one closed row, one attempted-not-closed row;
one waiting question; one problem; one snapshot command recorded in `node-status.json`.

- `test_page_renders_from_fixture_offline`: `socket.socket.connect` and `getaddrinfo` raise;
  `render_page(build_view("p"))` has every section, empty-state texts, chips with
  `aria-current`, "n/m met", PR links, role labels.
- `test_routes_leak_nothing_planted` on `/`, `/fragment`, `/api/state` (server on
  `127.0.0.1:0`): plants `password hunter2` and `https://user:pw@host/x` in `purpose`,
  `next_action` and a tick-log entry, `/etc` and absolute paths in fields and command
  output; none appears, nor `str(tmp_path)`, nor a 40-char hex.
- `test_token_in_id_is_redacted`: a row id and a problem key holding `ghp_DEMOTOKEN123`
  render `?` on all three routes.
- `test_no_agent_names_in_output`: `zed-agent`, `qux-agent` and run IDs absent on all three
  routes; plan review, missing code review and a repair worker render role labels.
- `test_etag_304_on_unchanged_poll`, `test_etag_changes_after_slice_move`.
- `test_needs_you_present_with_empty_state`, `test_stuck_flag_after_stale_secs`,
  `test_cards_sorted_stuck_then_oldest`, `test_checklist_lists_missing_first`.
- `test_fresh_frame_card_renders_nulls`, `test_shipped_merges_and_closed_rows_only`.
- `test_exited_builder_is_awaiting_apply`, `test_unconfirmed_test_is_awaiting_release`
  (both still count in slots; neither owns the card; the slice can show stuck).
- `test_bad_merge_ledger_keeps_cards`, `test_bad_sibling_beside_gate_card`.
- `test_build_view_writes_nothing`, `test_html_escapes_planted_markup`.
- `test_server_never_runs_configured_commands` (fake snapshot command writes a marker; start,
  poll ten times, no marker).
- `test_only_known_routes_and_methods`, `test_static_traversal_404`,
  `test_every_response_sends_security_headers`.
- `test_sigterm_removes_pid_file`, `test_second_start_same_project_other_port_refused`,
  `test_two_projects_get_distinct_ports`, `test_default_port_stable_and_reported`,
  `test_detach_prints_network_url`, `test_stop_kills_only_own_pid`,
  `test_check_reports_dash_running_and_not_running`.
- `test_install_ships_dash_assets`: `HOME` patched; asserts
  `~/.local/share/devloop/lib/devloop/dash/web/dash.js` and `<bin_dir>/devloop-dash`.
- `test_cap_active_cases` (absent, expired, future, naive, malformed).
- `test_roles_config_overrides_and_validates`, `test_card_owner_is_role_label`,
  `test_slots_read_per_role_pool`, `test_status_and_tick_line_use_role_labels`.
- `test_css_tokens_meet_aa`; `test_no_private_strings_in_shipped_files` covers
  `devloop/dash/web/*`.
- JS behaviour (5 s poll, pause while hidden, refetch on focus, details and scroll kept,
  stale banner, filters in the URL, `/` focuses search) has no pytest runner. VERIFY runs it
  in a browser against the fixture server and records steps and results.

## Slices

| Slice | Tasks | Ships | Gate |
|---|---|---|---|
| S1 read model and page | 1, 2, 3, 4 | shared helpers, labels, `build_view`, renderer, assets, routes | Codex on the S1 head; T1 = `pytest tests/devloop -k "dash or status or snapshot or project or tick or slices or evidence"` |
| S2 lifecycle and labels everywhere | 5, 6, 7 | CLI, lock, port, detach and stop, check line, skill, STATUS and Slack labels | Codex on the S2 head; T1 = `pytest tests/devloop`; `mutations.sh` |

Low risk, so preparation folds into S1 and docs into S2. One PR is fine if review is short.

## Pre-mortem

- **Private text on the LAN.** No free-text source feeds the view; IDs pass `plain`; leak
  tests plant in every source on all three routes.
- **Dashboard and STATUS disagree.** Both read `slot_usage` and `counts_from`.
- **Polls load the state dir.** The server caches the view for 2 s behind a lock; unchanged
  polls answer 304.
- **Two dashboards for one project, or two projects fight for 8791.** `dash.lock` per
  project; hashed default port in a range of 20, stored and printed.
- **Clock skew between devbox and viewer.** The client takes skew from each `Date` header.
- **A damaged file blanks the page.** Per-file and per-ledger catch; checklist unavailable.
- **Checklist labels drift from `move`.** `exit_checklist` is derived from `exit_items`;
  `test_exit_checklist_labels_unchanged` pins today's labels.
- **JS regressions unseen.** JS stays small; the browser check at VERIFY is required.

## Task 1: Shared helpers and role labels

**Files:** modify `devloop/scrub.py`, `store.py`, `runs.py`, `slices.py`, `evidence.py`,
`status.py`, `snapshot.py`, `tick.py`, `dispatch.py`, `project.py`,
`skills/dev-loop/project.example.json`. Test: `tests/devloop/test_dash_shared.py`.

**Interfaces:**
- `scrub.plain(value) -> str` (from `status._plain`, plus secret, hex and long-run checks).
- `store.cap_active(project, now=None) -> bool`: absent False; malformed True; naive read as
  UTC; future True; past False. `tick._cap_active` and `dispatch._pause_or_cap` call it.
- `runs.iter_runs` and `slices.list_slices` yield nothing for a missing directory; `mkdir`
  moves to writers. `runs.run_state(rd, req) -> "executing" | "awaiting-apply" |
  "awaiting-release" | None`. `runs.slot_usage(project) -> dict` from `holds_slot`.
- `evidence.exit_items(project, key, to_phase=None) -> list[Item]`, `Item(code, reviewer,
  ok, reason, label)`; `code` and `reason` from fixed tables; `exit_checklist` returns
  `[(i.label, i.ok)]` with today's labels.
- `status.counts_from(merges, closures, now) -> dict`; `status.event_words(ev) -> str | None`.
- `snapshot.command_outputs(project, cfg) -> list[(name, text, ok)]`; `tick._pass` writes
  `node-status.json` `{name: {ok, at}}` from the outputs it collected. No text is stored.
- `project.ROLE_DEFAULTS`: planner Architect, builder Developer, codex-builder Developer,
  dennis Senior reviewer, codex-review Outside reviewer, codex-fix Outside reviewer, test QA,
  docs Tech writer, duty Duty, tick Tech lead, owner "Product owner (you)".
  `POOL_DEFAULTS`: builder Developers, tester QA, reviewer Reviewers, planner Architects.
  `role_label(cfg, kind)`, `pool_label(cfg, slot)`. Optional `roles` map: keys in
  `ROLE_DEFAULTS`, values single-line, 1 to 40 chars, else `ConfigError("roles.<key>: ...")`.
  Optional `dash: {port: 1024-65535, host: str}`.

- [ ] Step 1: failing tests `test_cap_active_cases`, `test_slot_usage_matches_status`,
  `test_exit_checklist_labels_unchanged`, `test_exited_builder_is_awaiting_apply`,
  `test_unconfirmed_test_is_awaiting_release`, `test_roles_config_overrides_and_validates`,
  `test_kind_names_unchanged`, `test_token_in_id_is_redacted` (unit part).
- [ ] Step 2: run, expect FAIL. Step 3: implement. Step 4: run plus `test_tick.py`,
  `test_slices.py`, `test_snapshot_status.py`, `test_runs_*.py`, expect PASS.
- [ ] Step 5: commit `refactor(devloop): share read helpers and add role labels`.

## Task 2: Read model `devloop/dash/view.py`

**Files:** create `devloop/dash/__init__.py`, `devloop/dash/view.py`,
`tests/devloop/dash_fixture.py`, `tests/devloop/test_dash_view.py`.

**Interfaces:** `build_view(project) -> dict`; `VIEW_SCHEMA`; `WORDS`; `ViewError`;
`version_of(view) -> str` (16 hex of sha256 over canonical JSON without `version`). Slices
read file by file through `slices.load`; each ledger in its own `try`. Needs you order:
questions (key, wait), holds (HELD, owner parks, `merge_unknown`), stuck, problems (key,
count), unreadable. Rows and cards sorted stuck first, then oldest `last_moved_at`. Chips use
`PHASES[:10]`; HELD or PARKED cards show `parked_from` as current. Owner: an executing run's
role, else "Product owner (you)" for HELD and owner parks, else the tick role.

- [ ] Step 1: failing view tests from Acceptance tests (offline render view part, schema,
  stuck, sort, checklist order, null FRAME card, shipped shapes, unknown slug, orphan run,
  bad ledger, bad sibling, writes nothing, owner label, slots by pool, no agent names).
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): add dashboard read model`.

## Task 3: Renderer and assets

**Files:** create `devloop/dash/render.py`, `devloop/dash/web/dash.css`,
`devloop/dash/web/dash.js`, `tests/devloop/test_dash_render.py`.

**Interfaces:** `render_page(view) -> str`, `render_fragment(view) -> str`. Sections: header
strip, Needs you, In progress (lanes, two-line cards, checklist in `<details>` with a stable
`id`), Workers and nodes, Capacity ("Developers 3/8"), Recently shipped, Recent activity
(collapsed), Parked and held (collapsed). Times as `<time datetime>` with server fallback.
Each state colours only its chip and carries an icon and a word. Filters on `data-*`, kept
in the query string. CSS tokens on `:root`, dark under `prefers-color-scheme`. JS polls
`/fragment` every 5 s with `If-None-Match`, pauses when hidden, refetches on focus, swaps
parsed sections keeping open `details` and `scrollY`, applies the stale rule and `Date`
skew, and never assigns `innerHTML` from JSON.

- [ ] Step 1: failing `test_html_escapes_planted_markup`, `test_css_tokens_meet_aa`, page
  part of `test_page_renders_from_fixture_offline`.
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): render the dashboard page`.

## Task 4: Routes

**Files:** create `devloop/dash/server.py`, `tests/devloop/test_dash_server.py`.

**Interfaces:** `ROUTES`; handler with `do_GET`, `do_HEAD`, `_send(code, body, ctype,
etag=None)`, `send_error` overridden to call `_send` with fixed text; `make_server(project,
host, port) -> ThreadingHTTPServer` with no signal handling; 2 s view cache under a
`threading.Lock`; `log_message` writes method, path without query, status.

- [ ] Step 1: failing route, ETag, header, leak, agent-name and never-runs-commands tests,
  with `make_server` on `127.0.0.1:0` in a thread.
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): serve the dashboard`.

## Task 5: CLI and lifecycle

**Files:** create `bin/devloop-dash`; modify `devloop/dash/server.py` (`main`,
`default_port`, `lan_ip`, `dash_status`), `devloop/check.py`; test
`tests/devloop/test_dash_cli.py`.

**Interfaces:** `devloop-dash PROJECT [--port N] [--host H] [--detach|--stop]`. Port order:
flag, `project.json` `dash.port`, `dash-port.json`, then `default_port(project) = 8791 +
int(sha256(project)[:8], 16) % 20`, trying the next ports in that range of 20 when busy; the
bound port is saved to `dash-port.json`. `main` takes `dash.lock` (non-blocking flock) before
bind, writes `dash.json` after bind, installs SIGTERM and SIGINT handlers in the main thread,
and cleans up in `finally`. `--detach` starts the launcher with `start_new_session=True`,
output to `dash.log`, waits up to 5 s for a live pid, prints `Dashboard:
http://<lan-ip>:<port>/`. `--stop` signals only on a pid, start and boot id match.
`lan_ip(host)` returns `host` unless `0.0.0.0`, else the UDP-connect address, else the
hostname address. `dash_status(project) -> (state, url, port)`; `check` adds one `ok:` line.

- [ ] Step 1: failing lifecycle tests from Acceptance tests (SIGTERM in a subprocess).
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): add devloop-dash start and stop`.

## Task 6: Skill, install, shipped-files, mutations

**Files:** modify `skills/dev-loop/SKILL.md`, `tests/devloop/test_shipped_files.py`,
`tests/devloop/mutations.sh`; test `test_install_ships_dash_assets` in
`tests/devloop/test_dash_cli.py`.

- start mode: after the cron step, run `devloop-dash PROJECT --detach` and give the user the
  Network URL it prints. stop mode: `devloop-dash PROJECT --stop`. status mode: the URL from
  `devloop-state PROJECT check`.
- `shipped()` adds `devloop/**/*.js` and `*.css`.
- One `mutate` entry per mutation in the defect table, each with the named test.
- [ ] Steps: red, implement, green; `tests/devloop/mutations.sh` kills every dash mutation.
  Commit `feat(devloop): wire the dashboard into the skill`.

## Task 7: Role labels in STATUS and Slack (outside the dashboard)

**Files:** modify `devloop/status.py`; test `tests/devloop/test_snapshot_status.py`.

STATUS "Running" lines read per pool label ("Developers: 3"); the tick line tail reads
"Running: Developers 3/8, QA 1/4, Reviewers 2/4, Architects 1/3." `event_words` says
"started Developer on ROW SLICE" instead of the kind. Labels come from `role_label` and
`pool_label` only. The model-facing snapshot keeps kind names, since the tick runs commands
by kind.

- [ ] Step 1: failing `test_status_and_tick_line_use_role_labels`; update the assertions
  that pin "builders" wording (approved contract change, recorded here).
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): use role labels in status and tick posts`.
