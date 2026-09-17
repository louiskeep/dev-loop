# API and Compatibility

> Guidance for interfaces consumed outside the changed unit.

## When this applies

Use for public APIs, CLI flags, file formats, events, schemas, or any behavior
with external consumers.

## Scope

**Owns:** interface inventory, consumer impact, compatibility windows,
versioning, deprecation, and contract tests. **Does not own:** deployment
mechanics or internal module design.

## Rules

DO inventory producers, consumers, versions, contract assumptions, and failure
behavior before changing an interface.

DO preserve compatibility through a documented window or make the breaking
change explicit, approved, and coordinated.

DO define versioning, deprecation, defaults, validation, and error behavior in
terms consumers can verify.

DO use contract tests or equivalent consumer-facing evidence for affected paths.

DO plan migration and rollback behavior for persisted or asynchronous contracts.

DON'T change externally consumed behavior based only on internal tests.

DON'T remove a compatibility path before its documented window and consumer
migration evidence are complete.

## Red flags

- "No internal caller breaks." Identify consumers outside the changed unit.
- "A new default is compatible." Check omitted fields, older clients, and error
  handling.

## Done when

- [ ] Affected consumers and contracts are identified.
- [ ] Compatibility or coordinated breaking behavior is verified.
- [ ] Consumer-facing evidence covers key success and failure paths.
