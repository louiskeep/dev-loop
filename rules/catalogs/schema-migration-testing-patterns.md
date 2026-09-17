# Schema-Migration Testing Patterns

> Optional catalog for migration and startup interactions. General policy remains
> in [data and migrations](../data-and-migrations.md).

## Startup schema repair must not reverse a migration

**Risk:** startup code that recreates missing schema objects can silently undo a
completed migration and leave incompatible data or unused columns.

**Test shape:** migrate representative data to the target version, run the real
startup or schema-repair path against that same data, then assert that removed
objects remain absent and expected readers and writers still work.

**Evidence:** a migration-only test is insufficient when startup code can alter
the schema. The combined test should fail on a repair path that recreates the
retired object.

## Adding an entry

Record reusable migration-test shapes, not local incidents. Include the trap,
test sequence, and what evidence proves the interaction is safe.
