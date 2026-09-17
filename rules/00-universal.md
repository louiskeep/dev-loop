# Universal Rules

> General guidance for all development work. Read this with the task-specific
> rulebooks in this directory.

## Precedence and immutable invariants

Follow this order when guidance overlaps:

1. Platform safety, authorization, and sandbox rules.
2. These immutable invariants and [risk and exceptions](risk-and-exceptions.md).
3. Repository-specific instructions.
4. Task-specific rulebooks selected by the routing table.
5. A local implementation profile.

Repository guidance may specialize general guidance, but it cannot weaken an
immutable invariant, platform authorization, or a required human authorization.
The immutable invariants are: preserve user-owned work; do not disclose secrets
or personal data; be honest about unrun checks; resolve destructive targets
exactly; and obtain required human authorization before an R3 side effect.

## The goal

Make systems safe and economical to change. Optimize for the next reader and
modifier, not for cleverness, line count, or keystrokes.

## Rules

DO check for an existing skill, tool, package, template, or internal capability
before building custom work. See [reuse first](reuse-first.md).

DO understand the relevant source and surrounding context before changing it.
See [exploration](exploration.md).

DO classify risk and carry the required evidence through the work. See
[risk and exceptions](risk-and-exceptions.md).

DO preserve user-owned changes and keep the working tree recoverable. See
[workspace and VCS](workspace-and-vcs.md).

DO make each diff hunk traceable to the request, necessary support work, or a
documented exception. See [scope discipline](scope-discipline.md).

DO provide observed evidence before claiming success. See
[verification](verification.md).

DO follow established local conventions unless an approved change requires a
different convention.

DON'T weaken a pre-agreed acceptance criterion merely to make a check pass.

DON'T claim coverage that a check did not provide.

## Red flags

- "It probably works." Stop and obtain evidence.
- "This nearby change is close enough." Trace it to the request or separate it.
- "A repository rule lets me bypass safety." Check precedence and authorization.

## Done when

- [ ] The applicable risk level and evidence are recorded.
- [ ] User-owned work is preserved.
- [ ] The scoped change and its limitations are stated honestly.
