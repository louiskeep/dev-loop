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
the tests. A builder may add tests but may not weaken that coverage.

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

DON'T optimize for a coverage number instead of meaningful assertions.

DON'T over-mock until tests only prove the mock configuration.

## Red flags

- "The pyramid requires this layer." Choose the layer from the boundary and
  failure cost.
- "The flaky test is annoying." Fix it or quarantine it with a removal path.

## Done when

- [ ] Tests and other evidence cover the intended behavior and key failures.
- [ ] Fail-before or compensating evidence is recorded where applicable.
- [ ] Test strength on the changed units is measured, not inferred from counts.
- [ ] Flaky, obsolete, and coverage limitations are handled honestly.
