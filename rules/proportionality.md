# Proportionality

> Guidance for keeping a change as small as the need it serves.

## When this applies

Use on every change that adds code, tests, or docs, and at review. It matters
most for generated code: typing is free, so nothing but this check stops a
guard, a layer, or an option from being added "to be safe".

This is not minimalism. Checks at a trust boundary, security and privacy
controls, and the acceptance tests a plan names are needs, not extras. See
[security](security.md) and [testing](testing.md).

## Rules

DO name the present consumer of every new parameter, option, class, module, and
branch. A test is not a consumer: code with no production caller does not
merge, however well it is tested. One exception: a slice of an approved plan
may merge before its consumer when the plan names the later slice that calls
it, and that slice lands before the row closes. The plan's Placement table
records the pair, and review checks it.

DO handle an error only where you can name the failure that reaches that line
and what the handler does that letting it propagate would not. Validate once,
at the trust boundary; inside it, rely on the types and invariants. A branch
you would mark unreachable is an assertion or a deletion, not a handler.

DO write the direct version first. Add structure when the direct version shows
a concrete change pain. See [architecture](architecture.md).

DO state the expected size in the plan: files touched and new public names. A
build that grows well past it stops and explains before it continues.

DO apply the deletion test before review. For each added piece, ask whether
removing it fails an acceptance criterion or breaks a present caller. If not,
remove it.

DON'T add a fallback, default, or broad catch that turns a failure into a quiet
wrong answer.

DON'T keep an old path, alias, or shim without a named consumer. That includes
a re-export left behind so old imports keep working after a move.

DON'T probe for a version or capability that the dependency pin already
guarantees.

DON'T loosen production code to suit a test double. Fix the double.

DON'T add a wrapper that only forwards, a class with one method and no state,
or an interface with one implementation and no second one planned.

DON'T add a test that cannot fail for a reason a user would care about: one
that asserts on its own mocks, restates the implementation, or repeats another
test's case.

DON'T pin an incidental fact in a guard test, and keep each pinned fact in one
place. Derive a list that can be derived. A pin that ordinary changes must
hand-edit teaches people to edit pins.

DON'T write comments or docs that restate the code or narrate the change: no
task or ticket id, review round, date, or reviewer name. That history belongs
to version control.

## Enforcement

A prose rule here that keeps being broken needs a mechanical check, not a
restatement. Comment history, re-export shims, and code with no production
caller can each be found by a scanner. See
[cross-cutting concerns](cross-cutting-concerns.md).

## Red flags

- "Just in case." Name the case and how it reaches this line.
- "For robustness" or "defensive." Name the failure and who sees it.
- "For completeness." Name the consumer.
- "More flexible." Name the second use that exists today.
- "The diff is three times the plan." Stop and explain, or cut.
- "Review asked for one more guard." Ask which failure it covers.
- "Kept for a future caller" or "kept for tests." Delete it; version control
  keeps it.
- "Split out to stay under the size cap." Split along a reason to change, or
  see [architecture](architecture.md#layout-where-things-go).

## Done when

- [ ] Every added element has a named present consumer.
- [ ] Every handler names the failure it handles.
- [ ] The deletion test was applied and its removals made.
- [ ] The change is near the size the plan stated, or the difference is explained.
- [ ] No comment or docstring carries task, review, or date history.
