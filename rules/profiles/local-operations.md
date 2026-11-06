# Local Operations Profile

> The owner's dated close-out rules and the local tools that carry them.
> [Development loop](../development-loop.md) GATE and DOCUMENT point here. Read
> this profile wherever the development loop applies. Where a section below says
> it is a gate condition, it is one: the slice is not done until it holds.

## Purpose

The development loop stays stack-agnostic. This profile holds the rules the
owner added for local work, with their dates, and the local tool names they use.
Moving them here does not soften them. Reviewer roles named below are mapped to
local implementations in the [local agent stack profile](local-agent-stack.md).

## GATE: pre-merge done-state

**Pre-merge done-state (Cam, 2026-10-18): a merge is not green until every item
below is true.** The merge is the R3 side effect; do not take it until this list
passes (a standing "merge on green" authorization means green INCLUDES this list,
not just the two review verdicts). Split into what must be true BEFORE the merge and
the close-out that must finish immediately AFTER it, before the slice is DONE.

Before the merge:
- [ ] Independent review GO on the exact changed artifact; every finding
      remediated and re-verified first-hand, not relayed.
- [ ] Cross-model final gate GO on the finished artifact, after the independent
      review; each finding it raised fixed with a regression that fails on the
      pre-fix code.
- [ ] Each gate ran T1, its own mutations, and its assigned cells on the head it
      recorded (see development-loop REVIEW and
      [testing](../testing.md#test-depth)).
- [ ] Neither gate ran the full suite; it runs once, on the merge head (below).
- [ ] Local CI green on the changed units: lint + format + types + the slice's test
      suites, across the companion / no-companion matrix where the slice touches
      native paths. (GitHub CI is not the gate unless the repo says so.)
- [ ] One full-suite run on the exact merge head, with no new failing id against
      the stored failing-id baseline. A commit after that run needs a new run; no
      second full run on the same head.
- [ ] Acceptance proves the behavior and the stated invariants non-vacuously
      (e.g. output matches the oracle, route-independence, determinism); no
      weakened, narrowed, skipped, or deleted test.
- [ ] Appropriate logging wired for the slice's behavior, per the project's
      logging design (for example, three streams - a
      user-facing job event to the job log, an operational action/fallback to
      the server log, a human-initiated state change to the audit log), obeying
      the observability leak rule (log location + exception TYPE, never a value,
      query/expression text, or raw exception message). Logging is a per-slice
      done-state item, not a deferred batch.
- [ ] CHANGELOG, and any other durable doc that does NOT need the post-merge SHA,
      updated in the implementing repo to what actually shipped. This is a
      pre-merge item, not a post-merge chore (a slice has merged without it).
- [ ] Plan STATUS lines reconciled to the code; branch rebased clean on current main.

Immediately after the merge, before the slice is DONE and before the next slice opens
(this is the DOCUMENT close, below):
- [ ] Shipped-log entry added WITH the merge SHA; roadmap moved the item off
      current/next-up.
- [ ] Graph refreshed (`graphify <repo> --update`) against main, or a recorded
      reason it is deferred.
- [ ] Section-complete plain-language (`simple-english`) Slack sent.

A slice that is merged but whose close-out has not finished is NOT done: do not open
the next slice until the close-out is complete or its deferral is recorded.

## DOCUMENT: docs-current rule

**Docs-current rule (Cam, 2026-09-15): a slice of work is not DONE until its
durable docs match reality, and the roadmap is part of that.** After each slice
(a gated, merge-ready or merged unit of work), as the final step of DOCUMENT:

- Move the completed item OFF the roadmap into the shipped log (the repo's
  "recently shipped" doc / ledger), dated, with the merge/gate evidence.
- Update the roadmap's CURRENT-work status and NEXT-UP so the roadmap carries
  only current + next-up work, never a backlog of already-done items.
- Reconcile any plan/status doc whose own status lines now trail the code
  (a plan's inline STATUS must not claim a task is pending when it merged).
- Prefer a single authoritative shipped log over restating history in the
  roadmap; the roadmap points to it rather than duplicating it.

Auto-memory is a short pointer, not a substitute for this: the roadmap + shipped
log are the durable, human-readable source of truth. A stale roadmap that lags
the code by several phases is a process failure, not a cosmetic one -- it causes
real confusion about what has shipped. Treat "roadmap + shipped log current" as a
gate condition for closing the slice, the same way green tests are.

## DOCUMENT: graph-current rule

**Graph-current rule (Cam, 2026-10-11): refreshing the repo knowledge graph is a
mandatory DOCUMENT-phase action, not optional maintenance.** When a slice changed
code in a graphed repo, refresh that
repo's graphify graph as part of closing the slice:

- Run `graphify <repo> --update` (incremental) after the slice merges to the
  graphed repo's main, so the graph tracks main. For a multi-slice program,
  refresh at least once before relying on the graph for planning and again at
  program end; the graph is a planning aid across the whole program, so a stale
  one is a trap for every later slice.
- Build from a tree at the repo's current main, never a stale checkout. A graph
  built from stale code is worse than no graph because it looks authoritative.
- A graph many commits behind main is a process failure for the same reason a
  stale roadmap is: it feeds bad information into planning and reachability
  checks. Treat "graph current for the graphed code this slice touched" as a gate
  condition for closing the slice. If a refresh is genuinely deferred (for
  example a heavy rebuild batched to program end), record that reason.

## DOCUMENT: section-complete Slack explanation

**Section-complete Slack explanation (Cam, 2026-10-12): when a section of work
finishes (a slice merged, or a self-contained chunk done), send one Slack
message explaining the feature in plain language.** This fires at SECTION
boundaries, not after each turn or each commit.

- Write it in the `simple-english` style (short sentences, one idea each, no
  jargon, for a non-code reader): 1 to 4 short paragraphs saying, in plain terms,
  what the feature is and what it does or changes. Summarize the OUTCOME, not the
  internals: do NOT include implementation mechanisms (the test "witness", kernel
  or ABI details, edge-case parity caveats, accepted-divergence notes) unless one
  directly changes what a user would do or see. Cam wants "what we did," not how
  it was proven.
- Explain what was accomplished, not how. No SHAs, file paths, or severity
  tiers. This complements the existing Slack cadence (a terse "done" ping is not
  enough at a section boundary; the plain-language feature summary is).
- Send it through `~/.claude/scripts/slack-notify.sh manual "<message>"`.

## Other dated owner rules

- Development loop PLAN, "Plan from current code" (fetch, read from a fresh
  worktree or `origin/main`, use a venv built from that tree): owner, 2026-11-06.
  The rule stays in the loop because it is not tool-specific.

## Done when

- [ ] Every pre-merge done-state item held before the merge.
- [ ] The slice's durable docs are current: the roadmap carries only current +
      next-up work, the completed item moved to the shipped log, and any trailing
      plan STATUS lines were reconciled to the code.
- [ ] If the slice touched a graphed repo, that repo's graphify graph was
      refreshed (`graphify <repo> --update`) against current main, or a reason the
      refresh was deferred is recorded.
- [ ] At a section boundary (slice merged / chunk done), a 1-to-4-paragraph
      plain-language (`simple-english`) Slack explanation of the feature was sent.

## Maintenance

Add a dated owner rule here, not in a root rulebook, when it names a person, a
local tool, or a local script. The checker rejects those names in root
rulebooks.
