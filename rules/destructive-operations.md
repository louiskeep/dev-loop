# Destructive Operations

> Guidance for deletion, overwrite, force operations, irreversible transforms,
> and material production mutation.

## When this applies

Use for delete, drop, truncate, overwrite, force-push, irreversible transform,
or material production mutation.

## Scope

**Owns:** exact target resolution, read-only preview, backup and recovery
evidence, human authorization, bounded execution, and reconciliation. **Does
not own:** schema design or routine reversible deployment.

## Rules

DO resolve the exact target, scope, identities, and expected counts with
read-only inspection before execution.

DO prove backup, recovery, rollback, or compensating restoration paths before
an irreversible action.

DO obtain explicit human authorization immediately before the R3 side effect.

DO bound execution by target, batch, time, and stop condition; observe results
and reconcile actual outcomes to the approved expectation.

DO preserve an auditable record of authorization, target evidence, command or
operation identity, outcomes, and recovery evidence.

DON'T use a broad path, unresolved variable, wildcard, or inferred target for a
destructive operation.

DON'T proceed because a preview was once correct or an earlier authorization is
stale.

## Red flags

- "The target is obvious." Resolve it read-only and record the result.
- "We have a backup somewhere." Prove recovery for this target and operation.

## Done when

- [ ] Authorized target and recovery path are proven before action.
- [ ] Execution is bounded and observed.
- [ ] Results are reconciled and any recovery action is recorded.
