# Code Review

> Guidance for independent evaluation of a change and its evidence.

## When this applies

Use when reviewing a diff, change proposal, or exact artifact for a required
independent gate.

## Rules

DO review correctness before design, security, resilience, and clarity.

DO cite evidence for every finding: a location, counterexample, failed check, or
specific missing evidence.

DO label findings as BLOCKER, HIGH, MEDIUM, or LOW. Include confidence and the
checked and unchecked scope in the verdict.

DO use BLOCKER for an unsafe or invalid release condition, HIGH for likely
material harm, MEDIUM for a meaningful defect or maintainability risk, and LOW
for a bounded improvement that does not block the decision.

DO distinguish a finding from a question, suggestion, and unverified concern.

DO check new structure against the design principles in
[architecture](architecture.md#design-principles-apply-when-they-earn-their-keep)
when the change adds a variant, a module, or an abstraction. Raise a finding only
when the change creates or worsens a concrete change pain (a new case edits
central branching, parallel tables drift, a module gains an unrelated reason to
change), not because code fails to match a pattern.

DO run tests at review, not only read: T1 on the exact head, your assigned risk
cells, and your own mutations ([testing](testing.md#test-depth)). Record the
head tested. Do not run the full suite; that runs once on the merge head.

DO check the builder's report carries a Test scope block, that any slice-level
full suite names its trigger, and that commits follow
[workspace and VCS](workspace-and-vcs.md#commit-messages).

DON'T imply coverage beyond the evidence reviewed.

DON'T silently repeat a review at GATE. See [development loop](development-loop.md).

## Red flags

- "Looks fine." Name what was checked and what was not.
- "Severity is obvious." State the impact, likelihood, confidence, and evidence.

## Done when

- [ ] Findings have severity, confidence, and evidence.
- [ ] The verdict names checked and unchecked scope and the head SHA tested.
- [ ] Release-blocking conditions are unambiguous.
- [ ] Every new or edited log call was checked for data values, expression text, and input-echoing exception text ([observability](observability-and-resilience.md)).
