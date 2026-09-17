# Development Loop

> The task spine. Use [risk and exceptions](risk-and-exceptions.md) to select
> gates; use task rulebooks for the technical work within each phase.

## When this applies

Use for end-to-end non-trivial work. R0 and permitted R1 gates may collapse only
as the risk policy allows; R2 and R3 require the full applicable sequence.

## Flow

```text
FRAME -> PLAN -> DEVELOP -> SELF-CHECK -> VERIFY -> REVIEW -- clean --> GATE -> DOCUMENT
                                                |
                                             findings
                                                v
                                           REMEDIATE
                                                |
                                                v
                                     SELF-CHECK -> VERIFY -> REVIEW
```

Any artifact change in remediation returns through SELF-CHECK and the applicable
VERIFY step before re-review. GATE consumes evidence for the exact artifact; it
does not silently repeat REVIEW.

## Phases

### FRAME

State the problem, definition of done, boundaries, applicable rulebooks, and
risk. Check reuse before building. Exit with an understood request and a
recorded risk level.

### PLAN

Write a proportional approach. R2 and R3 require a plan on disk and independent
plan review before implementation. For R2 and R3, define observable behavior,
known failure modes, and acceptance tests before implementation. Use a
fail-before proof when meaningful; when it is unsafe, intermittent, or not yet
available, record the best available evidence and compensating validation.

Test authorship is role-neutral. A planner, builder, or another qualified
contributor may create tests, but no later contributor may weaken pre-agreed
acceptance criteria. R1 may record test cases without a separately authored test
artifact. Builders may add unit tests while preserving the agreed acceptance
coverage. Exit with the plan and required review evidence.

### DEVELOP

Implement the approved approach in recoverable slices. Preserve acceptance
criteria and record any approved change to them. Exit with the intended behavior
implemented and relevant checks ready to run.

### SELF-CHECK

Inspect the diff and relevant resulting context. Run the applicable checks,
check prose for clarity and truthfulness, and record known limitations. Exit with
author evidence suitable for an independent reviewer.

### VERIFY

Run targeted and project-required verification. State what each check covers and
does not cover; acceptance green is necessary evidence, not proof of untested
properties. Where the change alters source, verification includes the test-strength
measurement in `testing.md` on the changed units, so REVIEW and GATE consume
evidence about assertion quality rather than test counts. Exit with observed
evidence, failures, skips, and limitations.

### REVIEW

An independent reviewer evaluates the exact artifact against the plan, evidence,
and applicable rulebooks. Review produces findings and a verdict with checked
and unchecked scope. Exit with the required independent verdict.

### REMEDIATE

Correct supported findings at the root. If a change alters an artifact, repeat
SELF-CHECK and VERIFY, then obtain the required re-review. Exit only when
findings are resolved, accepted through an approved exception, or returned for
human direction.

### GATE

Consume the exact artifact identifier, verification evidence, review verdicts,
known limitations, and release or recovery readiness. A repository may add a
separate named gate; it must not silently duplicate review. R3 also requires
explicit human authorization immediately before the side effect.

### DOCUMENT

Update affected durable sources and project-required records. Add knowledge only
when it has demonstrated cross-session value and is in scope and authorized.
Exit with current docs or a stated reason no update was required.

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

## Red flags

- "The review was clean before this fix." Re-check the changed artifact.
- "Acceptance passed, so every property is proven." State the coverage gap.
- "The test author must be different from the builder." Preserve independent
  acceptance criteria without imposing a role label.

## Done when

- [ ] The risk-required phases have exact-artifact evidence.
- [ ] Remediation changes re-entered self-check and verification.
- [ ] Review and gate have distinct recorded purposes.
- [ ] Behavior, failure modes, and acceptance coverage were planned for R2/R3.
- [ ] The slice's durable docs are current: the roadmap carries only current +
      next-up work, the completed item moved to the shipped log, and any trailing
      plan STATUS lines were reconciled to the code.
