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

Plan from current code. Before measuring or citing code, `git fetch` and read from a
fresh worktree or `git show origin/main:<path>`, never a local checkout that may be
behind, and use a venv built from that tree.

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

Name the choke point. A plan that adds a rule more than one call site must
follow, or adds a site to such a rule (a route, a command, a table, a loader,
a client, a spawn), states where the rule is enforced by construction, which
guard test fails when a site bypasses it, and the one mutation that must turn
that guard red. A helper every site is expected to call is not an answer. See
[cross-cutting concerns](cross-cutting-concerns.md).

Name the placement. A plan that adds a file or package carries a short
"Placement" table: each new file, the directory it goes in, the line of the
repo's layout map (the "Where things go" section of its CODEMAP) that puts it
there, and the existing module it sits beside. A file the map does not place is
a map change, stated in the plan, not an improvised folder. A directory already
over the fan-out limit takes no new modules; the plan names the sub-package
instead. Plan reviewers check the table against the map. See
[architecture](architecture.md).

Research established solutions FIRST, as the starting point of PLAN, before
drafting any custom design. For a non-trivial R2 or R3 problem (a parser,
scheduler, resource limit, protocol, statistical or crypto method, concurrency
scheme, data-integrity or retention scheme), the planner's FIRST move is an
online plus in-codebase search for how established tools, standards, libraries
and papers solve it. Do this before authoring a design, not to justify one after
the fact, and never let an internal prior plan or your own recalled analysis
substitute for the external search: a problem that looks fully reasoned-out
internally is exactly the one most likely to be reinventing a named, vetted
pattern (tamper-evident-log retention, seekable RNG, FK preservation, and the
like all have established answers). The plan carries an "Established solutions"
section: the search actually run, what was found, which source the design
follows (cited), and why any remaining custom code is needed. Prefer the standard
library or a maintained library over a hand-written version; adopt a published
pattern over an invented one. Keep the result proportionate: don't overbuild,
and don't add machinery for 1% scenarios; record those as known issues or refuse
them fail closed. Plan reviewers check this section (including that the external
search happened). See [reuse first](reuse-first.md).

Test authorship is role-neutral. A planner, builder, or another qualified
contributor may create tests, but no later contributor may weaken pre-agreed
acceptance criteria. R1 may record test cases without a separately authored test
artifact. Builders may add unit tests while preserving the agreed acceptance
coverage. Exit with the plan and required review evidence.

### DEVELOP

Implement the approved approach in recoverable slices. Preserve acceptance
criteria and record any approved change to them. Exit with the intended behavior
implemented and relevant checks ready to run. Builders run T0 and T1
([testing](testing.md#test-depth)) and write commit messages per
[workspace and VCS](workspace-and-vcs.md#commit-messages).

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
probes to check runtime behavior that reading alone can miss. Each review or
gate runs T2 ([testing](testing.md#test-depth)): T1 on the exact head it
records, its own mutations, and its assigned risk cells. The claimed risk cells
are split by reviewer role, or else the first gate records the set it ran; a
later gate re-runs a cell only when it disputes the result or the head changed.
Severity is
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

The [local operations profile](profiles/local-operations.md) may add close-out
steps, including the pre-merge done-state; they are gate conditions where it says so.

### DOCUMENT

Update affected durable sources and project-required records. Add knowledge only
when it has demonstrated cross-session value and is in scope and authorized.
Exit with current docs or a stated reason no update was required.

The [local operations profile](profiles/local-operations.md) may add close-out
steps, including the docs-current, graph-current, and section-complete message rules;
they are gate conditions where it says so.

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
- [ ] A plan that adds files carries a Placement table checked against the
      repo's CODEMAP "Where things go" map.
- [ ] A plan that adds a rule with more than one site, or a site to one, names
      the choke point, the guard test, and the mutation that turns it red.
- [ ] Every close-out step the local operations profile marks as a gate
      condition is met, or its recorded deferral is one the profile allows.
