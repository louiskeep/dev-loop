# Workspace and VCS

> Guidance for working trees, coherent diffs, generated artifacts, branches,
> checkpoints, and version-control side effects.

## When this applies

Use for any repository edit or version-control operation.

## Scope

**Owns:** dirty-tree inspection, preservation of user edits, coherent diffs,
generated files, branches and worktrees, checkpoint labels, and commit or push
authorization. **Does not own:** destructive target handling or release approval.

## Rules

DO inspect the working tree before editing and identify pre-existing changes as
user-owned unless their owner says otherwise.

DO preserve user changes, integrate their stated intent when scope requires it,
and call out unavoidable conflicts before overwriting anything.

DO keep each change coherent and reviewable. A coherent change may include
required tests, documentation, migration, or generated output.

DO label WIP checkpoints honestly and keep them working or recoverable. See
[verification](verification.md).

DO commit, amend, push, create branches, switch worktrees, or alter remote state
only when authorized.

DO verify generated files from their source and repository conventions before
including them.

DON'T present a mixed WIP checkpoint as merge-ready.

DON'T discard, reset, or overwrite user work without explicit authorization and
exact target confirmation.

## Red flags

- "The dirty diff is probably mine." Treat it as user-owned until confirmed.
- "One commit means one concern." Keep a coherent reviewable change, not an
  artificial split that omits required support work.

## Done when

- [ ] User-owned work is preserved or its approved resolution is recorded.
- [ ] The diff is coherent, scoped, and honestly labeled.
- [ ] No unauthorized version-control side effect occurred.
