# Exploration

> Guidance for forming a source-backed model before changing an unfamiliar area.

## When this applies

Use before editing unfamiliar source or making a broad change across a codebase.

## Rules

DO locate relevant files, callers, consumers, tests, and durable architecture
references before editing.

DO inspect surrounding source and existing patterns until the intended change and
its dependencies are understood.

DO use focused search or an appropriate structural map for broad reachability
questions.

DO inspect the diff and relevant resulting context during self-check; broaden
inspection for generated or formatter rewrites when needed.

DON'T reread an entire edited file merely to confirm that an edit command ran.

DON'T guess at contracts, signatures, or consumer behavior.

## Red flags

- "I changed the line, so the file is verified." Inspect the diff and its
  context.
- "The interface probably works this way." Trace it to source or a contract.

## Done when

- [ ] The changed unit, dependencies, consumers, and relevant evidence are
  understood.
- [ ] Diff-based inspection covers the resulting change.
