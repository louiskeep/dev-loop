# Verification

> Evidence rules for success claims, checkpoints, and merge-ready revisions.

## When this applies

Use before claiming a result, making a checkpoint, proposing a merge-ready
revision, or passing evidence to a reviewer.

## Rules

DO run the actual applicable command, test, inspection, or observable check and
read its output.

DO report the command, result, coverage, failures, skips, and known limitations.

DO distinguish a WIP checkpoint from a merge-ready revision. A WIP checkpoint
needs a working or recoverable state and an honest label; a merge-ready revision
needs the full evidence required by its risk level and repository policy.

DO use accepted compensating evidence when a direct proof is unsafe,
intermittent, unavailable, or disproportionate, and record the gap.

DON'T claim that a green acceptance check proves properties it did not test.

DON'T present a checkpoint as merge-ready without the required evidence.

## Evidence record

State the exact artifact, checks run, result, covered behavior, unchecked scope,
limitations, and any approved exception or compensating evidence.

## Red flags

- "The summary is green." Read the underlying output.
- "It is committed, so it is verified." Checkpoint state is not merge evidence.

## Done when

- [ ] Every success claim has observed evidence.
- [ ] WIP and merge-ready states are labeled correctly.
- [ ] Coverage gaps and limitations are explicit.
