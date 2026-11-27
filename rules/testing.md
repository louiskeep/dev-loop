# Testing

> Guidance for choosing, writing, maintaining, and interpreting tests.

## When this applies

Use when designing or changing tests, test strategy, fixtures, or acceptance
evidence.

## Rules

DO derive test distribution from failure cost, system boundaries, and the
confidence each layer can provide, rather than a universal shape.

DO test observable behavior, known failure modes, contracts, and recovery paths
at the appropriate boundary.

DO make tests deterministic and isolate controllable sources of time, network,
filesystem, randomness, and external state where appropriate.

DO define R2 and R3 observable behavior, known failure modes, and acceptance
tests during PLAN. Prefer fail-before proof when meaningful; otherwise record
the evidence gap and compensating validation.

DO preserve pre-agreed acceptance criteria regardless of who adds or maintains
the tests. A builder may add tests but may not weaken that coverage. Weakening
includes dropping an equality or metadata flag, narrowing a comparison to
values-only, skipping or deleting a case, and loosening a tolerance. A reference
or oracle divergence is fixed at the source, not tested around.

DO measure test strength before any merge that changes source, not test count.
Record per-unit line and branch coverage plus a mutation score for the units
where a silent defect is most costly. Grade the changed units rather than the
whole tree, so the measurement stays affordable at merge cadence.

DO set bars from a measured baseline rather than a chosen number. Treat a
surviving mutant in a security or correctness primitive as build work, not as a
number to record and move past.

DO name honestly what kind of testing each unit actually received. An enumerated
table of cases is not generative property testing, and a passing suite is not
evidence of a property nobody asserted.

DO fix flaky tests or quarantine them with an owner, deadline, impact, and
reason. Delete only tests that are obsolete.

DO use property-based tests (Hypothesis in Python) for invariant claims such as
"route A equals route B", "output is reproducible", "order or batching does
not change the result", or round-trip guarantees. Generated inputs find the
empty, null, boundary and ordering cases that hand-picked examples miss. Keep a
fixed seed or database for reproducibility and pin any failing example as a
regular test.

DON'T optimize for a coverage number instead of meaningful assertions.

DON'T over-mock until tests only prove the mock configuration.

## Test depth

A change runs the tests it can break. The full suite runs once per merge, on
the exact merge head, and on a schedule.

| Tier | When | Who | What runs |
|---|---|---|---|
| T0 | Each edit | Builder | The touched file's own tests, stop on first failure |
| T1 | Each commit and the final head of a slice | Builder | Selected tests (below), the named acceptance tests, guard and boundary-check tests, lint, format, and type checks |
| T2 | Gate or review | Reviewer | T1 re-run on the exact head it records, its assigned risk cells (see the development loop's REVIEW), the affected package or marker subset, and the reviewer's own mutations |
| T3 | Before every merge to the main branch | Orchestrator, once | The full default suite on the merge head, failing ids diffed against the stored baseline; coverage and mutation on changed units |
| T4 | Nightly and before a release | Scheduler, idle window | Full suite plus opt-in markers (performance, real database, emulators, determinism); broad mutation sweeps; baseline refresh |

DO select T1 tests by change impact, unioned: changed test files; every test
file that imports a changed module; every test file that names a changed
signature's symbol; the repository's guard tests; and the rows of a per-repo
impact table that maps source areas to extra test sets.

DO escalate to a full suite at the slice, before the gate, when the change
touches: shared fixtures or test configuration, the test runner or selection
tooling, a schema or data migration, a dependency lock, authentication or
authorization checks, serialization or a frozen contract surface, a version
pin or compatibility contract, or when it moves or deletes test files. Escalate
also when the selection cannot be computed (renames, generated code, dynamic
imports) or a gate disputes the scope.

DO claim "no new failing id against the baseline", never "suite green". The
baseline is the failing-id set and collected-id count for a named main-branch
commit. A new failing id blocks; a baseline id that now passes is reported; a
collected-count change beyond the slice's own adds and removes is reported.
Every known failure has an owner and reason in the baseline.

DO compare T3 against the stored main baseline without updating it. Only after
the merge succeeds does that head's T3 result (failing-id set, collected count,
main commit) become the new main baseline. A failed or abandoned merge leaves
the baseline untouched. T4 refreshes it again nightly.

DO bind every full run to a commit. A commit added after it needs a new run
before merge. Each gate records the head it tested; when the merge head equals
a head that already had T3, nothing is re-run.

DO keep budgets: T1 within minutes, T2 within half an hour of runner time. A
run longer than that is a full suite or a benchmark and is announced first.
Tests slower than about 30 seconds each carry a marker and leave T1.

DO state test scope in every build brief and every builder report:

```text
Test scope
Tiers:        T0, T1 [, T3 because <trigger>]
Named tests:  <files and ids>
Selection:    <selection command>
Plus:         <impact-table rows>
Guards:       <guard test paths>
Triggers:     none | <trigger>
Mutations:    <mechanism -> test that went red>
Lock time:    <minutes>
Full suite:   not at slice | ran because <trigger>, baseline diff: <result>
```

DON'T run the same full suite twice on the same head, and DON'T run a
base-side full suite: diff against the stored baseline instead.

DON'T run a full suite at the slice without naming the trigger that requires it.

DON'T edit a known failing test to make it pass, or retry a flaky test until it
passes.

## Red flags

- "The pyramid requires this layer." Choose the layer from the boundary and
  failure cost.
- "The flaky test is annoying." Fix it or quarantine it with a removal path.
- "Full suite green." Ask for the failing-id diff against the baseline.
- "The gate also ran the full suite to be safe." That is a double run; the
  merge-head run is the one that counts.
- A "full suite" result whose command ran only the impact selection.

## Done when

- [ ] Tests and other evidence cover the intended behavior and key failures.
- [ ] Fail-before or compensating evidence is recorded where applicable.
- [ ] Test strength on the changed units is measured, not inferred from counts.
- [ ] Flaky, obsolete, and coverage limitations are handled honestly.
- [ ] Test scope is recorded, and any slice-level full suite names its trigger.
- [ ] One full-suite run is bound to the merge commit, with a failing-id diff
      against the stored baseline.
