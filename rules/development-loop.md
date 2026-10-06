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

Name the rules. Every plan carries a "Rules consulted:" line near its top
listing the rulebooks the [routing table](README.md) selected for this work
(always including the universal and development-loop rules), read during
planning rather than recalled. Plan reviewers check the list fits the work, and
the builder's brief points at the same rulebooks. The dev-loop plugin's
`rules_gate` hook enforces the read and the line.

Pin the guarantee. The plan states exactly what the change promises and lists
its non-goals. A review finding outside that stated guarantee becomes a
recorded follow-up item, not a blocker for this change. A guarantee that is
never written down lets each review round go one layer deeper.

Write design notes when the change adds structure. A plan that adds a module,
an abstraction, or a new variant of something expected to keep growing (an
operator, a route, a backend) carries a short "Design notes" section: which
design principles from [architecture](architecture.md) apply and the concrete
change pain each one addresses (or "none: no new structure"), the established
pattern followed and its source, and where each new fact lives (one place, never
restated). The builder's brief carries these notes plus the house style below,
and the reviewer checks the build against them. House style: typed context
objects over long parameter lists; one place per fact; single-purpose
functions; no flag arguments that switch behavior; comments that explain why.
Apply it to the code the change touches; it is not a license to restyle
untouched code.

Research established solutions before designing your own. Before an R2 or R3
plan proposes custom logic for a non-trivial problem (a parser, scheduler,
resource limit, protocol, statistical or crypto method, concurrency scheme), the
planner searches online and in the codebase for how established tools,
standards, libraries and papers solve it, and the plan carries an "Established
solutions" section: what was found, which source the design follows (cited),
and why any remaining custom code is needed. Prefer the standard library or a
maintained library over a hand-written version; adopt a published pattern over
an invented one. Keep the result proportionate: don't overbuild, and don't add
machinery for 1% scenarios; record those as known issues or refuse them fail
closed. Plan reviewers check this section. See [reuse first](reuse-first.md).

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
and unchecked scope. The first pass is exhaustive: the reviewer lists every
finding it can find, not only the first blocker, and may run small read-only
probes to check runtime behavior that reading alone can miss. Severity is
proportional to real-world risk: a finding blocks only if it is plausible in
real use of the system as deployed. A finding that needs an astronomically
unlikely event is recorded as low in the project's known-issues (tech debt)
list, with a sentence on why; it does not block, is not scheduled, and is fixed
only if it recurs in production. For anything rated high or above, the reviewer states
how the failure would occur in real use. Exit with the required independent
verdict.

### REMEDIATE

Correct supported findings at the root. If a change alters an artifact, repeat
SELF-CHECK and VERIFY, then obtain the required re-review. Patch what the review
found; do not redesign during remediation. A redesign is a new plan and needs
human direction. Each re-review checks the specific fix for new breakage, not
only the earlier findings. After three review rounds on the same plan or
artifact, stop for human direction: accept with documented gaps, narrow the
scope, or continue. Exit only when findings are resolved, accepted through an
approved exception, or returned for human direction.

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
- [ ] Plans carry a "Rules consulted:" line for the rulebooks actually read.
- [ ] R2/R3 plans carry an "Established solutions" section (online and codebase
      research, cited source pattern, justification for any custom code).
- [ ] The slice's durable docs are current: the roadmap carries only current +
      next-up work, the completed item moved to the shipped log, and any trailing
      plan STATUS lines were reconciled to the code.
