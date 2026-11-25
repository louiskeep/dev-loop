---
Status: plan
updated: 2026-11-25
---

# Dev Loop Dashboard: Mission Control and Loop Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
profiles/local-operations, feature-dev, reuse-first, proportionality, architecture,
cross-cutting-concerns, security, performance, observability-and-resilience,
api-and-compatibility, testing, workspace-and-vcs.

Risk: S1 and S2 are **R1** (read-only, local, reversible). S3 adds owner write actions over
HTTP (a new trust boundary), so it is **R2**: written plan, adversarial review (Senior
reviewer, then Outside reviewer, per the local operations profile), exact-head gate. Risk
only rises; the coordinator confirms the S3 tier. Repo `/home/cam/dev-loop-plugin`, branch
`feat/headless-loop`, planned from head `7af0177`.

**Goal (owner, 2026-11-25):** "I want something more like this"
(github.com/Dri-water/observe-agents-do-things, MIT). Rebuild the dashboard in that dense
mission-control style, keep everything only the loop knows, and make it "the one stop for
loop control": the owner answers questions, pauses, resumes and runs a tick from the page.

**Spec:** this plan, plus the base plan `docs/plans/2026-11-25-devloop-dashboard.md` (its
lifecycle and role-label rules stay in force; its read-only guarantee now covers GET routes
only). Reference screenshots: `/home/cam/scratch/oadt-ref/mission.png`,
`mission-drawer.png`, `mission-mobile.png`. Current look:
`/home/cam/scratch/devloop-dash/*.png`. Outside review folded in:
`/home/cam/scratch/devloop-review/dash-mc-plan-last.md` (see "Review record").

**Architecture:** `devloop/dash/transcript.py` reads the tail of each worker's output file
(Claude stream-json or Codex text), keeps an in-memory cache per file and a bounded feed
log, and returns only structured tool-call facts. `devloop/dash/live.py` turns runs plus
those facts into worker cards, the feed, the KPI numbers and per-slice drawer data.
`view.py` adds them to the sealed view (schema 2). `render.py` keeps the page shell and
pipeline; `render_live.py` renders the KPI strip, attention bar, worker cards, feed and
drawer sources; `render_control.py` renders the decisions drawer, the "Decided for you"
list and the loop controls. `server.py` gains one POST table, `ACTIONS`, whose handlers call
the same state functions the CLI uses. `dash.js` polls `/fragment` and drives both drawers.

**Tech Stack:** Python 3.11 stdlib (`os`, `json`, `re`, `collections`, `secrets`,
`hmac`, `urllib.parse`, `subprocess`), vanilla JS, CSS custom properties, inline SVG.
Tests: pytest with the existing `proj`, `home`, `dash` fixtures. No Hypothesis in the repo,
so fuzz tests use seeded `random` and are named as seeded fuzz.

## Global Constraints

- GET and HEAD routes never write state and run no command. Writes happen only in POST
  handlers listed in `server.ACTIONS`, each through an existing state function.
- Stdlib Python only. No external network, CDN, web font or JS library.
- Role labels from `project.role_label` and `pool_label` only. Run kind names such as
  `dennis` never reach a page.
- Loopback default bind, `dash.host` override: unchanged.
- Light and dark themes, both meeting the AA test in `test_dash_render.py`.
- Works at 390 px wide with no horizontal page scroll.
- No em dashes in any copy, CSS comment or JS string.
- No code diff text and no avatars. Role icons are inline SVG from a fixed set.
- Tool-call redaction: from a transcript the page shows only a tool kind tag, a Claude file
  basename or an allowlisted command name, counts and times.
- Owner questions (question, detail, options, answers) are shown in full, as written
  (owner decision, 2026-11-25; local single-user threat model).
- Poll every 4 s; keep open drawers, filters, open `details`, form input and scroll.
- Threat model: accidents only (double posts, stale tabs, wrong tab). Not attackers on the
  LAN. The default bind is loopback.

## Review Focus

1. A worker whose output file is huge or has one 50 MB line: the poll stays fast and
   memory flat (`test_first_read_is_bounded_by_tail_bytes`,
   `test_overlimit_line_across_polls_is_dropped`, Task 3).
2. An owner answers a question in a stale tab after the tick changed it: the answer is
   refused with "This question changed. Reload to see the new version."
   (`test_answer_with_old_version_is_refused`, Task 9).
3. A double click on "Go with recommended": one answer is recorded, the second post gets
   "Already answered" (`test_double_post_records_one_answer`, Task 11).
4. A shell command whose first word is data (`customer42`) or hides behind quotes: the page
   shows `?` (`test_shell_word_cases`, Task 1; leak sentry, Task 8).
5. The 31st active slice, opened then moved to DONE: it opens checklist-only, then reads
   "This slice is no longer active." (`test_drawer_fallback_hooks`, Task 6; VERIFY, Task 8).

## Guarantee and non-goals

Guarantee: GET routes publish only the base plan's structured facts plus tool kind tags,
Claude file basenames that pass `safe_target`, allowlisted command names, model names,
counts, buckets, timestamps, and owner question records in full. POST routes change state
only through `notes.answer`, `store.set_paused` and `tick.spawn_detached`, each refused
without the page token and a same-origin request, and `answer` refused on a stale version.

Non-goals: code diffs, `git` reads, line counts, costs, avatars, SSE, websockets, auth or
hostile-LAN defences, launching Claude or any agent from the page, editing slices from the
page, reading `~/.codex/sessions` or any file outside the project's state dir, a Codex
`--json` parser, per-subagent trees, a JS build step, multiple projects per page.

## Design notes

- **Kind on a worker card means the harness**: badge `Claude`, `Codex` or `Script`. One
  registry: `runs.Kind` gains `harness` (`claude`, `codex`, `script`) and `transcript`
  (`stream-json`, `codex-text`, `outside`, `none`). `wrap.CODEX_KINDS` is derived from it
  (`devloop/wrap.py:23`), `transcript_path` and the badge read it. `codex-fix` is
  `transcript: outside` (its launcher writes `~/.cache/codex-fix/<name>/out.txt`, outside
  the state dir), so its card reads "activity not shown for this worker", distinct from
  "starting". `test` and `duty` are `script`/`none`.
- **Feed tags live on the event descriptors**: `status._WORDS` (`devloop/status.py:152`)
  becomes `status.EVENTS = {kind: EventDesc(words, tag)}`; `EVENT_KINDS`, `event_words` and
  a new `event_tag` read it. The view reads events once into `ctx.events`; activity and the
  feed both project from that list (today `_activity` reads them, `devloop/dash/view.py:623`).
- **Owner questions in full** (coordinator decision): question, detail, options,
  recommended and answers are sealed as `owner_text` (single line, at most 1000
  characters, escaped at render). The answer hint uses the real key (`hold:KEY` for holds,
  `devloop/notes.py:473`) and the real option values.
- **Question class** (owner addition): `ask` takes `klass` (`tech` or `product`, default
  `product`). Product is anything a user of the product would notice or the business
  decides (behavior, scope, defaults, naming, pricing, release, data-handling policy,
  roadmap order); tech is implementation, tooling, test and gate mechanics; unsure means
  product. The class is the tick's judgment, written in TICK and PLAYBOOK; no config list.
  Ratchet, cap or allowlist growth follows the ratchet rule (owner, 2026-11-25): it is tech
  when a planned fix (roadmap row, plan or logged follow-up) will retire the entry, or the
  entry is a legitimate registry site, and the answer names that fix; with no fix planned,
  the tick logs one first; when the same class has needed entries in 3 or more places, the
  tick adds no entry and sends it to an Architect tech review for a central handler. The
  Decoy playbook already carries this rule (`PLAYBOOK-decoy.md` lines 129, 169, 212 in the
  vault); the generic skill playbook gets the same rule in generic words. `tech` means:
  the recommended option applies now, the Slack line reads "Tech lead decided KEY: OPTION. Override on the
  dashboard.", and the page lists it under "Decided for you" with Override. `product` means
  the question waits for the owner. `wait` is derived from the class; the CLI `--wait` flag
  stays and forces `product`.
- **Free-text answers** are stored as `other: <text>` (single line, 1 to 500 characters)
  for non-hold questions. Holds accept only their two options. The tick treats an
  `other:` answer as the owner's instruction (TICK and PLAYBOOK wording, Task 9).
- **Question version**: `notes.question_version(q)` is 16 hex of sha256 over the canonical
  JSON of the stored record. A dashboard answer carries it; `_apply_answer` compares it
  inside the questions lock and refuses a mismatch. The CLI passes no version.
- **One write path**: the dashboard calls `notes.answer(..., via="dashboard",
  expect_version=V)`, `store.set_paused(project, on, via)` (the CLI `pause`/`resume` branch,
  `devloop/state_cli.py:257`, moves into it) and `tick.spawn_detached(project)` (moved from
  `wrap._wake_tick`, `devloop/wrap.py:241`, so wrapper wake-ups and the button start a tick
  the same way). No new file format: two optional fields (`Detail`, `Class`)
  join the existing questions page.
- **"Run a tick now"** is refused when `tick.lock_held(project)` is true (non-blocking flock
  probe on `tick.lock`, released at once, `devloop/tick.py:199`). The probe and spawn can
  race with cron; the tick's own lock then coalesces the second start into
  `tick.requested`, so the race is harmless.
- **Token and origin**: one `secrets.token_urlsafe(32)` per server process, embedded in the
  page and every form. A POST needs the token (compared with `hmac.compare_digest`) and an
  `Origin` (or, without it, `Referer`) whose host and port equal the request's `Host`.
  A stale tab after a server restart gets "This page is out of date. Reload." The token is
  not in `/api/state` or the ETag.
- **"Copy for Claude"** copies: "Help me decide dev loop question KEY in project PROJECT.
  The question, its options and the background are in PATH. Read it, explain the trade-off,
  and recommend an option." PATH is the questions file path, shown only in this copy text
  (owner request). The clipboard API needs a secure context (loopback is one; a LAN IP over
  http is not), so the button falls back to selecting a read-only text box.
- **Drawers**: one shell outside `#app-body`, two kinds by hash: `#slice=KEY` and
  `#q=KEY`. Drawer data rides in hidden sources inside `/fragment`, so GET routes stay
  five. A slice beyond `DRAWER_SLICES` opens checklist-only from its pipeline card; only a
  key with neither source nor card reads "no longer active".
- **Choke points**: transcript text reaches the view only through `safe_target`, re-checked
  by `_seal`'s `target` kind. Writes happen only in `server.ACTIONS` handlers, reached only
  through `_post`, which checks token, origin and body size before any handler runs.
- **Builder work assumed in the tree** (coordinator note): dashboard config reads do not
  create files, ledger parsing is tolerant, text is normalized after truncation, and the
  dashboard iterates `project.SLOTS`.
- **Principles applied**: single responsibility (parsing and caching, view assembly, live
  HTML, control HTML and actions in separate modules; `view.py` is 640 lines and `render.py`
  485); information hiding (`Cache.read` hides offsets, rotation and format). House style:
  typed context objects, one place per fact, no flag arguments.

## Established solutions

Searched: the reference repo's protocol and mission view (`docs/PROTOCOL.md`,
`apps/web/src/viz/mission/mission.css`, read through `gh api`), and the codebase's own
readers and writers. Used:
- Tool categories and mapping a Codex `exec` of `rg` to search: the reference's PROTOCOL
  "Tool categories". We keep our own fixed tag table.
- File following: GNU `tail -F` semantics and Python's
  `logging.handlers.WatchedFileHandler` (compare dev and inode after `os.fstat`).
- Line framing with a hard per-line limit: `asyncio.StreamReader.readline` limit semantics
  (an over-limit line is discarded, not passed on).
- Write protection for a local page: the OWASP synchronizer-token pattern plus an `Origin`
  check (OWASP CSRF Prevention Cheat Sheet), POST-only state changes, and optimistic
  concurrency with a version token (HTTP `If-Match` idea, RFC 9110 section 13.1.1, carried
  as a form field).
- Stream-json shapes from live run files under `~/.local/state/devloop/decoy/runs/*/out/log`
  (field names only); Codex text from `codex.out` written by `~/bin/codex-review`.

## Performance limits

All constants live at the top of `devloop/dash/transcript.py` and `live.py`.

| Name | Value | Meaning |
|---|---|---|
| `TAIL_BYTES` | 524288 (512 KiB) | first read starts at `max(0, size - TAIL_BYTES)` |
| `STEP_BYTES` | 1048576 (1 MiB) | most bytes read from one file per build; a bigger gap jumps to the tail (a discontinuity) |
| `MAX_LINE` | 2097152 (2 MiB) | any line longer than this is dropped before parsing, and so is a pending piece that passes it |
| `KEEP_CALLS` | 50 | tool calls kept per file |
| `SPARK_MINUTES` | 15 | per-minute buckets for the sparkline |
| `RATE_SECONDS` | 60 | one-second buckets per file for the exact trailing-60-second rate |
| `DEDUP_IDS` | 2000 | message ids remembered for token deduplication (oldest dropped; the total is kept apart) |
| `MAX_LIVE` | 16 | executing runs parsed per build, newest first |
| `MAX_DRAWER_FILES` | 8 | finished-run transcripts parsed per build for drawers |
| `CACHE_ENTRIES` | 64 | files kept in the cache; least recently used evicted |
| `FEED_LIMIT` | 40 | feed items, newest first; the feed log keeps 40 safe facts apart from the file cache |
| `DRAWER_RUNS` | 8 | runs listed per slice drawer, newest first |
| `DRAWER_SLICES` | 30 | active slices given a drawer source |
| `POST_BYTES` | 8192 | largest POST body accepted |
| `TICK_DEBOUNCE_S` | 10 | a second "Run a tick now" within this window is refused |

Cache identity: `(st_dev, st_ino)`. Fast path: `os.stat(path)`; if inode, size and
`st_mtime_ns` match the entry, no open and no read. Otherwise open `"rb"`, `os.fstat`, and
use the fstat values. The cursor advances by the bytes actually read, never to the fstat
size. A steady poll reads only growth; a cold build reads at most
`(MAX_LIVE + MAX_DRAWER_FILES) * TAIL_BYTES` (12 MiB). Live files today are about 400 KB.

Missing or rotated transcripts:
- File absent and run executing: `starting`; absent and run finished: `none`.
- Kind with `transcript: outside` (`codex-fix`): `outside`, never `starting`.
- `OSError` on open: `unreadable` on that card only.
- Inode changed or size below the cursor: a discontinuity (below), tail read again.
- Run finished and in no drawer: its cache entry is evicted; its last feed facts stay in
  the feed log.

A discontinuity (tail jump, rotation, truncation, dropped over-limit line) clears the
pending bytes, the Codex parser's continuation state (an `exec` waiting for its command, an
open patch block) and the "newest call still running" flag, and sets `partial`.

## Claude and Codex parsing

Where the files are: the wrapper sends payload stdout and stderr to `out/log`
(`devloop/wrap.py:281`). Claude workers run with `--output-format stream-json --verbose`
(`devloop/dispatch.py:122`). Codex review and builder runs get `out` = `out/codex.out`
(`devloop/dispatch.py:443`) and their launchers write plain `codex exec` text there.
`final.md` is never read.

Claude (`parse_claude_line(raw: bytes) -> ClaudeFacts | None`), structural:
- A byte pre-filter only skips lines that cannot matter: lines whose `"type"` is
  `system` with a subtype other than `init`, `tool_progress`, `rate_limit_event`. Every
  `assistant`, `user`, `system/init` and `result` line within `MAX_LINE` is decoded with
  `json.loads` and read through typed field access.
- `assistant`: `message.id`, `message.usage` (`output_tokens`, `input_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`; ints only), and each
  `tool_use` block's `id`, `name` and `input`. A text-only message still yields its usage.
- `user`: each `tool_result` block's own `tool_use_id` and its own `is_error`, block by
  block.
- `system/init`: `model` only.
- Tokens: per message id, the largest `output_tokens` seen; the running total grows by the
  difference, so repeated lines count once. The id memory is bounded (`DEDUP_IDS`); the
  total is a separate integer.
- Tool name to tag (`TOOL_KINDS`): Read, NotebookRead READ; Edit, MultiEdit, NotebookEdit
  EDIT; Write WRITE; Bash, bash, BashOutput, Monitor SHELL; Grep, Glob, ToolSearch SEARCH;
  Task, Agent AGENT; WebFetch, WebSearch WEB; TodoWrite, Skill, ExitPlanMode PLAN; names
  starting `mcp__` MCP; anything else OTHER.
- Target: READ, EDIT, WRITE take `safe_target(input.file_path or input.notebook_path)`
  (a structured field). SHELL takes `shell_word(input.command)`. SEARCH, WEB, PLAN take the
  tool's own name from the table. AGENT, MCP, OTHER have none.

Codex text (`CodexParser.feed`): Codex text mixes commands with their output, so a line of
output can look like a block header. Rules:
- Tags come from block headers: a line exactly `exec` starts a call (tag SHELL, or SEARCH
  or READ when the command name is in `CODEX_SEARCH` or `CODEX_READ`); `succeeded in Nms`
  or `exited N in Nms` ends it (`exited` non-zero adds an error); `apply_patch` or
  `file update` starts a patch block whose `A`/`M`/`D` lines each count one WRITE or EDIT.
- Targets: a Codex SHELL, SEARCH or READ call shows the command name only when it is in
  `PUBLIC_COMMANDS` and the command line has no quote, backslash, `$`, backtick or `<`.
  Codex patch lines never show a file name (they may be command output). Otherwise `?`.
- Model: read once per file identity from the file head (first 4 KiB, between the first two
  `--------` lines), never from a tail. A header-looking line mid-file is ignored.
- `tokens used` then a number line sets `tokens_out`.
- Unknown text gives no calls and no error. Fake calls from output that mimics `exec` can
  inflate counts; they can only show allowlisted names. Recorded as a known limit.

`safe_target(name: object) -> str`: not a str gives `"?"`; else the part after the last `/`;
it must fullmatch `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}` and equal `scrub.plain(part)`
(`devloop/scrub.py:51`); else `"?"`.

`shell_word(cmd: object) -> str`, pinned by tests:
1. Not a str gives `"?"`. Take the first 400 characters.
2. If the whole text matches `^(?:/(?:usr/)?bin/)?(?:ba)?sh -l?c '([^']*)'$`, use group 1.
3. If the text before the first `&&`, `;` or newline contains a quote, backslash, `$`,
   backtick, `<`, `>` or `|`, return `"?"`.
4. Split that first segment on whitespace; drop leading `NAME=value` tokens and the
   wrappers `env`, `sudo`, `nice`, `nohup`, `time`, and `timeout` with a `\d+[smhd]?`
   argument. If the first token left is `cd`, move to the next `&&` segment and repeat
   step 3 on it (at most 3 segments).
5. Return the token's basename when it is in `PUBLIC_COMMANDS`, else `"?"`.

`PUBLIC_COMMANDS` (frozenset in `transcript.py`): `git gh python python3 uv uvx pip pytest
ruff mypy node npm npx pnpm make cargo go rustc docker jq rg grep find fd ls cat sed head
tail nl wc diff sort uniq mkdir cp mv rm touch chmod echo printf sleep curl tar bash sh
codex claude devloop-state devloop-dash devloop-tick devloop-dispatch`. A guard test pins
that every entry passes `safe_target`.

## Reuse inventory

- `view._seal`, `VIEW_SCHEMA`, `WORDS`, `version_of`, `_try`, `_Ctx`, `_iso`,
  `_needs_you`, `_problems` (`devloop/dash/view.py:201`, `:478`): extended, not copied.
- `_Ctx._runs` (`devloop/dash/view.py:312`): the one walk of `runs/`; it now also keeps a
  bounded history per active slice before it filters to slot holders.
- `runs.iter_runs`, `run_state`, `KINDS` (`devloop/runs.py:113`, `:233`, `:26`);
  `store.out_dir`, `store.read_header` (drawer status and verdict from `result.md`).
- `notes.load_questions`, `ask`, `answer`, `_apply_answer`, `HOLD_PREFIX`,
  `_post_question` (`devloop/notes.py:287`, `:371`, `:548`, `:517`, `:428`).
- `store.locked`, `store.append_event`, `store.state_dir`; `tick.tick` lock file
  (`devloop/tick.py:199`); `wrap._wake_tick` (moved, not copied).
- `status.events`, `status.event_words`; `project.role_label`, `pool_label`, `SLOTS`.
- `render._chip`, `_time`, `_bar`, `_phase_strip`, `_checklist`, `_section`, `_details`
  (`devloop/dash/render.py:93`, `:117`, `:234`, `:249`).
- `server.ViewCache`, `Handler._send`, `ROUTES` (`devloop/dash/server.py:41`, `:75`, `:144`).
- `dash.js` `swap`, `poll`, filters, theme (`devloop/dash/web/dash.js:97`, `:121`).
- Test helpers `dash_fixture.put_run`, `build`, `put_slice`; `srv`, `call`, `_plant` in
  `test_dash_server.py`.
- Layout ideas only from the reference repo. No code copied.

## Frozen contracts

- Unchanged: CLI `devloop-dash`; `dash.json`, `dash.lock`, `dash-port.json`,
  `node-status.json`; `KINDS` names; `exit_checklist` labels; the wrapper's output file
  names; stream-json is read, never changed; `devloop-state answer KEY CHOICE`, `pause`,
  `resume` behave as today.
- Changed on purpose, frozen here:
  - `/api/state` `schema: 2` with the Fields below.
  - Routes: the five GET routes plus POST `/act/answer`, `/act/pause`, `/act/resume`,
    `/act/tick`. `ROUTES` becomes `{(method, path): handler}`.
  - CSP `form-action 'self'` (was `'none'`).
  - `runs.Kind` gains `harness` and `transcript`; `wrap.CODEX_KINDS` is derived.
  - Questions page: optional `- Detail:` and `- Class:` lines; answers may be
    `other: <text>` for non-hold questions. A page without the new lines reads as today
    (class `product`).
  - `devloop-state ask` gains `--detail` and `--class tech|product`.
  - Event kinds added: `question-answered` gains `via`; new `question-decided`,
    `loop-paused`, `loop-resumed`, `tick-requested`, each with `via`.
- New and frozen: `TOOL_TAGS = ("READ", "EDIT", "WRITE", "SHELL", "SEARCH", "AGENT", "WEB",
  "PLAN", "MCP", "OTHER")`; event tags `DISPATCHED`, `APPLIED`, `MOVED`, `MERGED`,
  `CLOSED`, `QUESTION`, `DECIDED`, `ANSWERED`, `DROPPED`, `PAUSED`, `RESUMED`, `TICK`;
  icon ids `plan`, `build`, `review`, `test`, `docs`, `duty`; harness words `claude`,
  `codex`, `script`; transcript states `live`, `starting`, `outside`, `none`, `unreadable`.

## Enumerations

### Exit paths

- Reader: file absent, open fails, rotation, truncation, over-limit line: see "Missing or
  rotated transcripts". No exception leaves `transcript.py` for `OSError`, `ValueError`,
  `UnicodeError`, `RecursionError`, `TypeError` or `KeyError` from one line or one file;
  the per-line boundary is in `Cache.read`.
- Run leaves `executing` (`devloop/runs.py:233`): its card leaves the grid; it stays in its
  slice's drawer history; its feed facts stay in the feed log.
- GET view build raises: the existing fixed 500 (`devloop/dash/server.py:107`).
- POST refusals, each a fixed message, HTTP status, and no state change:
  - token missing or wrong: 403 "This page is out of date. Reload."
  - origin missing or foreign: 403 "Refused: not sent from this page."
  - body over `POST_BYTES`, bad form encoding, unknown or missing field: 400 "Bad request."
  - question unknown: 409 "This question is gone. Reload."
  - version differs: 409 "This question changed. Reload to see the new version."
  - already answered (product question with an answer, version unchanged would be
    impossible, so this is the double post): 409 "Already answered."
  - choice not an option and not a valid `other:` text, or `other:` on a hold: 400 with
    the `NotesError` message (fixed strings from `notes.py`).
  - tick lock held: 409 "A tick is running now." Within `TICK_DEBOUNCE_S`: 409 "A tick was
    just started."
  - a state function raises anything else: 500 "Could not save. Nothing was changed." and
    the log gets the exception class name only.
- POST success: 303 to `/` (no JS) or JSON `{"ok": true, "message": ...}` when the request
  sends `Accept: application/json` (JS).
- Client: a drawer open on a key with no source and no pipeline card reads "This slice is
  no longer active."; a question drawer whose question left `needs_you` reads "This
  question was answered." Two failed polls show the stale banner.

### Routes

- GET (read-only, as today): `/`, `/fragment`, `/api/state`, `/static/dash.css`,
  `/static/dash.js` (`devloop/dash/server.py:144`).
- POST (`server.ACTIONS`): `/act/answer` (fields `t`, `key`, `version`, `choice` or
  `other`), `/act/pause` (`t`), `/act/resume` (`t`), `/act/tick` (`t`).
- HEAD mirrors GET; any other method, or POST to a GET path, is 501 or 405 with fixed
  text; unknown paths 404.
- `_post` runs token, origin and size checks before looking up the handler, so a new
  action cannot skip them.

### FK shapes

- Run to slice: `req["key"]` to an active slice key; otherwise `slice_active: false`, no
  drawer link.
- Drawer run list: only runs whose `req["key"]` equals the drawer key; selected in the
  run walk before the slot filter.
- Card to transcript: path from `store.out_dir(rd)` and the kind's `transcript` value;
  `req["out"]` is honoured only when it equals `out_dir(rd) / "codex.out"`.
- Answer form to question: `key` must exist in the questions page; `version` must equal
  `question_version` of that record inside the lock. A hold item's form key is `hold:KEY`.
- Tech ratchet answer to its planned fix: the TICK wording requires the answer text to
  name the roadmap row, plan or follow-up key that retires the entry.

### Fields

`VIEW_SCHEMA` additions and changes (kind in brackets, `?` nullable). Everything else as in
the tree at `7af0177`, which already has `needs_you` (owner items) and `problems` (stuck,
problem, unreadable).
- top: `schema`[int] = 2; adds `kpi`, `live`, `waiting_runs`, `feed`, `drawers`,
  `decided`, `controls`; removes `workers`.
- `kpi`: `running`[int], `slots`[int], `needs_you`[int], `merged_today`[int?],
  `tools_per_min`[int] (exact trailing 60 s from second buckets), `free_mb`[int?],
  `memory_state`[word?], `last_pass_at`[at?].
- `needs_you[]` (tree fields plus): `answer_key`[id?] (`hold:KEY` for holds; null when no
  question record exists, as for `merge_unknown`), `question`[owner_text?],
  `detail`[owner_text?], `options`[owner_text list], `recommended`[owner_text?],
  `klass`[word: tech, product], `version`[version?].
- `decided[]` (tech questions decided by the Tech lead, newest 20): `key`[id],
  `question`[owner_text], `detail`[owner_text?], `options`[owner_text list],
  `applied`[owner_text], `answer`[owner_text?] (set after an override), `at`[at?],
  `version`[version].
- `controls`: `paused`[bool], `tick_running`[bool].
- `live[]`: `ref`[id], `role`[label], `icon`[icon], `key`[id], `slice_active`[bool],
  `phase`[word?], `harness`[harness], `model`[model?], `started_at`[at?], `state`[word],
  `doing`(null or {`tag`[tag], `target`[target?], `at`[at?], `running`[bool]}),
  `tools`[int], `partial`[bool], `errors`[int], `tokens_out`[int?], `context`[int?],
  `spark`[int list of `SPARK_MINUTES`].
- `waiting_runs[]`: `role`[label], `key`[id], `state`[word], `started_at`[at?].
- `feed[]`: `at`[at], `tag`[tag], `target`[target?], `role`[label?], `key`[id?],
  `words`[line?].
- `drawers[]`: `key`[id], `runs`[{`ref`[id], `role`[label], `harness`[harness],
  `started_at`[at?], `status`[word?], `verdict`[word?]}], `calls`[{`at`[at?], `tag`[tag],
  `target`[target?]}], `files`[{`name`[target], `edits`[int]}] (Claude runs only).

New scalar kinds: `tag`, `target` (`safe_target(v) == v`), `model` (fullmatch
`[a-z][a-z0-9.-]{1,48}`, no `scrub._TOKEN` match), `icon`, `harness`, `owner_text` (str,
no newline, at most 1000 characters).

Not in the view: run ids, run kind names, reviewer names, tool inputs other than the
basename or allowlisted name, prompts, tool results, model text, session ids, directories,
costs, the page token, the questions file path (only in the copy text, rendered from
`answer_at` plus the state dir at render time on the server).

### Output surfaces

- `/` and `/fragment` HTML: KPI strip with loop controls, attention bar (owner questions,
  then stuck), "Decided for you", worker grid, feed, pipeline, known problems, capacity,
  memory, shipped, parked, hidden drawer sources (slices and questions).
- `/api/state` JSON: the sealed schema 2 view.
- POST responses: fixed messages listed in Exit paths.
- Server log: method, path, status; for POST also the action name and outcome word.
  Never a form value or question text.
- Events (`events.jsonl`): `question-answered via=dashboard`, `question-decided`,
  `loop-paused`, `loop-resumed`, `tick-requested`, with IDs only.
- Slack: the tech-decision line from `_post_question`; dashboard answers post the existing
  answered line.
- Questions page: `Detail` and `Class` lines; `Answer: other: ...`.
- Browser: theme in `localStorage`; drawer key in `location.hash`; filters in the query.

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | transcript text reaches the view only through `safe_target` (Claude structured file fields) and `PUBLIC_COMMANDS`; Codex patch names never published; `_seal` kinds re-check | `test_transcript_secrets_reach_no_route`: asserts 200, the positive facts (`SHELL git` from Claude, `SEARCH rg` from Codex, `EDIT view.py` in a finished drawer) and the absence of `customer42` and every planted secret on all three GET routes | drop the `PUBLIC_COMMANDS` check in `shell_word` (planted `customer42 --x` then reaches the page as a sealed target) |
| 2 | cleanup on every exit path | `with open(path, "rb")`; `Cache.collect` evicts entries for runs neither live nor in a drawer; `tick.spawn_detached` uses `start_new_session`, DEVNULL stdin, `close_fds` | `test_cache_evicts_a_previously_cached_path` (asserts the path left `cache.entries`), `test_spawn_detached_leaves_no_child_fds` | delete the eviction step in `Cache.collect` |
| 3 | state consistency | GET never writes; POST writes only through `notes.answer`, `store.set_paused`, `tick.spawn_detached` under their existing locks; cursor advances by bytes read | `test_get_routes_write_nothing` (walks every GET in `ROUTES`, state dir listing, sizes and mtimes unchanged), `test_truncate_between_fstat_and_read_then_regrow` | advance the cursor to the fstat size instead of `start + len(data)` |
| 4 | route completeness | `ROUTES` keyed by `(method, path)`; every POST goes through `_post` (token, origin, size) before `ACTIONS` lookup | `test_every_post_route_refuses_without_token_and_origin` (walks `ACTIONS`), `test_only_known_routes_and_methods` | call the handler before the token check in `_post` |
| 5 | referential integrity | drawer runs selected by key in the run walk; answer key and version resolved inside the questions lock; hold forms use `hold:KEY` | `test_drawer_keeps_finished_run_history`, `test_hold_answer_uses_hold_key`, `test_unknown_question_key_is_refused` | build the hold form with the slice key instead of `answer_key` |
| 6 | untrusted input | transcript bytes: every line length-checked before parsing, typed field access, per-line boundary in `Cache.read`; POST bodies: size cap, strict field set, `other:` text validated by `notes` | seeded fuzz `test_parser_never_raises_on_random_bytes`, `test_parser_error_on_one_line_keeps_the_next` (injected through `Cache.read`), `test_post_body_limits_and_unknown_fields` | remove the per-line `try` in `Cache.read` |
| 7 | TOCTOU (check then use) | reader: open, then `fstat` the open file; answer: version compared inside `store.locked` of the questions page; tick: lock probe races resolved by the tick's own flock | `test_path_replaced_between_stat_and_open_reads_new_file_only`, `test_answer_with_old_version_is_refused`, `test_double_post_records_one_answer` | compare the version before taking the questions lock |
| 8 | weak tests | every mutation here is a `mutate` entry; the runner runs each entry's tests unmutated first and counts a kill only when pytest exits 1 (failed assertions), not 2 to 5 | `mutations.sh` reports every dash entry killed; a baseline-red entry prints `RED-BASE` | make the first read ignore `TAIL_BYTES`, caught by `test_first_read_is_bounded_by_tail_bytes` |
| 9 | refactor drift | `pause`/`resume` move into `store.set_paused`; `_wake_tick` moves into `tick.spawn_detached`; `CODEX_KINDS` derived from `runs.KINDS`; event tags on `status.EVENTS` | `test_cli_and_dashboard_pause_share_one_path`, `test_codex_kinds_derived_from_registry`, `test_every_event_kind_has_a_tag`, `test_every_run_kind_has_harness_icon_and_transcript` | hard-code `CODEX_KINDS` again without `codex-builder` |
| 10 | frozen contracts | `VIEW_SCHEMA` schema 2; route table; questions page reads old pages unchanged; CLI answer without version works as today | `test_view_keys_match_schema`, `test_schema_is_two`, `test_old_questions_page_reads_as_product`, `test_cli_answer_unchanged` | default a missing `Class` line to `tech` |

## Acceptance tests

All red first. New test files live in a new subpackage `tests/devloop/dash/` (the
`tests/devloop/` directory already holds 26 modules). It has a `conftest.py` that puts
`tests/devloop` on `sys.path` and re-exports the `dash` fixture from `dash_fixture`;
fixtures from `tests/devloop/conftest.py` apply as usual. Files:
`tests/devloop/dash/transcript_fixture.py`, `test_transcript.py`, `test_live.py`,
`test_controls.py`. Existing dash tests stay where they are and are updated in place.

`transcript_fixture.py` writes synthetic files with real shapes (field names only):
`claude_events(items) -> bytes` (`system/init`, `assistant` with `tool_use` or text only,
repeated message ids, `user` with several `tool_result` blocks, thinking, `result`) and
`codex_text(blocks) -> bytes` (header, `exec` blocks, `succeeded`/`exited`, `apply_patch`
and `file update:` blocks, `codex`/`thinking`, `tokens used`).

Reader and parser (S1):
- `test_shell_word_cases` (parametrized): `"git status"` gives `git`;
  `"/bin/bash -lc 'git diff'"` gives `git`; `"FOO=1 make t"` gives `make`;
  `"timeout 30 pytest"` gives `pytest`; `"cd /a && git log"` gives `git`;
  `"cd '/tmp/a; customer42 more' && git status"` gives `?`; `"customer42 --x"` gives `?`;
  `"ghp_DEMOTOKEN1234567 x"` gives `?`; `"echo $SECRET"` gives `?`; `"cat a | sh"` gives
  `?`; `""`, `None`, `"cd /a"` give `?`.
- `test_public_commands_pass_safe_target`, `test_safe_target_cases` (`view.py` passes;
  `.env`, `a b.py`, 65-character names, 7+ hex runs give `?`; `x/../../etc/passwd` gives
  `passwd`).
- `test_tool_kinds_and_targets_from_claude` (Read `/x/y/view.py` READ `view.py`; Grep
  SEARCH `Grep`; Agent and `mcp__qmd__query` no target).
- `test_text_only_message_counts_tokens`, `test_tokens_deduped_by_message_id`,
  `test_token_total_survives_dedup_eviction` (2 500 distinct ids: total exact).
- `test_two_results_with_different_error_flags` (one call errors, the other does not).
- `test_codex_exec_and_patch_blocks` (tags, counts, errors, tokens; patch lines carry no
  target), `test_codex_output_mimicking_exec_shows_no_data` (output lines `exec` then
  `customer42`: target `?`), `test_codex_model_only_from_file_head` (a `model:` line
  mid-tail is ignored), `test_codex_unknown_format_yields_no_calls`.
- `test_first_read_is_bounded_by_tail_bytes`, `test_steady_poll_reads_only_new_bytes`,
  `test_partial_last_line_waits_for_newline`, `test_aligned_tail_start_keeps_first_record`
  (tail start exactly after a newline keeps that record),
  `test_overlimit_line_across_polls_is_dropped` (a line grows past `MAX_LINE` over three
  polls, and in one chunk with its newline; the next good line is parsed; pending never
  exceeds `MAX_LINE`), `test_tail_jump_clears_codex_continuation` (a jump while an `exec`
  waits for its command: the first line after the jump is not taken as a command).
- `test_rotation_between_polls_resets_offset`, `test_truncate_between_fstat_and_read_then_regrow`
  (no bytes skipped or doubled after regrowth), `test_path_replaced_between_stat_and_open_reads_new_file_only`
  (old and new files hold distinct records; only new records appear).
- `test_unmatched_calls_keep_state_bounded` (1 000 calls without results over 50 polls:
  state size constant), `test_rate_is_exact_across_a_minute_boundary` (80 calls in 60 s
  spanning a minute boundary: `tools_per_min` 80).
- `test_invalid_utf8_and_junk_lines_are_ignored`, `test_wrong_types_in_tool_input_are_ignored`,
  `test_parser_never_raises_on_random_bytes` (seed 1234, 500 cases),
  `test_parser_error_on_one_line_keeps_the_next`.
- `test_cache_evicts_a_previously_cached_path`, `test_cache_lru_cap`,
  `test_transcript_reader_never_writes`, `test_request_out_path_outside_run_dir_is_ignored`.

Page (S2):
- `test_html_routes_serve_200_and_schema_2`: at every S2 commit, `/` and `/fragment`
  return 200 with the expected section ids and `/api/state` has `schema` 2.
- `test_transcript_secrets_reach_no_route`: as class 1. Planted values:
  `ghp_DEMOTOKEN1234567`, `AKIAABCDEFGH1234`, `password hunter2`, `https://user:pw@host/x`,
  `/home/someone/private-dir/`, a 40-hex string, `customer42`, in Bash commands, Edit and
  Write fields, Grep `pattern`, WebFetch `url`, Agent `prompt` and `subagent_type`, text,
  thinking, tool results, `result.result`, init `cwd`, and Codex user, codex, exec and
  output blocks and patch paths.
- `test_missing_transcript_card_says_starting`, `test_codex_fix_card_says_outside`,
  `test_unreadable_transcript_marks_card_only` (skipped as root).
- `test_kpi_counts`, `test_live_cards_one_per_executing_run`, `test_live_card_fields`,
  `test_max_live_cap`, `test_feed_merges_calls_and_events_newest_first`, `test_feed_limit`.
- `test_worker_across_executing_applied_evicted` (one worker over three polls: the drawer
  lists the run after it is applied; its calls stay in the feed after cache eviction).
- `test_drawer_runs_belong_to_their_slice`, `test_orphan_worker_has_no_drawer_link`,
  `test_drawer_keeps_finished_run_history`, `test_drawer_files_from_claude_edit_and_write`,
  `test_drawer_sources_capped_and_fallback_hooks` (slice 31 has a pipeline card with
  `data-card-key` and no source), `test_drawer_run_status_and_verdict_from_result_header`.
- `test_owner_question_shown_in_full_with_real_hint` (question, detail and every option
  verbatim, including an unprefixed value such as `hunter2`; the hint reads
  `devloop-state p answer KEY <option>` with the real key),
  `test_hold_answer_uses_hold_key`.
- `test_schema_is_two`, `test_tool_tags_frozen`, `test_view_keys_match_schema`,
  `test_no_persona_names_in_labels` (reviewer names and persona kind names such as
  `dennis` and `codex-review` absent from role labels, words and feed lines; icon ids such
  as `test` and `docs` allowed).
- `test_every_run_kind_has_harness_icon_and_transcript`, `test_codex_kinds_derived_from_registry`,
  `test_every_event_kind_has_a_tag`, `test_activity_and_feed_read_events_once`.
- Render: `test_page_sections_in_order`, `test_sparkline_is_inline_svg`,
  `test_worker_card_has_role_icon_not_avatar`, `test_fragment_size_budget` (16 workers, 40
  feed items, 30 drawers: under 200 KB), `test_no_em_dash_in_assets_and_copy`,
  `test_css_tokens_meet_aa` and `test_css_light_and_dark_define_the_same_tokens` (tag
  tokens included), `test_phone_rules_present`.

Control (S3):
- `test_ask_records_class_and_detail`, `test_tech_question_is_decided_now`
  (applied = recommended, event `question-decided`, Slack line "Tech lead decided"),
  `test_product_question_waits`, `test_class_defaults_to_product`,
  `test_old_questions_page_reads_as_product`, `test_wait_flag_forces_product`.
- `test_other_answer_stored_and_validated` (`other: use the slower path` accepted;
  empty, newline or 501 characters refused; refused on a hold),
  `test_answer_with_old_version_is_refused`, `test_cli_answer_unchanged`,
  `test_override_of_tech_decision_records_owner_answer`.
- `test_cli_and_dashboard_pause_share_one_path`, `test_tick_spawn_refused_while_locked`,
  `test_spawn_detached_leaves_no_child_fds`, `test_wrapper_wakes_tick_through_spawn_detached`.
- `test_every_post_route_refuses_without_token_and_origin`, `test_post_body_limits_and_unknown_fields`,
  `test_double_post_records_one_answer`, `test_stale_token_after_restart_is_refused`,
  `test_unknown_question_key_is_refused`, `test_tick_debounce`,
  `test_get_routes_write_nothing`, `test_post_success_json_and_redirect`,
  `test_each_write_appends_its_event` (`via=dashboard`), `test_post_log_has_no_values`,
  `test_server_env_is_not_headless` (the detached server child has no
  `DEVLOOP_HEADLESS`).
- `test_decisions_drawer_has_actions` (Go with recommended, each option, free-text box,
  Copy for Claude with the questions file path in its text), `test_decided_for_you_list`,
  `test_controls_render_paused_and_tick_running`.
- JS (no pytest runner; VERIFY in Task 13): drawers by click and hash, kept across polls;
  form input kept across swaps; fetch submit shows the server message; copy fallback on a
  LAN IP; 390 px with no horizontal scroll, both themes.

## Placement

| New file | Directory | Why there | Sits beside |
|---|---|---|---|
| `transcript.py` | `devloop/dash/` | dashboard-only reader; `dash/` has 6 modules | `memory.py` |
| `live.py` | `devloop/dash/` | live view sections; `view.py` is 640 lines | `view.py` |
| `render_live.py` | `devloop/dash/` | live HTML; `render.py` is 485 lines | `render.py` |
| `render_control.py` | `devloop/dash/` | decisions drawer, decided list, controls | `render.py` |
| `actions.py` | `devloop/dash/` | POST checks and `ACTIONS` handlers | `server.py` |
| `conftest.py`, `transcript_fixture.py`, `test_transcript.py`, `test_live.py`, `test_controls.py` | `tests/devloop/dash/` | `tests/devloop/` is over the fan-out bar | `tests/devloop/dash_fixture.py` |

Present consumers: `transcript.py` (S1) is used by `live.py` (S2); the plan pairs them under
proportionality's plan-slice exception, and S2 lands before the row closes. Everything else
has its caller in the same slice.

Expected size: 5 new source modules (about 330, 280, 280, 220, 200 lines); 5 new test
files (about 1 100 lines); edits to `view.py`, `server.py`, `render.py`, `dash.css`
(rewrite, about 300 lines), `dash.js` (about +180), `lifecycle.py`, `runs.py`, `wrap.py`,
`status.py`, `notes.py`, `store.py`, `tick.py`, `state_cli.py`,
`skills/dev-loop/TICK.md`, `PLAYBOOK.md`, `SKILL.md`,
`dash_fixture.py`, existing dash tests, `mutations.sh`. New public names: those in each
task's Interfaces block.

## Slices

| Slice | Tasks | Risk | Ships | Gate |
|---|---|---|---|---|
| S1 transcript reader | 1, 2, 3 | R1 | `transcript.py`, registry fields | Outside reviewer; T1 = `pytest tests/devloop/dash tests/devloop/test_dash_view.py tests/devloop/test_runs_dispatch.py tests/devloop/test_wrap.py` plus ruff |
| S2 mission-control page | 4, 5, 6, 7, 8 | R1 | schema 2, live model, new page, drawers, leak sentry; one PR; every commit serves 200 on `/` and `/fragment` | Outside reviewer; T1 = `pytest tests/devloop -k dash` plus `tests/devloop/dash`; dash mutations; VERIFY record |
| S3 loop control | 9, 10, 11, 12, 13 | R2 | question class and detail, free-text answers, one write path, POST actions, decisions drawer, controls | Senior reviewer then Outside reviewer; T1 = `pytest tests/devloop -k "dash or notes or tick or state or wrap or status"`; control mutations; VERIFY record |

T2 at each gate runs T1 on the exact head plus that slice's mutations. S3 touches a
serialized format (questions page) and a trust boundary, so its gate escalates to the full
suite once on its merge head, diffed against the stored main baseline.

## Pre-mortem

- **A secret or customer value reaches the page through a command.** Commands show only
  allowlisted names, after refusing quoted or piped forms; Codex patch names never show;
  `_seal` re-checks; the sentry asserts 200, positive facts and absence.
- **A big or odd transcript stalls polls or invents calls.** Bounded reads, a per-line
  length check before parsing, cursor by bytes read, continuation cleared on every
  discontinuity; tests count bytes and records.
- **Stream-json or Codex text changes shape.** Unknown fields and lines are ignored; cards
  fall back to elapsed time.
- **An owner answer lands on the wrong version, twice, or from a stale tab.** Version check
  inside the questions lock; per-process token; same-origin check; fixed refusal messages.
- **The dashboard and the CLI disagree on pause or answers.** One function each, shared;
  `test_cli_and_dashboard_pause_share_one_path`.
- **A GET starts writing.** `test_get_routes_write_nothing` walks every GET route.
- **A tech question that the owner would care about is auto-decided.** Default class is
  product; the ratchet rule sends repeated growth to an Architect review instead of an
  entry; every tech decision posts to Slack and shows with Override.
- **Anyone on the LAN can answer when `dash.host` is opened.** Accepted under the
  accidents-only model; the default bind is loopback. Recorded as an owner decision.
- **The server runs with `DEVLOOP_HEADLESS=1` and refuses answers.** `lifecycle` removes
  it from the server child env; tested.
- **Two writers in this repo.** S1 touches mostly new files. S2 and S3 edit files another
  builder may be changing; each task starts by reading the tree. One writer at a time.

## Review record

Outside reviewer, plan round 1: GO-WITH-FIXES. All findings folded, with coordinator
decisions:
- V1 Codex and shell targets: allowlist `PUBLIC_COMMANDS`, quoted or piped forms give `?`,
  Codex patch names and mid-tail model lines never published (Claude and Codex parsing).
- V1 owner options: coordinator decision replaces the reviewer's fix. Full question, detail
  and option text shown; the hint uses the real key (`hold:KEY` for holds) and real values.
- V1 cursor and framing: cursor by bytes read, aligned starts, per-line limit, continuation
  cleared on discontinuity (Task 3 tests).
- V1 Claude usage: structural parse, text-only messages counted, per-block `is_error`,
  separate token total.
- V1 unbounded state and rate: newest-call flag only, 60 one-second buckets.
- V1 `codex-fix` outside the run dir: transcript state `outside`.
- V1 finished runs: history selected in the run walk before the slot filter; feed log apart
  from the cache.
- V1 31st slice: checklist-only fallback from the pipeline card.
- V1 S2 not shippable: old S2 and S3 merged into one S2; every commit serves 200.
- DESIGN: leak mutation that survives sealing; reader mutations that hit their tests and a
  baseline-green runner; one harness registry; one event read with tags on descriptors;
  new tests in `tests/devloop/dash/`.
- V2-LOG: no inner cache lock (the server serializes builds); `read_states` passes one
  mapping to cards, feed and KPI; the name assertion is scoped to labels and words.

## Task 1: Registry fields, safe extractors and the Claude line parser

**Files:**
- Create: `devloop/dash/transcript.py`, `tests/devloop/dash/conftest.py`,
  `tests/devloop/dash/transcript_fixture.py`, `tests/devloop/dash/test_transcript.py`
- Modify: `devloop/runs.py` (`Kind` gains `harness`, `transcript`), `devloop/wrap.py`
  (`CODEX_KINDS` derived)

**Interfaces:**
- Produces: `runs.Kind(slot, deadline_s, idle_s, reader, writes_worktree, pinned,
  harness, transcript)`; `runs.HARNESSES = ("claude", "codex", "script")`;
  `runs.TRANSCRIPTS = ("stream-json", "codex-text", "outside", "none")`.
  Values: planner, builder, dennis, docs `claude`/`stream-json`; codex-review,
  codex-builder `codex`/`codex-text`; codex-fix `codex`/`outside`; test, duty
  `script`/`none`. `wrap.CODEX_KINDS = tuple(k for k, s in runs.KINDS.items() if s.harness
  == "codex")`.
- `transcript.TOOL_TAGS`, `TOOL_KINDS`, `PUBLIC_COMMANDS`, `CODEX_SEARCH`, `CODEX_READ`;
  `safe_target(name: object) -> str`; `shell_word(cmd: object) -> str`;
  `Call = namedtuple("Call", "tag target at call_id")`;
  `ClaudeFacts` dataclass: `calls: list[Call]`, `results: list[tuple[str, bool]]`,
  `model: str | None`, `usage: tuple[str, int, int] | None` (message id, output tokens,
  context tokens); `parse_claude_line(raw: bytes) -> ClaudeFacts | None`.

- [ ] **Step 1: Write the failing tests** `test_every_run_kind_has_harness_icon_and_transcript`
  (harness and transcript part), `test_codex_kinds_derived_from_registry`,
  `test_shell_word_cases`, `test_public_commands_pass_safe_target`,
  `test_safe_target_cases`, `test_tool_kinds_and_targets_from_claude`,
  `test_text_only_message_counts_tokens` (parser part), `test_two_results_with_different_error_flags`
  (parser part), `test_invalid_utf8_and_junk_lines_are_ignored`,
  `test_wrong_types_in_tool_input_are_ignored`, `test_parser_never_raises_on_random_bytes`.
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_transcript.py -q`. Expected: FAIL,
  `ModuleNotFoundError: devloop.dash.transcript`.
- [ ] **Step 3: Implement** as in "Claude and Codex parsing". No file access in this task.
  Update every `Kind(...)` in `runs.py`.
- [ ] **Step 4: Run** the file plus `pytest tests/devloop/test_wrap.py
  tests/devloop/test_runs_dispatch.py tests/devloop/test_dash_shared.py -q`, ruff check and
  format. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): parse worker tool calls into safe facts`, last line
  `cam`.

## Task 2: Codex text parser

**Files:**
- Modify: `devloop/dash/transcript.py`, `tests/devloop/dash/transcript_fixture.py`,
  `tests/devloop/dash/test_transcript.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `class CodexParser` with `feed(lines: list[bytes], seen_at: str) ->
  tuple[list[Call], list[tuple[str, bool]]]` (new calls; finished call ids with error
  flag), `reset_continuation() -> None`, `tokens_out: int | None`;
  `codex_head_model(head: bytes) -> str | None` (first 4 KiB only).

- [ ] **Step 1: Write the failing tests** `test_codex_exec_and_patch_blocks`,
  `test_codex_output_mimicking_exec_shows_no_data`, `test_codex_model_only_from_file_head`,
  `test_codex_unknown_format_yields_no_calls`, Codex half of the fuzz test.
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_transcript.py -q -k codex`. Expected:
  FAIL on `CodexParser`.
- [ ] **Step 3: Implement** the rules in "Claude and Codex parsing".
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): read Codex exec output as tool calls`, last line
  `cam`.

## Task 3: Bounded tail reader, file cache and feed log

**Files:**
- Modify: `devloop/dash/transcript.py`, `tests/devloop/dash/test_transcript.py`,
  `tests/devloop/mutations.sh`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the constants in "Performance limits";
  `class FileState` with `ident`, `offset`, `mtime_ns`, `pending: bytearray`,
  `skip_to_newline: bool`, `partial: bool`, `calls: deque[Call]` (`KEEP_CALLS`),
  `tools: int`, `errors: int`, `newest_id: str | None`, `newest_done: bool`,
  `token_total: int`, `seen_tokens: OrderedDict[str, int]` (`DEDUP_IDS`),
  `context: int | None`, `model: str | None`, `minutes: dict[str, int]`,
  `seconds: dict[int, int]` (epoch second to count, last `RATE_SECONDS`), `state: str`,
  `bytes_read: int`, `codex: CodexParser | None`; methods `doing() -> tuple[Call, bool] |
  None`, `rate(now_s: int) -> int`, `spark(now) -> list[int]`.
- `class Cache` (no inner lock; callers serialize builds): `entries: dict[Path,
  FileState]`, `feed_log: deque` (`FEED_LIMIT` safe facts: `(at, tag, target, role, key)`),
  `read(path: Path | None, transcript: str, executing: bool, label: tuple[str, str]) ->
  FileState`, `collect(keep: set[Path]) -> None`.
- `transcript_path(rd: Path, req: dict) -> Path | None` from the kind's `transcript`.

- [ ] **Step 1: Write the failing tests** (S1 list under Acceptance tests from
  `test_first_read_is_bounded_by_tail_bytes` to `test_request_out_path_outside_run_dir_is_ignored`,
  plus the file-level halves of the token and result tests).
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_transcript.py -q`. Expected: FAIL on
  `Cache`.
- [ ] **Step 3: Implement** `Cache.read` in this order:

```text
st = os.stat(path)                    # OSError: state starting/none (outside for transcript=outside)
if identity, size and mtime unchanged: return entry
with open(path, "rb") as fh:          # OSError: state unreadable
    fs = os.fstat(fh.fileno()); ident = (fs.st_dev, fs.st_ino); end = fs.st_size
    if entry.ident and (ident != entry.ident or end < entry.offset): discontinuity(entry)
    if (entry.offset == 0 and end > TAIL_BYTES) or end - entry.offset > STEP_BYTES:
        start = end - TAIL_BYTES; discontinuity(entry) if entry.offset
        fh.seek(start - 1); aligned = fh.read(1) == b"\n"; skip_to_newline = not aligned
    else: start = entry.offset
    fh.seek(start); data = fh.read(end - start)
entry.offset = start + len(data); entry.mtime_ns = fs.st_mtime_ns; entry.ident = ident
frame: pending + data split on b"\n"; any complete line over MAX_LINE is dropped (discontinuity);
       pending over MAX_LINE is dropped with skip_to_newline (discontinuity)
each complete line: try parse; except the listed errors: count, continue
each new call: append to entry.calls and cache.feed_log with label
```

  `discontinuity(entry)` clears pending, Codex continuation and `newest_done`, and sets
  `partial`. Add `mutate` entries for classes 2, 3, 6, 7, 8 with their named tests, and
  change the runner: run each entry's tests unmutated first (`RED-BASE` when red), and count
  a kill only on pytest exit code 1.
- [ ] **Step 4: Run** the file, `pytest tests/devloop -k dash -q`, and the new mutations.
  Expected: PASS; each mutation killed.
- [ ] **Step 5: Commit** `feat(devloop): tail worker output files with a bounded cache`,
  last line `cam`.

## Task 4: Schema 2 with the renderer kept serving

**Files:**
- Modify: `devloop/dash/view.py`, `devloop/dash/render.py`, `devloop/status.py`,
  `tests/devloop/test_dash_view.py`, `tests/devloop/test_dash_server.py`,
  `tests/devloop/test_dash_render.py`, `tests/devloop/test_dash_shared.py`

**Interfaces:**
- Consumes: the tree split at `7af0177` (`_needs_you`, `_problems`).
- Produces: `SCHEMA = 2`; `status.EventDesc = namedtuple("EventDesc", "words tag")`,
  `status.EVENTS`, `EVENT_KINDS = tuple(EVENTS)`, `event_tag(kind) -> str | None`;
  `ctx.events` read once; needs_you fields `answer_key`, `detail`, `klass`, `version`
  (detail null, klass `product`, version from `notes.question_version` once Task 9 adds it;
  until then null); scalar kind `owner_text` replacing `line` for question fields;
  `render._needs` updated for the new fields and the hold hint.

- [ ] **Step 1: Write the failing tests** `test_schema_is_two`,
  `test_html_routes_serve_200_and_schema_2`, `test_owner_question_shown_in_full_with_real_hint`,
  `test_hold_answer_uses_hold_key` (hint part), `test_every_event_kind_has_a_tag`,
  `test_activity_and_feed_read_events_once` (activity part); update the schema 1 pin
  (`tests/devloop/test_dash_server.py:56`).
- [ ] **Step 2: Run** `pytest tests/devloop -k dash -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Hold items read their `hold:KEY` record when present for
  question, options and recommended. Hint copy: "Answer: devloop-state PROJECT answer
  ANSWER_KEY <option>, or on the questions page."
- [ ] **Step 4: Run** the same. Expected: PASS, `/` and `/fragment` 200.
- [ ] **Step 5: Commit** `feat(devloop): move the dashboard view to schema 2`, last line `cam`.

## Task 5: Live read model `devloop/dash/live.py`

**Files:**
- Create: `devloop/dash/live.py`, `tests/devloop/dash/test_live.py`
- Modify: `devloop/dash/view.py` (run walk keeps history; new keys and scalar kinds),
  `devloop/dash/server.py` (`ViewCache` owns one `transcript.Cache`),
  `devloop/dash/render.py` (replace the workers table with a minimal live list so every
  commit serves 200), `tests/devloop/dash_fixture.py`, `tests/devloop/test_dash_view.py`

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces: `ICONS: dict[str, str]` (every `runs.KINDS` key); `read_states(ctx, cache) ->
  dict[str, FileState]` (one read per selected run, keyed by run ref); `live_cards(ctx,
  states, now) -> list[dict]`; `waiting_runs(ctx) -> list[dict]`; `feed(ctx, cache, now) ->
  list[dict]` (events plus `cache.feed_log`); `kpi(ctx, view, states, now) -> dict`;
  `drawers(ctx, cache, cards) -> list[dict]`; `build_view(project, proc="/proc", now=None,
  cache=None) -> dict`. `_Ctx.history: dict[str, list[tuple[rd, req]]]` (newest
  `DRAWER_RUNS` per active slice, selected before the slot filter).

- [ ] **Step 1: Write the failing tests** (S2 list from `test_missing_transcript_card_says_starting`
  to `test_drawer_run_status_and_verdict_from_result_header`, plus `test_tool_tags_frozen`,
  `test_no_persona_names_in_labels`, the icon part of
  `test_every_run_kind_has_harness_icon_and_transcript`, the feed part of
  `test_activity_and_feed_read_events_once`).
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_live.py tests/devloop/test_dash_view.py
  -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Model: for Claude kinds the value after `--model` in
  `req["argv"]`, else `FileState.model`, sealed as `model` or None. Call
  `cache.collect(keep)` once per build.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q` and `pytest tests/devloop/dash -q`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): add live worker activity to the dashboard model`,
  last line `cam`.

## Task 6: Render the mission-control page

**Files:**
- Create: `devloop/dash/render_live.py`
- Modify: `devloop/dash/render.py`, `tests/devloop/test_dash_render.py`

**Interfaces:**
- Produces: `kpi_strip(v, now)`, `attention(v, now)`, `worker_grid(v, now)`,
  `sparkline(counts: list[int])` (`<svg class="spark" viewBox="0 0 60 16" role="img"
  aria-label="Tool calls per minute, last 15 minutes">`, one `<polyline>`),
  `role_icon(icon: str)` (inline `<svg data-icon=...>`), `feed(v, now)`,
  `drawer_sources(v, now)`, all `-> str`. Pipeline cards carry `data-card-key`.
  `render_page` adds `<aside id="drawer" hidden aria-label="Details">` outside `#app-body`.
- Copy: KPI labels "Workers", "Needs you", "Merged today", "Tools / min", "Free memory",
  "Last tick"; empty states "Nothing needs you right now.", "No workers running.",
  "No activity yet."; cards "starting" and "activity not shown for this worker"; drawer
  "This slice is no longer active."

- [ ] **Step 1: Write the failing tests** render list under S2, `test_drawer_sources_capped_and_fallback_hooks`
  (render part); update `test_page_renders_from_fixture_offline`,
  `test_empty_project_shows_empty_states`, `test_html_escapes_planted_markup` (plant in a
  configured role label and in a question's detail), `test_counts_read_naturally`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with the reused helpers; every value through `html.escape`.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): render the dashboard as mission control`, last line
  `cam`.

## Task 7: Dense theme, phone layout and the slice drawer

**Files:**
- Modify: `devloop/dash/web/dash.css`, `devloop/dash/web/dash.js`,
  `tests/devloop/test_dash_render.py`

**Interfaces:**
- Produces: tokens on `:root` (light), dark under `prefers-color-scheme` and
  `[data-theme=dark]`, tag tokens `--t-read` to `--t-other`, each AA on its chip. JS
  `POLL_MS = 4000`, `openDrawer(kind, key)`, `closeDrawer()`, `fillDrawer()` after every
  swap.

- [ ] **Step 1: Write the failing tests** `test_phone_rules_present`; extend the two CSS
  token tests; keep `test_static_assets_have_no_inner_html_from_data`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Desktop: KPI 6 columns, worker grid
  `repeat(auto-fill, minmax(300px, 1fr))`, feed and pipeline side by side above 1100 px,
  drawer a 420 px right panel. Phone (max 640 px): KPI 3 by 2, one column, feed rows a
  two-line grid with `minmax(0, 1fr)` and ellipsis, drawer `position: fixed; inset: 0`.
  `fillDrawer` clones `#ds-KEY` children with `cloneNode(true)`; without a source it clones
  the checklist from `[data-card-key=KEY]`; with neither it shows the fixed text. Escape,
  close and Back close; focus moves in and back.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): add the slice drawer and the dense theme`, last line
  `cam`.

## Task 8: Leak sentry, page mutations and browser check

**Files:**
- Modify: `tests/devloop/test_dash_server.py`, `tests/devloop/mutations.sh`

- [ ] **Step 1: Write** `test_transcript_secrets_reach_no_route` as class 1 (asserts 200,
  positive facts, absences). Apply the class 1 mutation by hand: the test fails on the
  absence assertion with a 200 response. Restore.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_server.py -q`. Expected: PASS.
- [ ] **Step 3:** Add `mutate` entries for classes 1, 5 (drawer part), 9 (registry and event
  parts) and 10, each with its test; run `tests/devloop/mutations.sh`; all dash entries
  killed, none `RED-BASE`.
- [ ] **Step 4: VERIFY in a browser.** A scratch copy of
  `/home/cam/scratch/devloop-dash/make_fixture.py` writes transcript fixtures (scratch only).
  Start `devloop-dash demo --host 0.0.0.0`; at 1440 px and 390 px, light and dark: open a
  worker drawer, wait two polls (drawer, scroll, filters hold); append transcript lines
  (doing-now and feed update within 8 s); open slice 31 (checklist only), move it to DONE
  ("no longer active"); no horizontal scroll at 390 px. Record steps and screenshots in
  the PR body.
- [ ] **Step 5: Commit** `test(devloop): guard transcript facts on every route`, last line
  `cam`.

## Task 9: Question class, detail, versions and free-text answers

**Files:**
- Modify: `devloop/notes.py`, `devloop/state_cli.py`,
  `devloop/status.py`, `skills/dev-loop/TICK.md`, `skills/dev-loop/PLAYBOOK.md`,
  `skills/dev-loop/SKILL.md`
- Test: `tests/devloop/dash/test_controls.py`, `tests/devloop/test_notes.py`

**Interfaces:**
- Produces: `notes.ask(project, key, *, question, options, recommended, wait=False,
  detail="", klass="product") -> dict` (`klass` in `("tech", "product")`; product when
  `wait`; `wait` stored as `klass == "product"`); `notes.question_version(q: dict) -> str`;
  `notes.answer(project, key, choice, *, expect_version=None, via="cli") -> None`;
  `notes.OTHER_PREFIX = "other: "`; `notes.valid_choice(q, choice) -> bool`;
  questions page fields `Detail` (`_line(detail, 1000)`) and `Class`; events `question-decided`
  (tech) and `question-answered` with `via`; `status.EVENTS` entries with tags DECIDED and
  ANSWERED. CLI: `ask --detail TEXT --class tech|product`.
- Slack (`_post_question`): tech reads "Tech lead decided KEY: OPTION. Override on the
  dashboard."; product keeps today's waiting wording.

- [ ] **Step 1: Write the failing tests** S3 notes list (`test_ask_records_class_and_detail`
  to `test_override_of_tech_decision_records_owner_answer`, `test_wait_flag_forces_product`)
  in `tests/devloop/dash/test_controls.py`; keep `tests/devloop/test_notes.py` green.
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_controls.py tests/devloop/test_notes.py
  -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The version check, the answered check and the choice check run
  inside `store.locked(_lock_for(project))` in `_apply_answer`. `read_answers` uses
  `valid_choice` too. Docs: TICK step 10 says to pass `--class`, `--detail` (what is
  happening, why it matters, what each option does, in short plain sentences), with the
  product and tech definitions above and "when unsure, product". PLAYBOOK gets: the
  question row says an `other: <text>` answer is the owner's instruction and the tick
  follows it; a `[judgment]` rule and a Scenarios row "Fix needs a ratchet, cap or
  allowlist to grow" carrying the ratchet rule in generic words (tech when a planned fix
  will retire the entry or it is a legitimate registry site, the answer names the fix; no
  fix planned means log one first; 3 or more places with the same class means no entry
  and an Architect tech review for a central handler), modelled on the Decoy playbook rows.
  SKILL.s answer section names the dashboard.
- [ ] **Step 4: Run** `pytest tests/devloop -k "notes or dash or state" -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): classify owner questions and accept typed answers`,
  last line `cam`.

## Task 10: One write path for pause, resume and tick

**Files:**
- Modify: `devloop/store.py`, `devloop/tick.py`, `devloop/wrap.py`, `devloop/state_cli.py`,
  `devloop/status.py`, `devloop/dash/lifecycle.py`
- Test: `tests/devloop/dash/test_controls.py`

**Interfaces:**
- Produces: `store.set_paused(project, on: bool, via: str) -> None` (touch or unlink
  `PAUSE`, append `loop-paused` or `loop-resumed`); `tick.lock_held(project) -> bool`;
  `tick.spawn_detached(project, via: str) -> None` (the old `wrap._wake_tick` body, output
  appended to `cron.log`, event `tick-requested` when `via != "wrapper"`); the CLI
  `pause`/`resume` call `set_paused(..., via="cli")`; `wrap.finish` calls
  `tick.spawn_detached(project, "wrapper")`; `lifecycle` drops `DEVLOOP_HEADLESS` from the
  server child env.

- [ ] **Step 1: Write the failing tests** `test_cli_and_dashboard_pause_share_one_path`,
  `test_tick_spawn_refused_while_locked` (lock part), `test_spawn_detached_leaves_no_child_fds`,
  `test_wrapper_wakes_tick_through_spawn_detached`, `test_server_env_is_not_headless`.
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_controls.py -q -k "pause or spawn or
  wakes or headless"`. Expected: FAIL.
- [ ] **Step 3: Implement** by moving code, leaving no old copy or alias.
- [ ] **Step 4: Run** `pytest tests/devloop -k "tick or wrap or state or dash" -q`. Expected:
  PASS.
- [ ] **Step 5: Commit** `refactor(devloop): share one path for pause, resume and tick
  starts`, last line `cam`.

## Task 11: POST actions with token, origin and version checks

**Files:**
- Create: `devloop/dash/actions.py`
- Modify: `devloop/dash/server.py`
- Test: `tests/devloop/dash/test_controls.py`, `tests/devloop/test_dash_server.py`

**Interfaces:**
- Produces: `server.ROUTES: dict[tuple[str, str], handler]`; `Handler.do_POST`;
  `actions.ACTIONS = {"/act/answer": answer, "/act/pause": pause, "/act/resume": resume,
  "/act/tick": run_tick}`; `actions.check_post(handler) -> dict[str, str]` (token via
  `hmac.compare_digest`, origin, `Content-Length <= POST_BYTES`, `parse_qs` with
  `strict_parsing=True`, `max_num_fields=8`, exact field set per action);
  `actions.Refused(code: int, message: str)`; `DashServer.token: str`,
  `DashServer.last_tick: float`. CSP `form-action 'self'`. Each handler returns a fixed
  message; `_send` writes JSON or a 303 by `Accept`.

- [ ] **Step 1: Write the failing tests** S3 server list
  (`test_every_post_route_refuses_without_token_and_origin` to
  `test_post_log_has_no_values`) and update `test_only_known_routes_and_methods` and
  `test_every_response_sends_security_headers` (POST and 303 included).
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_controls.py tests/devloop/test_dash_server.py
  -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `_post` order: method and path in `ACTIONS` (else 404), size,
  token, origin, fields, then the handler; `NotesError` and `Refused` map to the fixed
  messages in Exit paths; any other exception gives the fixed 500 and a class-name log.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`, `pytest tests/devloop/dash -q`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): let the owner act from the dashboard`, last line
  `cam`.

## Task 12: Decisions drawer, decided list and loop controls

**Files:**
- Create: `devloop/dash/render_control.py`
- Modify: `devloop/dash/view.py` (`decided`, `controls`, needs_you `version`, `detail`,
  `klass`), `devloop/dash/render.py`, `devloop/dash/web/dash.js`,
  `devloop/dash/web/dash.css`
- Test: `tests/devloop/dash/test_controls.py`, `tests/devloop/test_dash_render.py`

**Interfaces:**
- Produces: `render_control.question_sources(v, token, copy_path) -> str` (one hidden
  `#qs-KEY` per open owner question: question, detail, options with the recommended one
  marked, "Go with recommended" form, one form per other option, free-text form "Your
  answer" with Save, "Copy for Claude" button with `data-copy` text);
  `decided_list(v, token) -> str` ("Decided for you", each with Override opening the same
  drawer kind); `controls(v, token) -> str` (Pause or Resume, "Run a tick now", disabled
  with "A tick is running" while `tick_running`). `render_page` and `render_fragment` take
  `token` and `copy_path` from the server, never from the view.
- JS: `#q=KEY` opens the question drawer; forms submit through `fetch` with `Accept:
  application/json` and show the message in the drawer; the drawer's typed text survives
  swaps (kept by textarea `id`); copy uses `navigator.clipboard.writeText` when
  `isSecureContext`, else selects a read-only `<textarea>` and shows "Press Ctrl+C to
  copy."; a question drawer whose source is gone reads "This question was answered."

- [ ] **Step 1: Write the failing tests** `test_decisions_drawer_has_actions`,
  `test_decided_for_you_list`, `test_controls_render_paused_and_tick_running`.
- [ ] **Step 2: Run** `pytest tests/devloop/dash/test_controls.py -q -k "drawer or decided or
  controls"`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`, `pytest tests/devloop/dash -q`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): add the decisions drawer and loop controls`, last
  line `cam`.

## Task 13: Control mutations and browser check

**Files:**
- Modify: `tests/devloop/mutations.sh`

- [ ] **Step 1:** Add `mutate` entries for classes 3 (GET write), 4, 5 (hold key), 6 (POST
  body), 7 (version outside the lock), 9 (pause path, `CODEX_KINDS`) and 10 (default
  class), each with its test.
- [ ] **Step 2: Run** `tests/devloop/mutations.sh`. Expected: every entry killed, none
  `RED-BASE`.
- [ ] **Step 3: VERIFY in a browser** on the fixture server at 1440 px and 390 px, light
  and dark: answer a product question with "Go with recommended" (event and page update);
  double click (one answer, "Already answered"); answer from a tab opened before the tick
  re-asked (refused, "changed"); type an `other:` answer; override a tech decision; Pause,
  Resume, "Run a tick now" twice (second refused); restart the server and post from the
  old tab ("out of date"); "Copy for Claude" on loopback and on the LAN IP (fallback).
  Record steps and screenshots in the PR body.
- [ ] **Step 4: Run** the S3 T1 selection on the head. Expected: PASS.
- [ ] **Step 5: Commit** `test(devloop): guard the dashboard write path`, last line `cam`.

## Builder brief notes

Commit subjects follow Conventional Commits, 72 characters max, no body unless it explains
why, final line `cam`, no tool-attribution trailers. Test depth: T0 the touched file's
tests per edit, T1 the slice's selection per commit, T2 at the gate on the exact head; S3
also gets one full suite on its merge head (serialized format and trust boundary).
Name the defect class of each finding you fix; write tests first and see them red; attack
your own diff with the listed mutations; never widen a limit or skip a test to get green;
stop and report if a catalogued class repeats. One writer at a time in this worktree.
