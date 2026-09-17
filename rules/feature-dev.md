# Feature Development

> Guidance for new behavior, components, and user-visible changes.

## When this applies

Use when adding behavior or a new component. Also read
[API and compatibility](api-and-compatibility.md) for externally consumed
interfaces.

## Rules

DO identify the user need, observable behavior, boundaries, and acceptance
criteria before implementing.

DO choose the smallest coherent vertical slice that proves the design.

DO place code where its responsibility and change reason cohere with existing
structure.

DO abstract when a stable shared invariant and shared reason to change justify
the boundary.

DON'T add configuration, generality, or indirection for imagined future use.

DON'T use a numeric repetition threshold as an abstraction rule.

## Red flags

- "It may be useful later." State the present consumer and behavior or defer it.
- "The code repeats enough times." Identify the shared invariant and divergence
  risk.

## Done when

- [ ] A coherent slice proves the intended behavior.
- [ ] Acceptance evidence covers the stated behavior and failures.
- [ ] New boundaries have a clear responsibility and reason to change.
