# Workspace and VCS

> Guidance for working trees, coherent diffs, generated artifacts, branches,
> checkpoints, and version-control side effects.

## When this applies

Use for any repository edit or version-control operation.

## Scope

**Owns:** dirty-tree inspection, preservation of user edits, coherent diffs,
generated files, branches and worktrees, checkpoint labels, commit message form,
and commit or push authorization. **Does not own:** destructive target handling or release approval.

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

## Commit messages

Standard: [Conventional Commits 1.0](https://www.conventionalcommits.org/) for the
subject, with the git project's and Chris Beams's seven rules for form. A commit
should read like a teammate wrote it in under a minute.

DO write the subject as `type(scope): summary`. Types: `feat`, `fix`, `docs`,
`refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. Scope is the area
(`jobs`, `subset`, `cli`, `roadmap`), optional, and may carry a slice id
(`B-10 S0`) when the repo tracks slices.

DO keep the subject to 72 characters or fewer, imperative mood ("add", "fix",
"bound"), lowercase after the colon, no trailing period, and plain words for what
changed. A squash merge uses the PR title, which follows the same form, plus the
`(#N)` suffix.

DO leave the body out when the subject says it all. Most commits need none.

DO use a body only for why, a constraint, or a side effect the diff cannot show:
one to three short sentences, or at most five plain bullets, wrapped at 72.

DO mark a breaking change with `!` after the scope and a `BREAKING CHANGE:` line.

DO end every commit and PR body with the owner's trailer line the project
requires (here: `cam`), and nothing else after it.

DON'T put in a body: a recap of the rules followed, test counts, file lists,
gate or review history, a "Summary/Changes/Testing" template, bold, headings,
emoji, or phrases like "this commit" and "this change ensures". That material
belongs in the PR description, the build record, or the gate ledger.

DON'T add `Co-Authored-By`, `Claude-Session`, or "Generated with" lines unless
the project asks for them.

DON'T bundle unrelated changes to keep the subject short. Split the commit.

DO copy these commit rules, and the test scope rules in
[testing](testing.md#test-depth), into every builder or delegate brief that may
commit. See [delegation](delegation.md).

Good:

```text
fix(jobs): persist the vault before any target publishes

A crash between publish and persist left outputs with no vault to
re-identify them from.

cam
```

```text
chore(deps): repin the engine dependency to 0.18.0

cam
```

Bad: a 25-line body that lists every touched file, restates the plan, counts
tests, and ends with a tool-attribution banner.

## Red flags

- "The dirty diff is probably mine." Treat it as user-owned until confirmed.
- "One commit means one concern." Keep a coherent reviewable change, not an
  artificial split that omits required support work.

## Done when

- [ ] User-owned work is preserved or its approved resolution is recorded.
- [ ] The diff is coherent, scoped, and honestly labeled.
- [ ] No unauthorized version-control side effect occurred.
