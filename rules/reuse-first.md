# Reuse First

> Guidance for choosing an existing capability, dependency, template, or custom
implementation.

## When this applies

Use before creating non-trivial logic, artifacts, or operational capability.

## Rules

DO timebox a search for existing capabilities, internal components, templates,
and maintained external options.

DO search online (library documentation, standards, well-known open-source
implementations, papers) before designing a custom solution to a non-trivial
problem, and cite the pattern the design follows.

DO prefer the standard library or a maintained library to hand-written code
for parsing, protocols, resource limits and similar well-trodden problems.

DON'T build machinery for rare (about 1%) scenarios; record them as known
issues or refuse them fail closed.

DO compare viable options for maintenance, provenance, license, security,
stability, footprint, compatibility, and exit cost.

DO record why the chosen option fits and why a custom implementation, vendored
copy, or fork is justified when selected.

DO record an update and ownership plan for a vendored or forked dependency.

DO make dependency inputs reproducible and attributable. See
[config, secrets, and supply chain](config-secrets-and-supply-chain.md).

DON'T adopt the first option that appears to fit without comparison.

DON'T add a dependency for a trivial, well-understood local operation.

## Red flags

- "It is faster to write it." Include maintenance, test, and exit costs.
- "It works today." Check provenance, stability, and update responsibility.

## Done when

- [ ] The search and comparison are proportionate to risk.
- [ ] The selected option has a recorded rationale.
- [ ] Forked or vendored code has an update plan.
