# Refactoring

> Guidance for structural change whose intended behavior is preserved.

## When this applies

Use when improving structure, names, organization, or boundaries without an
intended behavior change.

## Rules

DO identify the behavior-preservation evidence appropriate to the changed risk:
tests, characterization checks, static or type checks, build evidence, or a
controlled comparison.

DO work in recoverable slices and rerun proportionate evidence as structure
changes.

DO separate intended behavior changes from refactoring unless a documented
exception explains why separation would increase risk.

DO record an explicit exception when no behavior-preservation evidence is
available, including residual risk and compensating controls.

DON'T assume a test suite exists or fully covers the changed behavior.

DON'T replace duplication with a boundary that lacks a shared invariant.

## Red flags

- "No tests means no refactor." Choose proportionate evidence or record an
  exception.
- "It is only cleanup." Identify what preserves behavior.

## Done when

- [ ] Behavior-preservation evidence is proportionate and observed.
- [ ] Intended behavior changes are separated or explicitly justified.
- [ ] Any evidence gap has approved compensating controls.
