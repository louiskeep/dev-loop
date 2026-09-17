# Reuse First

> Guidance for choosing an existing capability, dependency, template, or custom
implementation.

## When this applies

Use before creating non-trivial logic, artifacts, or operational capability.

## Rules

DO timebox a search for existing capabilities, internal components, templates,
and maintained external options.

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
