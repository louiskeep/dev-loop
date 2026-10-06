# dev-rules Architecture

## Purpose

This repository is a stack-agnostic policy layer for development work. Its
documents route work, assign each policy domain a canonical owner, and make
consistency checks executable without imposing a local tool or model stack.

## Layers

1. `00-universal.md` establishes precedence and immutable safety invariants.
2. `risk-and-exceptions.md` classifies work R0 through R3 and selects gates.
3. `development-loop.md` sequences FRAME through DOCUMENT and separates review
   from the evidence-consuming gate.
4. Root rulebooks own task-specific and operational policy domains.
5. Catalogs provide optional language or pattern detail; profiles map durable
   roles to local implementations.

The README is the entry point and routes every active root rulebook. It is an
index, not a second source of normative policy.

## Ownership

`rules-manifest.json` records one active-document entry with an ID, path, kind,
status, and unique policy-domain owner list. A policy domain has one canonical
owner. Other documents link to that owner rather than defining competing rules.

## Enforcement

`scripts/check_rules.py` uses only the standard library. It validates manifest
structure, paths, unique IDs and owners, local Markdown links, README routing,
active-document discovery, historical-document placement, placeholders, and
root-policy leakage. Its artifact hash covers only existing, non-ignored
working-tree files; tracked files absent from the tree are reported as
omissions rather than read.

`tests/test_check_rules.py` uses isolated fixtures to prove that the checker
rejects representative broken states. The checker is structural enforcement,
not proof that prose is wise or a release is authorized.

## Lifecycle

Active durable documents live at the repository root, `profiles/`, `catalogs/`,
or directly under `docs/`. Plans and specifications are ephemeral. Before a
completed plan moves to `docs/archive/`, promote its enduring decisions into
the durable rulebooks or this architecture document and update indexes. Active
`docs/plans/` and `docs/specs/` must not retain historical completed documents.

## Change process

Changes to this policy are R2 because many repositories consume it. Use the
written-plan, independent-review, verification, exact-artifact-gate sequence.
Do not mutate downstream repositories, indexes, or external systems merely to
update these rulebooks.
