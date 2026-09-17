# Data and Migrations

> Guidance for schema evolution, migration execution, and data recovery.

## When this applies

Use for schema changes, backfills, transformations, and data operations. Also
read [destructive operations](destructive-operations.md) for irreversible work.

## Rules

DO inventory affected data, readers, writers, consumers, compatibility windows,
and operational constraints before changing a schema.

DO prefer expand, migrate, verify, and contract when live compatibility is
needed.

DO make operations idempotent where practical; batch bounded work; support
pause, resume, progress observation, and reconciliation.

DO test representative migration and recovery paths, including interactions
with startup or self-healing behavior. See the
[schema-migration testing catalog](catalogs/schema-migration-testing-patterns.md).

DO plan locks, load, ordering, and failure handling before execution.

DO distinguish code rollback from data recovery; record which is possible.

DON'T drop, overwrite, or transform data without exact-target and recovery
evidence.

## Red flags

- "The schema change is reversible." Confirm the data state can actually be
  recovered.
- "The backfill can run all at once." Bound it and provide observation and stop
  controls.

## Done when

- [ ] Compatibility, migration, and recovery paths are verified.
- [ ] Batching, reconciliation, and operational limits are executable.
- [ ] Destructive authorization is obtained where required.
