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

## Layout: where things go

Keep the source tree predictable, so anyone can find a module without searching.
The repo's CODEMAP has a "Where things go" section with one line per kind of
module: which package owns that concern, how its files are named, and where its
tests and sentries live. Plans place each new file against that map (see the
PLAN "Placement" rule in [development loop](development-loop.md)). Enforce the
layout by construction, not by review alone:

- Size. A hand-written source file stays under the repo's cap (about 800 lines
  unless the repo sets a lower one), and a file already over it does not grow.
- Fan-out. A package holds at most about 25 modules directly. A package over the
  limit is frozen at its current count by a ratchet test and takes new modules
  only in a named sub-package.
- Layers. Import direction is declared as contracts and checked by an
  established tool (for Python, `import-linter`): lower layers never import
  higher ones, and consumers reach a library only through its public API.

## Design principles (apply when they earn their keep)

These are lenses, not a checklist. Reach for one when it names a real problem in
the change at hand: a module that keeps getting edited for unrelated reasons, a
new case that forces edits to central branching code, a caller that depends on
far more than it uses. If no such problem exists, leave the code alone.

- **Single responsibility.** A module should have one reason to change. When a
  file is touched by most unrelated changes, or two concerns drift apart inside
  it, split along the reason to change, not along size alone.
- **Open/closed.** Where variants are expected to keep arriving (strategies,
  operators, backends, job types), add new behavior by registering it (a table
  entry, a registry, a per-variant descriptor) rather than editing shared
  `if/elif` chains. Prefer one descriptor per variant over several parallel
  tables that must be kept in sync by hand; if parallel tables exist, add a test
  that fails when they disagree. A rule that every variant or call site must
  follow needs one choke point and a guard test; see
  [cross-cutting concerns](cross-cutting-concerns.md).
- **Liskov substitution.** An implementation behind an interface must honor the
  whole contract, including errors and edge cases, or it is not substitutable.
  Parity oracles and fallbacks are the usual place this matters.
- **Interface segregation.** Give callers the narrow interface they use. A
  function that takes a whole context to read one field hides its real
  dependencies.
- **Dependency inversion.** High-level policy should depend on an abstraction,
  not on a concrete low-level module, when the low-level side is volatile or has
  more than one implementation (for example compiled vs reference kernels).

- **Deep modules and information hiding** (Ousterhout, *A Philosophy of
  Software Design*). Prefer modules whose interface is small relative to what
  they hide. A change that leaks an implementation decision into several
  callers (the same fact restated in many places, a parameter threaded through
  layers that do not use it) is the usual sign of a shallow boundary. Often a
  better lens than SOLID for data-pipeline code.

Cost check before applying any of them: an abstraction with one implementation
and no expected second one is usually premature. Record the change reason that
justifies it.

## Red flags

- "We might need it later." Identify a present change reason or defer it.
- "It appears three times." Identify the shared invariant and likely divergence.
- "Adding one variant means editing N files." An open/closed or single-
  responsibility problem; consider a registry or descriptor.
- "Each site checks that itself." A scattered rule; name the choke point and
  the guard test per [cross-cutting concerns](cross-cutting-concerns.md).
- "Applying SOLID" with no named change pain. A principle is a means, not a goal.

## Done when

- [ ] Boundaries and reasons to change are clear.
- [ ] Complexity is justified by current evidence.
- [ ] Significant trade-offs are recorded.
- [ ] Any design principle applied names the concrete change pain it removes.
