# Architecture

> Guidance for structural decisions, module boundaries, and durable design.

## When this applies

Use for meaningful boundaries, modules, services, or structural trade-offs.
Pair it with [API and compatibility](api-and-compatibility.md) when consumers
outside the changed unit are affected.

## Rules

DO identify the stable shared invariant and the reason a boundary must change.

DO prefer high cohesion, low coupling, and simple interfaces around volatile
implementation detail.

DO compare the proposed structure with the smallest workable alternative and
record significant decisions. See [documentation](documentation.md).

DO extract a distributed boundary only for demonstrated operational or ownership
need.

DON'T use a repetition count as an abstraction gate. Repeated code is evidence,
not a decision rule.

DON'T adopt a named pattern without naming the problem it solves and its costs.

## Red flags

- "We might need it later." Identify a present change reason or defer it.
- "It appears three times." Identify the shared invariant and likely divergence.

## Done when

- [ ] Boundaries and reasons to change are clear.
- [ ] Complexity is justified by current evidence.
- [ ] Significant trade-offs are recorded.
