# Program board Implementation Plan

**Goal:** Give the dev-loop a read-at-a-glance progress board (Recently
completed / Current / Next up) that stays current with no model tokens, and
that the conductor can edit cheaply.

Rules consulted: dev-rules `00-universal.md`, `development-loop.md`,
`reuse-first.md`, `architecture.md`, `testing.md`, `documentation.md`.

**Guarantee (and non-goals).** The board renders from one JSON document and
refreshes from data the loop already maintains. It is a *view*, not a new
source of truth: it never gates anything, and it holds no state the loop
depends on. Non-goals: no new per-substep narration (the token-cost dial the
user declined); no roadmap mutation; no network; no runtime dependencies.

**Established pattern.** Kanban (Trello, GitHub Projects, Jira): three columns
read left to right as done / doing / next. Chosen because it is the simplest
board people already know how to read. No library: a self-contained HTML page
with inline CSS, light/dark via `prefers-color-scheme`.

## Design notes

- **One editable JSON object (`.loop-board.json`) is canonical.** Deriving the
  whole board from roadmap/shipped prose alone is brittle (the roadmap has no
  machine-readable "next up"), and side discussions/questions have no existing
  source. Making the JSON canonical lets the conductor edit any card for a few
  tokens, and lets `refresh` *seed* it cheaply. This is the one place each
  board fact lives.
- **Refresh re-derives the derived columns; authored cards survive.** Each
  refresh rebuilds `recently_completed`, `current`, and `next_up` from the
  current source, so a card the source no longer produces is pruned instead of
  left as a stale union with the new cards. A card's `source` field is the
  authority: `roadmap` / `shipped-log` / `loop-state` are derived; a hand-added
  card, or one moved with `move` (which stamps `source: "authored"`), is
  authored and always kept. A phase pinned with `set-phase` is carried as an
  overlay keyed to the card id, only while that id is still derived. A refresh
  can run on every step without eating conductor edits and without accumulating
  stale cards. `init` stays non-destructive; `init --force` is the explicit
  reset.
- **Phase is derived only as far as recorded state proves it.** `derive_phase`
  reads gate status + open findings from `.loop-state.json`: coarse boundaries
  (PLAN -> DEVELOP -> REVIEW -> REMEDIATE -> GATE) are provable; the
  trace-less phases (FRAME/SELF-CHECK/VERIFY/DOCUMENT) are left to an optional
  `set-phase`, which pins the phase against the hook.
- **Updates are a hook, not a model habit.** The loop has no phase event, so
  `board_update` reacts to the signals that exist: the conductor's own
  `loop_state.py`/git commands (PostToolUse on Bash) and Stop. It is
  best-effort, silent, and a no-op without a board file, so it adds no tokens
  and is inert until opted into.

## Components

- `hooks/loop_board.py`: stdlib-only model (load/validate/save of a versioned
  schema), seeders (`parse_shipped`, `parse_next_up`,
  `parse_current_assignment`, `derive_phase`, `scan_loop_states`),
  `refresh` (re-derive with authored preservation), `render_html` (kanban), a
  stdlib `serve`, and a small CLI
  (`init`/`refresh`/`render`/`add-question`/`set-phase`/`move`/`show`/`serve`).
- `hooks/board_update.py`: PostToolUse(Bash) + Stop hook that refreshes and
  re-renders on loop-state/git commands and at step end.
- `config.json`: a `board` block (roadmap/shipped paths, repos to scan,
  section headings, done-markers, counts, output paths), overridable per repo
  in `.loop-config.json`.
- `tests/test_loop_board.py`: parsing, phase derivation, re-derive pruning and
  authored preservation, render escaping, CLI, and hook no-op/trigger behavior.

## Known limitations

- Roadmap "done" detection is a lead-line token match (`done_markers`); a
  task written "DONE except ..." reads as done. The board is a seed the
  conductor curates, so a misread is a one-line `move`, not a wrong gate.
- `next_up` is capped (`next_up_count`) and ordered by document position, not
  priority.
