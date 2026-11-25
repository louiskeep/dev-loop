---
Status: plan
updated: 2026-11-25
---

# Dev Loop Dashboard: Mission Control Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
profiles/local-operations, feature-dev, reuse-first, proportionality, architecture,
cross-cutting-concerns, security, performance, observability-and-resilience,
api-and-compatibility, testing, workspace-and-vcs.

Risk: **R1** (read-only, local, reversible; no new writes, no new routes, no commands).
Review: Outside reviewer only, on the plan and on each slice head. Repo
`/home/cam/dev-loop-plugin`, branch `feat/headless-loop`, planned from head `0b867f9`.

**Goal (owner, 2026-11-25):** "I want something more like this"
(github.com/Dri-water/observe-agents-do-things, MIT). Rebuild the dashboard in that dense
mission-control style and keep everything only the loop knows: slices, phases, checklists,
owner questions, holds, capacity, memory, merges.

**Spec:** this plan, plus the base plan `docs/plans/2026-11-25-devloop-dashboard.md`, whose
guarantee, routes, lifecycle and role-label rules stay in force. Reference screenshots:
`/home/cam/scratch/oadt-ref/mission.png`, `mission-drawer.png`, `mission-mobile.png`.
Current look: `/home/cam/scratch/devloop-dash/*.png`.

**Architecture:** one new module, `devloop/dash/transcript.py`, reads the tail of each live
worker's output file (Claude stream-json or Codex text), keeps an in-memory cache per file,
and returns only structured tool-call facts: a fixed kind tag, a safe short target, a time.
A second new module, `devloop/dash/live.py`, turns runs plus those facts into worker cards,
the activity feed, the KPI numbers and the per-slice drawer data. `view.py` adds them to the
sealed view (schema 2). `render.py` keeps the page shell and pipeline; a new
`render_live.py` renders the KPI strip, attention bar, worker cards, feed and drawer sources.
`dash.js` keeps polling `/fragment` and adds the drawer. No new route.

**Tech Stack:** Python 3.11 stdlib (`os`, `json`, `re`, `collections`, `threading`), vanilla
JS, CSS custom properties, inline SVG. Tests: pytest with the existing `proj`, `home`,
`dash` fixtures. No Hypothesis in the repo, so fuzz tests use seeded `random`.

## Global Constraints

- Read-only: the dashboard never writes loop state and runs no command. Transcript files
  open with mode `"rb"` only.
- Stdlib Python only. No external network, CDN, web font or JS library.
- Routes stay exactly `/`, `/fragment`, `/api/state`, `/static/dash.css`, `/static/dash.js`,
  with ETag and 304 as now.
- Role labels from `project.role_label` and `pool_label` only (Tech lead, Architect,
  Developer, Senior reviewer, Outside reviewer, QA, Tech writer, Product owner). Run kind
  names such as `dennis` never reach a page.
- Loopback default bind, `dash.host` override: unchanged.
- Light and dark themes, both meeting the AA test in `test_dash_render.py`.
- Works at 390 px wide with no horizontal page scroll.
- No em dashes in any copy, CSS comment or JS string.
- No code diff text and no avatars. Role icons are inline SVG from a fixed set.
- Redaction: from a transcript the page shows only a tool kind tag, a file basename or a
  command's first word, counts and times. Never arguments, prompts, outputs, model text,
  thinking, directories or full commands.
- Poll every 4 s; keep the open drawer, filters, open `details` and scroll position.

## Review Focus

1. A worker whose output file is huge (hundreds of MB) or has one 50 MB line: the poll must
   stay fast and memory flat. Pinned by `test_first_read_is_bounded_by_tail_bytes` and
   `test_huge_line_is_skipped_bounded` (Task 3).
2. A run that is executing but has not written a byte yet, or whose output directory is
   gone: the card shows "starting" and the page still renders. Pinned by
   `test_missing_transcript_card_says_starting` (Task 5).
3. A shell command whose first word is a secret or a path, or wrapped in `bash -lc`, `env`,
   `cd x &&`: the page shows a safe word or `?`, never the rest. Pinned by
   `test_shell_word_cases` (Task 1).
4. The drawer is open on a slice that moves to DONE between polls: the drawer says the slice
   is no longer active instead of going blank or throwing. Checked by
   `test_drawer_sources_only_for_active_slices` (Task 5) and the VERIFY browser script
   (Task 8).
5. A Codex file written by another Codex version (no `exec` blocks, different header): the
   card still shows the worker, with no doing-now line and no error. Pinned by
   `test_codex_unknown_format_yields_no_calls` (Task 2).

## Guarantee and non-goals

Guarantee: as in the base plan. The new public facts are: tool kind tags from a fixed
table, file basenames and command first words that pass `safe_target`, model names that
pass the `model` kind, integer counts (tools, errors, tokens), per-minute buckets,
timestamps, and the option words of owner questions when each option passes `scrub.plain`.

Non-goals: code diffs, `git` reads, line counts per file, cost in dollars, avatars or
animation, SSE or websockets, write actions (no dismiss or answer buttons), reading
`~/.codex/sessions` or any file outside the project's state dir, per-subagent trees, a JS
build step, a settings panel, multiple projects per page, a Codex `--json` parser.

## Design notes

- **Kind on a worker card means the harness**, shown as a badge `Claude`, `Codex` or
  `Script` (tests and duties). The run kind name is internal and can be a persona name, so
  it never shows; the role label carries the meaning.
- **Owner question options:** an option shows as its own text only when
  `scrub.plain(option) == option` and it is 40 characters or less (holds use the release
  option and `keep-held`, which pass); otherwise it shows as "Option 2". Option text is
  model-written free text up to 200 characters (`devloop/notes.py:389`), so this keeps the
  base plan's no-free-text rule. The recommended one is marked. "Where to answer" is fixed
  copy built from IDs, `devloop-state PROJECT answer KEY <option>` (the wording
  `notes._post_question` already uses, `devloop/notes.py:434`), plus "or the questions
  page". No file path is shown.
- **Files touched in the drawer** come from EDIT and WRITE calls in the selected run's
  transcript: basename and number of edit calls. No line counts, no git.
- **Drawer data travels in `/fragment`**, one hidden `<section class="dsrc">` per active
  slice (bounded below), so no route is added and `/api/state` carries the same facts.
- **One time source per harness:** Claude events carry `timestamp`; Codex text has none, so
  a Codex call is stamped with the time the server first read its bytes (on a first tail
  read: the file mtime). Codex sparklines are therefore coarse; that is accepted.
- **Doing now** is the newest tool call plus `running` (no matching `tool_result` seen yet
  for Claude; for Codex, no `succeeded`/`exited` line after the `exec` block yet).
- **Tokens:** Claude: `tokens_out` is the sum of `output_tokens` over distinct
  `message.id`s (stream-json repeats one message's usage on each content block);
  `context` is the newest message's `input_tokens + cache_read_input_tokens +
  cache_creation_input_tokens`. Codex: the number after a `tokens used` line, else null.
- **Needs attention** holds Product owner items only (questions, holds), then stuck slices.
  Known problems and unreadable files move to a collapsible "Known problems" panel owned by
  Tech lead. If a builder has already split these in the tree when Task 4 starts, Task 4
  adapts that code to the schema below rather than adding a second split.
- **Schema bump:** `/api/state` goes from `schema: 1` to `schema: 2`. The only consumers are
  the page and the tests (`grep -rn '"schema"' devloop` finds no other reader).
- **Choke point for transcript text:** `safe_target` is the only function that turns
  transcript bytes into a string the view keeps (`shell_word` ends in it), and `_seal`'s new
  `target` kind re-checks every such value, so a new extractor that skips `safe_target`
  fails `_seal` (`ViewError`) before any route renders. Guard: the class 1 sentry below.
- **Principles applied:** single responsibility (parsing and caching in `transcript.py`,
  view assembly in `live.py`, HTML in `render_live.py`; `render.py` is at 485 lines and
  `view.py` at 640, so new code goes in new files split by reason to change);
  information hiding (`Cache.read` hides offsets, rotation and format; callers get a
  `FileState`). House style: typed context objects, one place per fact, no flag arguments.

## Established solutions

Searched: the reference repo's protocol and mission view (`docs/PROTOCOL.md`,
`apps/web/src/viz/mission/mission.css`, read through `gh api`), and the codebase's own
readers. Findings used:
- Tool categories (`read`, `search`, `edit`, `write`, `shell`, `web`, `agent`, `plan`,
  `mcp`, `other`) and mapping a Codex `exec` of `rg` to search, from the reference's
  PROTOCOL "Tool categories". We adopt the category idea and our own fixed tag table.
- Tail reading follows `tail -F` semantics (follow by name, detect rotation by inode and
  truncation by size shrink) as in GNU coreutils `tail` and Python's
  `logging.handlers.WatchedFileHandler` (dev and inode comparison after `os.fstat`).
- Incremental line framing with a bounded pending buffer is the usual line-reader shape
  (`asyncio.StreamReader.readline` limit semantics: over-limit lines are discarded).
- Stream-json event shapes are taken from live run files under
  `~/.local/state/devloop/decoy/runs/*/out/log` (field names only), and Codex text shapes
  from `codex.out` written by `~/bin/codex-review` (`codex exec ... > "$out" 2>&1`).
Custom code is limited to the two small parsers and the safe extractors; no library parses
these formats, and the stdlib covers file following.

## Performance limits

All constants live at the top of `devloop/dash/transcript.py` and `live.py`.

| Name | Value | Meaning |
|---|---|---|
| `TAIL_BYTES` | 524288 (512 KiB) | first read of a file starts at `max(0, size - TAIL_BYTES)`, after the next newline |
| `STEP_BYTES` | 1048576 (1 MiB) | most bytes read from one file per build; a bigger gap jumps to the tail and sets `partial` |
| `MAX_LINE` | 2097152 (2 MiB) | a pending partial line longer than this is dropped; reading skips to the next newline |
| `KEEP_CALLS` | 50 | tool calls kept per file (a `deque`) |
| `SPARK_MINUTES` | 15 | per-minute buckets kept per file |
| `MAX_LIVE` | 16 | executing runs parsed per build, newest first; the rest show without a doing-now line |
| `MAX_DRAWER_FILES` | 8 | finished-run transcripts parsed per build for drawers (newest run per active slice) |
| `CACHE_ENTRIES` | 64 | files kept in the cache; least recently used evicted |
| `FEED_LIMIT` | 40 | feed items (tool calls plus loop events), newest first |
| `DRAWER_RUNS` | 8 | runs listed per slice drawer, newest first |
| `DRAWER_SLICES` | 30 | active slices given a drawer source; the rest open a drawer with checklist only |

Cache identity: `(st_dev, st_ino)`. Fast path: `os.stat(path)`; if inode, size and
`st_mtime_ns` match the entry, no open and no read. Otherwise open `"rb"`, `os.fstat` the
open file, and use the fstat values for every decision. Steady-state cost per poll is the
new bytes only. Budget, asserted by byte counts rather than wall time: a steady poll over
16 growing files reads only their growth; a cold build reads at most
`(MAX_LIVE + MAX_DRAWER_FILES) * TAIL_BYTES` (12 MiB). Live files today run to about
400 KB per run, so most cold reads are whole files.

Missing or rotated transcripts:
- File absent (run just started, or Codex writes `out/log` only): `state = "starting"` when
  the run is executing, else `"none"`; no error, no `problems` item.
- `PermissionError` or other `OSError` on open: `state = "unreadable"` on that card only.
- Inode changed or size smaller than the stored offset: entry reset, tail read again,
  `partial = True`, counts restart.
- Run finished and not in any drawer: entry evicted on the next build.

## Claude and Codex parsing

Where the files are: the wrapper sends the payload's stdout and stderr to `out/log`
(`devloop/wrap.py:281`). Claude workers run with `--output-format stream-json --verbose`
(`devloop/dispatch.py:122`), so `out/log` is one JSON event per line. Codex kinds
(`wrap.CODEX_KINDS`, `devloop/wrap.py:23`) get `out` = `out/codex.out` and
`last_msg` = `out/final.md` (`devloop/dispatch.py:443`); `codex-review` and `codex-builder`
write plain `codex exec` text to `codex.out`, `codex-fix` writes to `out/log`. The parser
picks `codex.out` when it exists, else `out/log`. `final.md` is never read. Kinds `test` and
`duty` have no transcript: the card shows elapsed time and the `Script` badge only.

Claude (`parse_claude_line(raw: bytes) -> ClaudeFacts | None`):
- Pre-filter on bytes before `json.loads`: only lines containing `b'"tool_use"'`,
  `b'"tool_result"'`, `b'"subtype":"init"'` or `b'"type":"result"'` go further. Other lines
  (thinking, progress, hooks) are never decoded.
- `assistant` events: for each `content` block of type `tool_use`, one `Call(tag, target,
  at, call_id)`; `at` from the event's `timestamp`; `usage` once per `message.id`.
- `user` events with `tool_result` blocks: only `tool_use_id` and `is_error`, found with
  bytes regexes (`rb'"tool_use_id":"(toolu_[A-Za-z0-9]{1,64})"'`, `rb'"is_error":true'`)
  so a 256 KB result line is never decoded.
- `system`/`init`: `model` only.
- Tool name to tag (`TOOL_KINDS`): Read, NotebookRead to READ; Edit, MultiEdit,
  NotebookEdit to EDIT; Write to WRITE; Bash, bash, BashOutput, Monitor to SHELL; Grep,
  Glob, ToolSearch to SEARCH; Task, Agent to AGENT; WebFetch, WebSearch to WEB; TodoWrite,
  Skill, ExitPlanMode to PLAN; names starting `mcp__` to MCP; anything else OTHER.
- Target: READ, EDIT, WRITE take `safe_target(input.file_path or input.notebook_path)`.
  SHELL takes `shell_word(input.command)`. SEARCH, WEB and PLAN take the tool's own name
  from the fixed table (for example `Grep`). AGENT, MCP and OTHER have no target (an
  agent's `subagent_type` or an MCP server name can be a persona or config name). Every
  other input field is ignored.

Codex text (`CodexParser.feed`), a small state machine:
- `model: NAME` in the first 40 lines sets the model.
- A line exactly `exec` starts a call; the next non-empty line is the command, cut at a
  trailing ` in /...`; the tag is SHELL unless `shell_word` is in `CODEX_SEARCH`
  (`rg`, `grep`, `find`, `fd`, `ls`) for SEARCH or `CODEX_READ` (`cat`, `sed`, `head`,
  `tail`, `nl`, `wc`) for READ.
- A line matching `^\s*(succeeded|exited \d+) in \d+ms:?` ends the running call; `exited`
  with a non-zero code adds one error.
- A line exactly `apply_patch`, `file update` or `file update:` (an optional trailing
  ` auto_approved=...:` allowed) starts an edit block; each following line matching
  `^([AMD]) (\S+)$` is one EDIT (`M`, `D`) or WRITE (`A`) call with `safe_target`; a blank
  line ends the block.
- `tokens used` followed by a number line (commas allowed) sets `tokens_out`.
- `codex`, `thinking`, `user` blocks: skipped. Any other line: ignored. Unknown format:
  no calls, no error.

`safe_target(name: object) -> str`: not a str gives `"?"`; else cut to the part after the
last `/`; must fullmatch `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}` and equal `scrub.plain(part)`
(`devloop/scrub.py:51`); else `"?"`. Leading-dot files (`.env`) read `?`.

`shell_word(cmd: object) -> str`, an algorithm the tests pin:
1. Not a str gives `"?"`. Take the first 400 characters. Up to twice, if the text matches
   `^(?:\S*/)?(?:ba|z)?sh\s+-l?c\s+(['"])(.*)` take group 2 without a trailing matching
   quote.
2. Split on `&&`, `||`, `;`, `|` and newlines into segments, in order.
3. In a segment, split on whitespace; drop leading `NAME=value` tokens; drop wrapper words
   `env`, `sudo`, `nice`, `nohup`, `time`, `command`, `exec`, and `timeout` with its next
   token when that token matches `\d+[smhd]?`; if the first token left is `cd`, go to the
   next segment.
4. Return `safe_target(first token)`; with no segment left, `"?"`.

## Reuse inventory

- `view._seal`, `VIEW_SCHEMA`, `WORDS`, `version_of`, `_try`, `_Ctx`, `_iso`
  (`devloop/dash/view.py:201`, `:91`, `:31`, `:221`, `:269`, `:281`, `:240`): extended, not
  copied. New scalar kinds `tag`, `target`, `model`, `icon`, `harness`, `option`.
- `_Ctx._runs` (`devloop/dash/view.py:296`): keeps `rd` and `req` so `live.py` does not walk
  `runs/` again.
- `runs.iter_runs`, `run_state`, `KINDS` (`devloop/runs.py:113`, `:233`, `:26`);
  `wrap.CODEX_KINDS`; `store.out_dir`, `store.read_header` (status and verdict of
  `result.md` for the drawer run list).
- `notes.load_questions` (options and recommended, `devloop/notes.py:251`),
  `notes.HOLD_PREFIX`.
- `scrub.plain`; `memory.read_memory` (`devloop/dash/memory.py`).
- `status.events`, `status.event_words`, `status.EVENT_KINDS` for loop events in the feed.
- `project.role_label`, `pool_label`.
- `render._chip`, `_time`, `_bar`, `_phase_strip`, `_checklist`, `_section`, `_details`,
  `PHASE_TEXT`, `CODE_TEXT`, `REASON_TEXT` (`devloop/dash/render.py:93`, `:117`, `:127`,
  `:234`, `:249`).
- `server.ViewCache` (`devloop/dash/server.py:41`): owns the one `transcript.Cache` and
  passes it to `build_view`, so the cache lives as long as the server and is never a
  module global.
- `dash.js` `swap`, `poll`, filters, theme (`devloop/dash/web/dash.js:97`, `:121`).
- Test helpers `dash_fixture.put_run` (`tests/devloop/dash_fixture.py:47`), `build`, the
  `srv` fixture, `call` and `_plant` in `test_dash_server.py`.
- Layout ideas only from the reference repo (KPI strip, attention bar, card grid with
  sparkline, activity table, side drawer, tag colours). No code copied.

## Frozen contracts

- Unchanged: the five routes and their headers; CLI `devloop-dash`; `dash.json`,
  `dash.lock`, `dash-port.json`, `node-status.json`; `KINDS` names; `exit_checklist` labels;
  STATUS.md and the Slack tick line; the wrapper's output file names (`out/log`,
  `out/codex.out`); stream-json is read, never changed.
- Changed on purpose, frozen here: `/api/state` `schema: 2` with the Fields below.
  `needs_you` holds owner items only; stuck items move to `stuck`; problem and unreadable
  items move to `problems`; `workers` is replaced by `live` and `waiting_runs`.
- New and frozen: `TOOL_TAGS = ("READ", "EDIT", "WRITE", "SHELL", "SEARCH", "AGENT", "WEB",
  "PLAN", "MCP", "OTHER")`; feed event tags `DISPATCHED`, `APPLIED`, `MOVED`, `MERGED`,
  `CLOSED`, `QUESTION`, `DROPPED` mapped from `status.EVENT_KINDS`; icon ids `plan`,
  `build`, `review`, `test`, `docs`, `duty`; harness words `claude`, `codex`, `script`;
  transcript states `live`, `starting`, `none`, `unreadable`.

## Enumerations

### Exit paths

- Transcript file absent: `Cache.read` returns the entry with state `starting` (executing)
  or `none`. No exception leaves `transcript.py` for `OSError`, `ValueError`,
  `UnicodeError`, `RecursionError` or `TypeError` from one line or one file.
- Open fails (`PermissionError`, `IsADirectoryError`): state `unreadable` on that card;
  the view adds no `problems` item (the run dir belongs to the loop, not the owner).
- Rotation or truncation (`(st_dev, st_ino)` differs, or `st_size < offset`): reset and tail.
- A pending partial line over `MAX_LINE`: dropped, `skip_to_newline = True`.
- Run leaves `executing` (exit file written, `devloop/runs.py:233`): its card leaves the
  grid on the next build; its last calls stay in the feed until older than the newest
  `FEED_LIMIT`; its cache entry is evicted unless a drawer uses it.
- View build raises anyway: the server's existing fixed 500 (`devloop/dash/server.py:111`).
- Client: a drawer open on a key with no source in the new fragment shows the fixed text
  "This slice is no longer active." Escape, the close button and browser Back close it.
  Two failed polls show the existing stale banner; the drawer stays open on old data.

### Routes

- No route is added. Drawer sources ride inside `/fragment` and `/` (hidden sections) and
  inside `/api/state` (`drawers`). `server.ROUTES` keeps its five keys
  (`devloop/dash/server.py:144`).
- `/fragment` keeps `If-None-Match` and 304. The ETag changes when any fact changes, so
  with active workers most polls return 200; the fragment budget is 200 KB with 16 workers,
  40 feed items and 30 drawer sources (asserted in Task 6).

### FK shapes

- Run to slice: `req["key"]` to an active slice key. A worker whose slice is gone or not
  active gets a card with no drawer link (`slice_active: false`).
- Drawer run list to slice: only runs whose `req["key"]` equals the drawer's key.
- Worker card to its transcript: the path is built from `store.out_dir(rd)` and fixed names
  only. `req["out"]` is honoured only when it equals `str(store.out_dir(rd) / "codex.out")`
  (`devloop/dispatch.py:443`), so a damaged request cannot point the reader at another file.
- Run ref `KEY.N` (N is the 1-based position of the run among that key's runs by
  `started_at`): resolves only within its slice's drawer; never a run id.
- Feed item to worker: by role and key; a call from a run that left the grid still names
  its role and slice.

### Fields

`VIEW_SCHEMA` additions and changes (kind in brackets, `?` nullable). Everything else as in
the base plan.
- top: `schema`[int] = 2; adds `kpi`, `stuck`, `problems`, `live`, `waiting_runs`, `feed`,
  `drawers`; removes `workers`.
- `kpi`: `running`[int], `slots`[int] (sum of caps), `needs_you`[int], `merged_today`[int?],
  `tools_per_min`[int] (calls with `at` in the last 60 s across live files),
  `free_mb`[int?], `memory_state`[word?], `last_pass_at`[at?].
- `needs_you[]`: `kind`[word: question, hold], `key`[id], `owner`[label], `since`[at?],
  `waiting`[bool], `options`[{`text`[option?], `n`[int], `recommended`[bool]}].
- `stuck[]`: `key`[id], `owner`[label], `since`[at?].
- `problems[]`: `kind`[word: problem, unreadable], `key`[id?], `owner`[label],
  `count`[int?], `since`[at?].
- `live[]` (one per executing run, newest first; transcripts parsed for the first
  `MAX_LIVE`): `ref`[id], `role`[label], `icon`[icon], `key`[id], `slice_active`[bool],
  `phase`[word?], `harness`[harness], `model`[model?], `started_at`[at?], `state`[word:
  live, starting, none, unreadable], `doing`(null or {`tag`[tag], `target`[target?],
  `at`[at?], `running`[bool]}), `tools`[int], `partial`[bool], `errors`[int],
  `tokens_out`[int?], `context`[int?], `spark`[int list, length `SPARK_MINUTES`].
- `waiting_runs[]`: `role`[label], `key`[id], `state`[word: awaiting-apply,
  awaiting-release], `started_at`[at?].
- `feed[]`: `at`[at], `tag`[tag], `target`[target?], `role`[label?], `key`[id?],
  `words`[line?] (loop events only, from `status.event_words`).
- `drawers[]`: `key`[id], `runs`[{`ref`[id], `role`[label], `harness`[harness],
  `started_at`[at?], `status`[word?: DONE, BLOCKED, FAILED, running], `verdict`[word?: GO,
  GO-WITH-FIXES, NO-GO]}], `calls`[{`at`[at?], `tag`[tag], `target`[target?]}] (newest run
  with a transcript, up to `KEEP_CALLS`), `files`[{`name`[target], `edits`[int]}] (sorted
  by edits, then name; at most 20). The checklist and PR link come from the card of the
  same key in `rows`.

New scalar kinds in `_scalar`: `tag` (in `TOOL_TAGS` or the feed event tags), `target`
(`safe_target(v) == v`), `model` (fullmatch `[a-z][a-z0-9.-]{1,48}` and no `scrub._TOKEN`
match; dates in model names are allowed, which `plain`'s hex rule would refuse), `icon`,
`harness` (fixed tuples), `option` (`plain(v) == v` and `len(v) <= 40`). `spark` is
declared `["int"]`; `live.py` asserts its length.

Not in the view: run ids, run kind names, reviewer names, tool input fields other than the
basename or first word, commands, prompts, tool results, text and thinking blocks, session
ids, cwd, directories, `final.md`, costs.

### Output surfaces

- `/` and `/fragment` HTML: KPI strip, attention bar, worker grid, feed, pipeline, known
  problems, capacity, memory, shipped, parked, hidden drawer sources.
- `/api/state` JSON: the sealed schema 2 view.
- Server log (`dash.log`): unchanged (method, path, status). The transcript reader logs
  nothing; a parse failure is a counter in memory, never a line with file content.
- CLI stdout and `devloop-state check`: unchanged.
- Browser: theme in `localStorage` as now; drawer key in `location.hash` (`#slice=KEY`);
  filters in the query string.

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | parser emits only `Call(tag, target, at, call_id)` from fixed tables plus `safe_target`; `_seal` kinds `tag`, `target`, `model`, `option` re-check | `test_transcript_secrets_reach_no_route` (Claude and Codex transcripts with secrets in every argument, prompt, output and text block; all three routes) | make `shell_word` return the whole segment instead of `safe_target(first token)` |
| 2 | cleanup on every exit path | every read is `with open(path, "rb")`; `Cache.collect` prunes entries for runs not live and not in a drawer; LRU cap `CACHE_ENTRIES` | `test_cache_prunes_finished_runs_and_leaves_no_open_files` (counts `/proc/self/fd` before and after ten builds) | delete the prune step in `Cache.collect` |
| 3 | state consistency (read-only) | reader opens `"rb"` only; cache in server memory; offsets only move forward or reset on rotation | `test_transcript_reader_never_writes` (patched `open` refuses any mode but `rb`; run dir listing, sizes and mtimes unchanged) and `test_build_view_writes_nothing` with transcripts present | open the file with `"r+b"` in `Cache.read` |
| 4 | route completeness | no new route; drawers ride in `/fragment` and `/api/state` | `test_routes_unchanged_after_redesign` (`set(ROUTES)` equals the five paths) and `test_only_known_routes_and_methods` | add `"/drawer"` to `server.ROUTES` |
| 5 | referential integrity | drawer runs filtered by `req["key"]`; worker without an active slice gets `slice_active: false`; Codex `out` path checked against `out_dir(rd)` | `test_drawer_runs_belong_to_their_slice`, `test_orphan_worker_has_no_drawer_link`, `test_request_out_path_outside_run_dir_is_ignored` | drop the key filter in `live.drawers` |
| 6 | untrusted input | transcript bytes are untrusted: per-line `try`, byte pre-filter, `MAX_LINE`, type checks on every field (`input` must be a dict, `file_path` a str) | seeded fuzz `test_parser_never_raises_on_random_bytes` (500 random and mutated real-shape lines), `test_huge_line_is_skipped_bounded`, `test_wrong_types_in_tool_input_are_ignored` | remove the per-line `try` around `parse_claude_line` in `Cache.read` |
| 7 | TOCTOU (check then use) | open first, `os.fstat` the open file, read up to the fstat size from the offset; identity `(st_dev, st_ino)` from fstat; `os.stat` only as a skip-if-unchanged hint | `test_rotation_between_polls_resets_offset`, `test_truncate_resets`, `test_growth_after_fstat_is_read_next_poll` | take size and inode from `os.stat(path)` before open and skip the fstat |
| 8 | weak tests | every mutation in this table is a `mutate` entry in `tests/devloop/mutations.sh` with its named test | `mutations.sh` reports each new dash mutation killed | make the first read ignore `TAIL_BYTES` (read from 0), caught by `test_first_read_is_bounded_by_tail_bytes` |
| 9 | refactor drift | `needs_you` split reuses `_needs_you`; `workers` replaced by `live` plus `waiting_runs`; render helpers reused, not copied | existing dash tests updated in place; `test_needs_you_has_owner_items_only`, `test_problems_panel_owned_by_tech_lead` | leave `problem` items in `needs_you` |
| 10 | frozen contracts | `VIEW_SCHEMA` with `schema: 2`; `TOOL_TAGS` and feed tags as module tuples; routes unchanged | `test_view_keys_match_schema`, `test_schema_is_two`, `test_tool_tags_frozen` | rename `doing` to `now` in `live.py` only |

## Acceptance tests

All red first, in `tests/devloop/`. New fixture module `tests/devloop/transcript_fixture.py`
writes synthetic files with the real shapes seen in live runs (field names only, invented
values): `claude_events(calls) -> bytes` builds `system/init`, `assistant` lines with
`tool_use` blocks and `timestamp`, `user` lines with `tool_result`, thinking and text
lines, and `result`; `codex_text(blocks) -> bytes` builds the `codex exec` header, `exec`
blocks with `succeeded in` and `exited N in`, `apply_patch` and `file update:` blocks,
`codex` and `thinking` blocks, and `tokens used`. `dash_fixture.build` gains one executing
Claude builder with a transcript, one executing Codex reviewer with `codex.out`, and one
executing planner with no file yet.

- `test_transcript_secrets_reach_no_route`: plants `ghp_DEMOTOKEN1234567`, `AKIAABCDEFGH1234`,
  `password hunter2`, `https://user:pw@host/x`, `/home/someone/private-dir/` and a 40-hex
  string in Bash commands (as arguments, after `export`, inside `bash -lc`), Edit
  `old_string` and `new_string`, Write `content`, Grep `pattern`, WebFetch `url`, Agent
  `prompt` and `subagent_type`, text and thinking blocks, tool results, `result.result`, the
  init `cwd`; and in Codex `user`, `codex`, `exec` command and output blocks and
  `file update` directory parts. A command whose first word is `ghp_DEMOTOKEN1234567` and a
  file named `ghp_DEMOTOKEN1234567.txt` render `?`. None of the planted strings, no
  `str(tmp_path)` and no `/home/` appear on `/`, `/fragment` or `/api/state`.
- `test_tool_kinds_and_targets_from_claude`: a Read of `/x/y/view.py` gives `READ view.py`;
  Bash `cd /r && uv run pytest -q` gives `SHELL uv`; Grep gives `SEARCH Grep`; Agent gives
  `AGENT` with no target; `mcp__qmd__query` gives `MCP` with no target.
- `test_shell_word_cases` (parametrized): `"git status"` to `git`;
  `"/bin/bash -lc 'git diff'"` to `git`; `"FOO=1 BAR=2 make t"` to `make`;
  `"timeout 30 pytest"` to `pytest`; `"cd /a && cd b; ls"` to `ls`;
  `"sudo env X=1 ./scripts/run.sh"` to `run.sh`; `"ghp_DEMOTOKEN1234567 x"` to `?`;
  `"deadbeefcafe"` to `?`; `""` to `?`; `"cd /a"` to `?`; `None` to `?`; a 10 000-character
  command is cut at 400 characters before splitting.
- `test_safe_target_cases`: `view.py` passes; `.env`, `a b.py`, 65-character names and
  names with a 7+ hex run give `?`; `x/../../etc/passwd` gives `passwd` (no directory).
- `test_codex_exec_and_patch_blocks`: two `exec` blocks (`rg` to SEARCH, `pytest` to SHELL),
  one `exited 1` (errors 1), an `apply_patch` block with `M a/b.py` and `A c.md` (EDIT
  `b.py`, WRITE `c.md`), `tokens used` then `12,345` (tokens_out 12345), model from the
  header.
- `test_codex_unknown_format_yields_no_calls`: arbitrary prose gives zero calls, no error.
- `test_tokens_deduped_by_message_id`: three `assistant` lines sharing a `message.id` count
  its `output_tokens` once; `context` is the newest message's sum.
- `test_running_flag_clears_on_tool_result`: a call is `running` until its `tool_result`
  line arrives; `is_error: true` adds one to `errors`.
- `test_first_read_is_bounded_by_tail_bytes`: a 5 MB file; the entry's `bytes_read` is at
  most `TAIL_BYTES` and `partial` is true.
- `test_steady_poll_reads_only_new_bytes`: append 3 lines; the next read consumes exactly
  their bytes; an unchanged file causes no `open` (patched `open` counter).
- `test_partial_last_line_waits_for_newline`: a line split across two writes yields one call
  after the second write, none after the first.
- `test_huge_line_is_skipped_bounded`: one 3 MB line, then a normal call: the call is found
  and `len(entry.pending)` never exceeds `MAX_LINE`.
- `test_invalid_utf8_and_junk_lines_are_ignored`, `test_wrong_types_in_tool_input_are_ignored`,
  `test_parser_never_raises_on_random_bytes` (seed 1234; named as seeded fuzz, not
  shrinking property testing).
- `test_rotation_between_polls_resets_offset`, `test_truncate_resets`,
  `test_growth_after_fstat_is_read_next_poll`.
- `test_missing_transcript_card_says_starting`, `test_unreadable_transcript_marks_card_only`
  (chmod 000; skipped when running as root).
- `test_cache_prunes_finished_runs_and_leaves_no_open_files`, `test_cache_lru_cap`.
- `test_transcript_reader_never_writes`, `test_request_out_path_outside_run_dir_is_ignored`.
- `test_kpi_counts` (running, slots, owner-only needs you, merged today, tools per minute
  from fixture timestamps with a fixed `now`, free memory and state, last pass).
- `test_needs_you_has_owner_items_only`, `test_problems_panel_owned_by_tech_lead`,
  `test_question_options_plain_or_numbered` (an option `approve` shows as text; an option
  with spaces or a secret shows as `Option 2`; recommended marked),
  `test_stuck_listed_after_owner_items`.
- `test_live_cards_one_per_executing_run` (awaiting runs not in `live`, listed in
  `waiting_runs`), `test_live_card_fields` (role label, icon, harness, model from argv
  `--model`, phase, doing, tools, spark length 15), `test_max_live_cap`.
- `test_feed_merges_calls_and_events_newest_first`, `test_feed_limit`.
- `test_drawer_runs_belong_to_their_slice`, `test_orphan_worker_has_no_drawer_link`,
  `test_drawer_files_from_edit_and_write_calls`, `test_drawer_sources_only_for_active_slices`,
  `test_drawer_run_status_and_verdict_from_result_header`.
- `test_schema_is_two`, `test_tool_tags_frozen`, `test_routes_unchanged_after_redesign`,
  `test_view_keys_match_schema`.
- Render: `test_page_sections_in_order` (KPI strip, attention bar, workers, feed, pipeline,
  known problems, capacity and memory, shipped, parked), `test_sparkline_is_inline_svg`,
  `test_worker_card_has_role_icon_not_avatar` (an `<svg>` with `data-icon`, no `<img>`),
  `test_fragment_size_budget` (16 workers, 40 feed items, 30 drawers: under 200 KB),
  `test_no_em_dash_in_assets_and_copy` (render output, `dash.css`, `dash.js`),
  `test_css_tokens_meet_aa` and `test_css_light_and_dark_define_the_same_tokens` extended to
  the tag tokens, `test_phone_rules_present` (a `@media (max-width: 640px)` block that sets
  the grids to one column and the drawer to `inset: 0`).
- JS behaviour (4 s poll, drawer open by click and by `#slice=` on load, drawer kept across
  swaps, "no longer active" text, Escape and Back close, filters and scroll kept, no
  horizontal scroll at 390 px in both themes) has no pytest runner. Task 8 VERIFY runs it
  in a browser against the fixture server and records the steps and screenshots.

## Placement

| New file | Directory | Why there | Sits beside |
|---|---|---|---|
| `transcript.py` | `devloop/dash/` | dashboard-only reader; `devloop/` is near the fan-out cap, `dash/` has 6 modules | `memory.py` (the other read-only OS reader) |
| `live.py` | `devloop/dash/` | view sections from runs; `view.py` is already 640 lines | `view.py` |
| `render_live.py` | `devloop/dash/` | live HTML sections; `render.py` is 485 lines | `render.py` |
| `transcript_fixture.py`, `test_dash_transcript.py`, `test_dash_live.py` | `tests/devloop/` | existing dash test home | `dash_fixture.py`, `test_dash_view.py` |

Present consumers: `transcript.py` is used by `live.py` (S2), `live.py` by `view.py` (S2),
`render_live.py` by `render.py` (S3). S1 merges `transcript.py` before its consumer; that is
the approved pair under proportionality's plan-slice exception, and S2 lands before the
row closes.

Expected size: 3 new source modules (about 330, 260 and 280 lines), 3 new test files
(about 700 lines), edits to `view.py`, `server.py`, `render.py`, `dash.css` (rewrite, about
260 lines), `dash.js` (about +90), `dash_fixture.py`, `test_dash_view.py`,
`test_dash_render.py`, `test_dash_server.py`, `mutations.sh`. New public names:
`TOOL_TAGS`, `TOOL_KINDS`, `CODEX_SEARCH`, `CODEX_READ`, `Call`, `ClaudeFacts`,
`safe_target`, `shell_word`, `parse_claude_line`, `CodexParser`, `FileState`, `Cache`,
`transcript_path`, `ICONS`, `HARNESS`, `live_cards`, `waiting_runs`, `feed`, `kpi`,
`drawers`, `kpi_strip`, `attention`, `worker_grid`, `sparkline`, `role_icon`,
`drawer_sources`.

## Slices

| Slice | Tasks | Ships | Gate |
|---|---|---|---|
| S1 transcript reader | 1, 2, 3 | `transcript.py` with tests; nothing on the page yet | Outside reviewer on the S1 head; T1 = `pytest tests/devloop/test_dash_transcript.py tests/devloop/test_dash_view.py` plus ruff |
| S2 read model schema 2 | 4, 5 | attention split, `live.py`, schema 2, server cache wiring | Outside reviewer on the S2 head; T1 = `pytest tests/devloop -k dash` plus ruff |
| S3 page | 6, 7, 8 | new render, CSS, JS drawer, leak sentry, mutations, browser VERIFY | Outside reviewer on the S3 head; T1 = `pytest tests/devloop -k dash`; `tests/devloop/mutations.sh` dash entries; VERIFY record |

T2 at each gate runs T1 on the exact head plus that slice's mutations. No escalation
trigger applies (no state-file schema, lock, auth or dependency change; `/api/state` has no
consumer outside the dashboard), so no full suite at the slice; the merge follows the
local operations profile as usual. S2 and S3 may ship as one PR if review is short.

## Pre-mortem

- **A secret reaches the LAN through a command or a path.** Only `safe_target` produces
  kept text (`shell_word` ends in it), `_seal` re-checks, and the sentry plants in every
  field of both formats on all three routes.
- **A big transcript makes every poll slow.** Tail start, `STEP_BYTES`, the byte pre-filter,
  bytes regexes on tool results and a no-open fast path; tests assert bytes read.
- **Stream-json changes shape in a Claude release.** Unknown events and fields are ignored;
  the card falls back to elapsed time; fixture shapes are copied from current live runs and
  documented in `transcript_fixture.py`.
- **Codex text changes between versions.** Same fallback: no calls, no error. A `--json`
  branch is a later slice if Codex output moves to JSONL.
- **The ETag stops helping.** With active workers the fragment changes every few seconds;
  the 200 KB budget and the 2 s server cache keep that cheap on a LAN.
- **The drawer flickers or closes on each poll.** The drawer shell sits outside
  `#app-body`; only its body is refilled from the new source, and its scroll top restored.
- **Two writers in this repo.** S1 touches only new files. S2 and S3 edit `view.py`,
  `render.py`, `dash.js` and `dash.css`, which another builder may be changing for the
  attention split. Task 4 starts by reading the tree and building on it; one writer at a
  time in this worktree.
- **Persona names leak through `subagent_type` or MCP server names.** AGENT and MCP calls
  carry no target by rule, asserted in `test_tool_kinds_and_targets_from_claude` and the
  sentry.

## Task 1: Safe extractors and the Claude line parser

**Files:**
- Create: `devloop/dash/transcript.py`, `tests/devloop/transcript_fixture.py`,
  `tests/devloop/test_dash_transcript.py`

**Interfaces:**
- Produces: `TOOL_TAGS: tuple[str, ...]`; `TOOL_KINDS: dict[str, str]`;
  `safe_target(name: object) -> str`; `shell_word(cmd: object) -> str`;
  `Call = namedtuple("Call", "tag target at call_id")` (`target` str or None, `at` ISO str
  or None, `call_id` str or None);
  `ClaudeFacts` dataclass: `calls: list[Call]`, `results: list[tuple[str, bool]]` (call id,
  is error), `model: str | None`, `usage: tuple[str, int, int] | None` (message id, output
  tokens, context tokens);
  `parse_claude_line(raw: bytes) -> ClaudeFacts | None` (None for a skipped line).

- [ ] **Step 1: Write the failing tests** `test_shell_word_cases`, `test_safe_target_cases`,
  `test_tool_kinds_and_targets_from_claude`, `test_tokens_deduped_by_message_id` (parser
  part: the `usage` tuple), `test_invalid_utf8_and_junk_lines_are_ignored`,
  `test_wrong_types_in_tool_input_are_ignored`, `test_parser_never_raises_on_random_bytes`
  (seed 1234, 500 cases: random bytes, and real-shape lines with fields deleted, retyped or
  cut), with the values in Acceptance tests.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_transcript.py -q`. Expected: FAIL,
  `ModuleNotFoundError: devloop.dash.transcript`.
- [ ] **Step 3: Implement** the names above as in "Claude and Codex parsing". No file access
  in this task.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then `ruff check` and
  `ruff format --check` on the new files.
- [ ] **Step 5: Commit** `feat(devloop): parse worker tool calls into safe facts`, last line
  `cam`.

## Task 2: Codex text parser

**Files:**
- Modify: `devloop/dash/transcript.py`, `tests/devloop/transcript_fixture.py`,
  `tests/devloop/test_dash_transcript.py`

**Interfaces:**
- Consumes: `Call`, `safe_target`, `shell_word` from Task 1.
- Produces: `CODEX_SEARCH`, `CODEX_READ` (frozensets); `class CodexParser` with
  `feed(lines: list[bytes], seen_at: str) -> tuple[list[Call], list[tuple[str, bool]]]`
  (new calls, finished call ids with error flag), attributes `model: str | None`,
  `tokens_out: int | None`, `lines_seen: int`. Call ids are `"c" + str(n)` per parser.

- [ ] **Step 1: Write the failing tests** `test_codex_exec_and_patch_blocks`,
  `test_codex_unknown_format_yields_no_calls`, and a Codex half of
  `test_parser_never_raises_on_random_bytes`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_transcript.py -q -k codex`. Expected:
  FAIL, `AttributeError` on `CodexParser`.
- [ ] **Step 3: Implement** the state machine in "Claude and Codex parsing". `model` is read
  only while `lines_seen < 40`. Every call gets `at = seen_at`.
- [ ] **Step 4: Run** the same command, then the whole file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): read Codex exec output as tool calls`, last line
  `cam`.

## Task 3: Bounded tail reader and the file cache

**Files:**
- Modify: `devloop/dash/transcript.py`, `tests/devloop/test_dash_transcript.py`,
  `tests/devloop/mutations.sh`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the constants in "Performance limits";
  `class FileState` with fields `ident: tuple[int, int] | None`, `offset: int`,
  `mtime_ns: int`, `pending: bytearray`, `skip_to_newline: bool`, `partial: bool`,
  `calls: deque[Call]` (maxlen `KEEP_CALLS`), `tools: int`, `errors: int`,
  `open_calls: dict[str, Call]`, `tokens: dict[str, int]` (message id to output tokens,
  oldest dropped past 2000 keys), `context: int | None`, `model: str | None`,
  `minutes: dict[str, int]` (`YYYY-MM-DDTHH:MM` to count, last `SPARK_MINUTES` kept),
  `state: str`, `bytes_read: int`, `codex: CodexParser | None`, and properties
  `tokens_out -> int | None`, `doing -> tuple[Call, bool] | None`;
  `class Cache` with `read(path: Path | None, harness: str, executing: bool) -> FileState`
  and `collect(keep: set[Path]) -> None`, both under one `threading.Lock`;
  `transcript_path(rd: Path, req: dict) -> Path | None` (Claude kinds:
  `out_dir(rd) / "log"`; Codex kinds: `out_dir(rd) / "codex.out"` when it exists and
  `req.get("out")` is absent or equals that path, else `out_dir(rd) / "log"`; `test` and
  `duty`: None).

- [ ] **Step 1: Write the failing tests** `test_first_read_is_bounded_by_tail_bytes`,
  `test_steady_poll_reads_only_new_bytes`, `test_partial_last_line_waits_for_newline`,
  `test_huge_line_is_skipped_bounded`, `test_rotation_between_polls_resets_offset`,
  `test_truncate_resets`, `test_growth_after_fstat_is_read_next_poll` (append between the
  patched `os.fstat` and the read; the extra bytes arrive on the next read, none lost or
  doubled), `test_cache_prunes_finished_runs_and_leaves_no_open_files`, `test_cache_lru_cap`,
  `test_transcript_reader_never_writes`, `test_request_out_path_outside_run_dir_is_ignored`,
  `test_running_flag_clears_on_tool_result`, `test_tokens_deduped_by_message_id` (file part).
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_transcript.py -q`. Expected: the new
  tests FAIL with `AttributeError` on `Cache`.
- [ ] **Step 3: Implement** `Cache.read` in this order:

```text
st = os.stat(path)                    # OSError: absent -> state starting or none; return entry
if (st_dev, st_ino) == entry.ident and st_size == entry.offset and st_mtime_ns == entry.mtime_ns:
    return entry                      # no open, no read
with open(path, "rb") as fh:          # OSError -> state unreadable; return entry
    fs = os.fstat(fh.fileno()); ident = (fs.st_dev, fs.st_ino); end = fs.st_size
    if entry.ident is not None and (ident != entry.ident or end < entry.offset):
        reset entry counters and pending; entry.partial = True
    if entry.offset == 0 and end > TAIL_BYTES:      start = end - TAIL_BYTES; skip to newline; partial
    elif end - entry.offset > STEP_BYTES:           start = end - TAIL_BYTES; clear pending; skip to newline; partial
    else:                                           start = entry.offset
    fh.seek(start); data = fh.read(end - start)
entry.ident, entry.offset, entry.mtime_ns = ident, end, fs.st_mtime_ns
split pending + data on b"\n"; the last piece becomes pending (dropped, with skip_to_newline, past MAX_LINE)
each complete line: try parse_claude_line or codex.feed; except the listed errors: count and continue
```

  `minutes` uses each call's `at` (Claude) or `seen_at` (Codex). Add the class 2, 3, 6, 7
  and 8 `mutate` entries to `tests/devloop/mutations.sh` with their tests.
- [ ] **Step 4: Run** the file, then `pytest tests/devloop -k dash -q`, then the five new
  mutations. Expected: PASS, and each mutation killed.
- [ ] **Step 5: Commit** `feat(devloop): tail worker output files with a bounded cache`, last
  line `cam`.

## Task 4: Attention split and question options (schema 2, part 1)

**Files:**
- Modify: `devloop/dash/view.py`, `tests/devloop/test_dash_view.py`,
  `tests/devloop/test_dash_render.py`, `tests/devloop/dash_fixture.py`

**Interfaces:**
- Consumes: the tree as it is when the task starts. First run `git log -3 --oneline --
  devloop/dash/view.py` and read `_needs_you` (`devloop/dash/view.py:436`); if a split is
  already there, rename its keys to match this plan and keep its tests.
- Produces: `SCHEMA = 2`; `VIEW_SCHEMA` entries `needs_you`, `stuck`, `problems` as in
  Fields; scalar kind `option`; `_options(q: dict) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** `test_needs_you_has_owner_items_only`,
  `test_problems_panel_owned_by_tech_lead`, `test_question_options_plain_or_numbered`,
  `test_stuck_listed_after_owner_items`, `test_schema_is_two`; update
  `test_needs_you_lists_question_hold_stuck_problem` to the three lists.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_view.py -q`. Expected: FAIL on the new
  keys.
- [ ] **Step 3: Implement** the split in `_needs_you` and `_unreadable`. An option passes the
  `option` kind or gets `text: None` with its 1-based `n`. Render keeps working by reading
  `problems` for the old problem rows (full layout comes in Task 6).
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): split owner items from known problems`, last line
  `cam`.

## Task 5: Live read model `devloop/dash/live.py` (schema 2, part 2)

**Files:**
- Create: `devloop/dash/live.py`, `tests/devloop/test_dash_live.py`
- Modify: `devloop/dash/view.py` (`_Ctx._runs` keeps `rd` and `req`; new keys; scalar kinds
  `tag`, `target`, `model`, `icon`, `harness`), `devloop/dash/server.py` (`ViewCache` holds
  `transcript.Cache()` and calls `build_view(project, proc=self.proc,
  cache=self.transcripts)`), `tests/devloop/dash_fixture.py` (three live runs built with
  `transcript_fixture`), `tests/devloop/test_dash_render.py` (the old workers table test
  reads `live`)

**Interfaces:**
- Consumes: `transcript.Cache`, `transcript_path`, `FileState`; `_Ctx`.
- Produces: `ICONS: dict[str, str]` (run kind to icon id: planner and codex-fix `plan`,
  builder and codex-builder `build`, dennis and codex-review `review`, test `test`, docs
  `docs`, duty `duty`); `HARNESS: dict[str, str]` (Claude kinds `claude`, Codex kinds
  `codex`, test and duty `script`); `live_cards(ctx, cache, now) -> list[dict]`;
  `waiting_runs(ctx) -> list[dict]`; `feed(ctx, states, now) -> list[dict]`;
  `kpi(ctx, view, states, now) -> dict`; `drawers(ctx, cache, cards) -> list[dict]`;
  `build_view(project, proc="/proc", now=None, cache=None) -> dict` (a fresh `Cache` when
  None, so tests and one-shot callers work). `states` is `dict[str, FileState]` keyed by run
  ref. Model: for Claude kinds, the value after `--model` in `req["argv"]`, else
  `FileState.model`; it must pass the `model` kind or become None.

- [ ] **Step 1: Write the failing tests** `test_live_cards_one_per_executing_run`,
  `test_live_card_fields`, `test_max_live_cap`, `test_missing_transcript_card_says_starting`,
  `test_unreadable_transcript_marks_card_only`,
  `test_feed_merges_calls_and_events_newest_first`, `test_feed_limit`, `test_kpi_counts`,
  `test_drawer_runs_belong_to_their_slice`, `test_orphan_worker_has_no_drawer_link`,
  `test_drawer_files_from_edit_and_write_calls`, `test_drawer_sources_only_for_active_slices`,
  `test_drawer_run_status_and_verdict_from_result_header`, `test_tool_tags_frozen`; update
  `test_view_keys_match_schema`, `test_no_agent_names_or_run_ids_in_the_view` (also asserts
  no run kind name in `live`, `feed` and `drawers`), `test_build_view_writes_nothing`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_live.py tests/devloop/test_dash_view.py
  -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Spark: for the minutes `now - 14` to `now`, the count from
  `FileState.minutes` or 0. Feed events: `status.events(project)[-200:]` mapped to tags
  (`run-dispatched` DISPATCHED, `run-applied` APPLIED, `slice-moved` MOVED, `merged` MERGED,
  `row-closed` CLOSED, `question-waiting` and `question-defaulted` QUESTION,
  `apply-mismatch` DROPPED), `words` from `status.event_words`. `kpi.free_mb` and
  `memory_state` come from the `memory` section already built. Call `cache.collect(keep)`
  once at the end with the paths used in this build.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): add live worker activity to the dashboard model`,
  last line `cam`.

## Task 6: Render the mission-control page

**Files:**
- Create: `devloop/dash/render_live.py`
- Modify: `devloop/dash/render.py`, `tests/devloop/test_dash_render.py`

**Interfaces:**
- Consumes: the schema 2 view.
- Produces, in `render_live.py`: `kpi_strip(v, now) -> str`, `attention(v, now) -> str`,
  `worker_grid(v, now) -> str`, `sparkline(counts: list[int]) -> str` (an `<svg
  class="spark" viewBox="0 0 60 16" role="img" aria-label="Tool calls per minute, last 15
  minutes">` with one `<polyline>`; all zero draws a flat line), `role_icon(icon: str) ->
  str` (one inline `<svg data-icon=...>` per id, `aria-hidden="true"`), `feed(v, now) ->
  str`, `drawer_sources(v, now) -> str`. In `render.py`: `render_fragment` in the order of
  `test_page_sections_in_order`; `render_page` adds `<aside id="drawer" hidden
  aria-label="Slice details">` with a close button, outside `#app-body`.
- Copy (exact): KPI labels "Workers", "Needs you", "Merged today", "Tools / min",
  "Free memory", "Last tick". Attention empty state "Nothing needs you right now." Worker
  empty state "No workers running." Feed empty state "No activity yet." Drawer gone
  "This slice is no longer active." Card without output yet "starting". Answer hint
  "Answer: devloop-state PROJECT answer KEY <option>, or on the questions page." Known
  problems summary "Known problems" with the count.
- Card layout: line 1 role icon, role label, slice key, harness badge, elapsed `<time>`;
  line 2 phase chip and model; line 3 doing now (tag chip with `data-tag`, target in
  `<code>` with CSS ellipsis, a `running` dot); line 4 tools (`N`, or `N+` when partial),
  errors when non-zero, tokens out, sparkline. The card is `<article tabindex="0"
  data-open="KEY">` only when `slice_active`.
- Pipeline: one line per slice (key, phase strip, risk, round, PR link, owner, since,
  "n/m met"), checklist in `<details>` as now; rows keep their header.

- [ ] **Step 1: Write the failing tests** `test_page_sections_in_order`,
  `test_sparkline_is_inline_svg`, `test_worker_card_has_role_icon_not_avatar`,
  `test_fragment_size_budget`, `test_no_em_dash_in_assets_and_copy`; update
  `test_page_renders_from_fixture_offline`, `test_empty_project_shows_empty_states`,
  `test_html_escapes_planted_markup` (markup cannot pass `safe_target`, so plant it in a
  configured role label), `test_counts_read_naturally`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with the reused helpers; every value through `html.escape`.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): render the dashboard as mission control`, last line
  `cam`.

## Task 7: Dense theme, phone layout and the drawer

**Files:**
- Modify: `devloop/dash/web/dash.css`, `devloop/dash/web/dash.js`,
  `tests/devloop/test_dash_render.py`

**Interfaces:**
- Consumes: class names and `data-*` hooks from Task 6.
- Produces: CSS tokens on `:root` for light, dark under `@media (prefers-color-scheme:
  dark)` and `[data-theme=dark]`, with tag tokens `--t-read`, `--t-edit`, `--t-write`,
  `--t-shell`, `--t-search`, `--t-agent`, `--t-web`, `--t-plan`, `--t-mcp`, `--t-other`,
  each AA against its chip background; system font stacks only. JS: `POLL_MS = 4000`;
  `openDrawer(key)`, `closeDrawer()`, `fillDrawer()` (called after every `swap`).

- [ ] **Step 1: Write the failing tests** `test_phone_rules_present`; extend
  `test_css_tokens_meet_aa` and `test_css_light_and_dark_define_the_same_tokens` to the tag
  tokens; keep `test_static_assets_have_no_inner_html_from_data`.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Desktop: KPI strip in 6 columns; attention bar full width;
  worker grid `repeat(auto-fill, minmax(300px, 1fr))`; feed and pipeline side by side above
  1100 px, stacked below; drawer a right panel 420 px wide. Phone (max 640 px): KPI 3 by 2,
  one column everywhere, feed rows as a two-line grid with `minmax(0, 1fr)` and ellipsis,
  drawer `position: fixed; inset: 0`. JS: click or Enter on `[data-open]` sets
  `location.hash = "slice=" + key`; `hashchange` opens or closes; `fillDrawer` copies the
  `#ds-KEY` source's children with `cloneNode(true)` (never `innerHTML`), keeps the drawer
  body's `scrollTop`, and shows the fixed "no longer active" text when the source is gone;
  Escape closes; focus moves to the close button on open and back to the card on close.
- [ ] **Step 4: Run** `pytest tests/devloop -k dash -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(devloop): add the drawer and the dense theme`, last line `cam`.

## Task 8: Leak sentry, mutations and browser check

**Files:**
- Modify: `tests/devloop/test_dash_server.py`, `tests/devloop/mutations.sh`

**Interfaces:**
- Consumes: everything above; `srv`, `call` and `_plant` in `test_dash_server.py`.

- [ ] **Step 1: Write the tests** `test_transcript_secrets_reach_no_route` (values in
  Acceptance tests; it writes Claude and Codex files into the fixture's executing runs and
  into a finished run used by a drawer) and `test_routes_unchanged_after_redesign`. Apply
  the class 1 mutation by hand, see the sentry FAIL, restore.
- [ ] **Step 2: Run** `pytest tests/devloop/test_dash_server.py -q`. Expected: PASS after
  the restore.
- [ ] **Step 3:** Add `mutate` entries for classes 1, 4, 5, 9 and 10 (Task 3 added 2, 3, 6,
  7 and 8), each naming its test; run `tests/devloop/mutations.sh`; every dash mutation is
  killed.
- [ ] **Step 4: VERIFY in a browser.** Extend a scratch copy of
  `/home/cam/scratch/devloop-dash/make_fixture.py` to write the transcript fixtures (scratch
  only, not committed), start `devloop-dash demo --host 0.0.0.0`, and at 1440 px and 390 px,
  light and dark: open a worker drawer, wait two polls, and confirm the drawer, its scroll,
  the filters and the page scroll hold; append lines to a fixture transcript and confirm the
  doing-now line and feed update within 8 s; move the slice to DONE and confirm "This slice
  is no longer active."; confirm no horizontal scroll at 390 px. Record steps and
  screenshots in the PR body.
- [ ] **Step 5: Commit** `test(devloop): guard transcript facts on every route`, last line
  `cam`.

## Builder brief notes

Commit subjects follow Conventional Commits, 72 characters max, no body unless it explains
why, final line `cam`, no tool-attribution trailers. Test depth: T0 the touched file's
tests per edit, T1 the slice's selection per commit, T2 at the gate on the exact head.
Name the defect class of each finding you fix; write tests first and see them red; attack
your own diff with the listed mutations; never widen a limit or skip a test to get green;
stop and report if a catalogued class repeats. One writer at a time in this worktree.
