# Scope Discipline

> Guidance for keeping work traceable to the requested outcome.

## When this applies

Use throughout every task, especially when adjacent issues or cleanup appear.

## Rules

DO trace every diff hunk to the request, required support work, or a documented
exception.

DO include necessary tests, docs, migrations, and recovery work even when the
request did not name each artifact literally.

DO surface adjacent tangents separately when they do not support the requested
outcome.

DO seek direction for a material preference, irreversible choice, or ambiguity
whose wrong answer is expensive.

DON'T use literal one-to-one request wording to exclude necessary support work.

DON'T hide unrelated cleanup inside a valid change.

## Red flags

- "The request did not mention the test." Include necessary verification.
- "This cleanup is nearby." Document its necessity or separate it.

## Done when

- [ ] Every hunk has a traceable purpose.
- [ ] Necessary support work is included and tangents are separated.
