---
name: documenting-the-slice
description: >-
  Use when closing a gated, merge-ready, or merged slice, before calling it done, to bring the
  roadmap, shipped log, and any trailing plan status current. Also use whenever you feel pressure to
  call work done or merge-ready while the roadmap or shipped log still lags the code.
---

# Documenting the slice

## Overview

A slice is not done until its durable docs match reality, and the roadmap is part of that. Treat
"roadmap and shipped log current" as a close-the-slice gate condition, the same as green tests.
Delegate to barry (Haiku for mechanical sync, Opus when the docs need judgment). Policy:
`rules/documentation.md`, `rules/development-loop.md` (DOCUMENT).

## The close-out

1. Move the completed item **off the roadmap and into the shipped log** (the repo's recently-shipped
   ledger), dated, with the merge or gate evidence.
2. Update the roadmap's CURRENT and NEXT-UP so it carries only current plus next-up work, never a
   backlog of already-done items.
3. Reconcile any plan or status doc whose inline STATUS now trails the code (no "pending" on a task
   that merged).
4. Keep one authoritative shipped log; the roadmap points to it rather than restating history.

## Project specifics (Decoy)

- Shipped history lives in `decoy-platform/docs/backlog/RECENTLY-SHIPPED.md`; the cross-repo roadmap
  is `decoy-platform/docs/ROADMAP.md`. Do not create new roadmap or todo docs.
- New docs carry a `Status:` header (current / plan / proposal / record). It names the doc's kind, not
  its accuracy: `current` is not "verified" and `plan` is not "unbuilt".
- Auto-memory is a pointer, not a substitute. The roadmap and shipped log are the human-readable
  source of truth.

## Boundary

barry edits documentation, docstrings, comments, and cosmetic names only, never program logic,
behavior, or test assertions, and he does not merge.

## Red flags

- "Docs can follow later." There is no later; docs-current is the close condition.
- A roadmap lagging the code by phases. That is a process failure, not a cosmetic one.
- Restating shipped history in the roadmap instead of pointing to the shipped log.
