# Documentation

> Guidance for docs, comments, decisions, and their lifecycle.

## When this applies

Use when creating or changing durable references, comments, decisions, plans,
or project-required release records.

## Rules

DO update the durable source that describes changed behavior, interface,
decision, or operational procedure.

DO comment non-obvious reasons, constraints, and invariants rather than restate
code.

DO record material decisions with alternatives and consequences.

DO maintain one authoritative current-work index only when the project maintains
planning documentation; follow that project's documentation architecture.

DO promote durable knowledge before archiving a completed plan, design, or
review artifact. Update indexes when locations or status change.

DO update a changelog or knowledge system only when it is project-required or
has demonstrated cross-session value and is authorized.

DON'T prescribe a universal roadmap taxonomy.

DON'T leave completed historical plans or specs in active planning directories.

## Red flags

- "Every project needs this roadmap layout." Use the repository's index and
  lifecycle instead.
- "The plan is the current reference." Promote the enduring information first.

## Done when

- [ ] Current durable sources match the changed system.
- [ ] Required indexes and records are current.
- [ ] Completed ephemeral documents are promoted and archived.
