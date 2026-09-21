---
name: verifying-changed-units
description: >-
  Use when you have changed source and are about to claim VERIFY complete, hand a change to review,
  or call it merge-ready, and need to measure whether the tests actually exercise the change rather
  than trusting a green suite or a whole-tree coverage number.
---

# Verifying changed units

## Overview

A green suite is necessary, not proof. VERIFY measures test strength on the units your diff changed,
not test counts and not the whole tree. Grade the diff so the measurement stays affordable at merge
cadence. Policy: `rules/testing.md`, `rules/verification.md`.

## The measurement

1. **Scope to the diff.** `git diff --name-only <base>` gives the changed source units. Measure those,
   not the tree.
2. **Coverage on the changed units.** Run the suite under coverage and read line *and* branch coverage
   for the changed functions. An uncovered new branch is untested behavior, not "probably fine."
3. **Mutation on the costly units.** Run mutation testing on the units where a silent defect is most
   expensive (crypto, referential integrity, money, auth). A surviving mutant there is build work, not
   a number to log and move past.
4. **Mutate to confirm.** Break the changed logic by hand and confirm a test goes red. A test that
   passes with the code broken guards nothing.

## Bars

Set bars from a measured baseline, not a chosen number. 100% on security and correctness primitives
(crypto, RI); elsewhere, do not regress the unit's baseline. Name honestly what each unit got: an
enumerated table of cases is not property testing.

## Tooling gotcha

mutmut (3.7) skips decorated-class bodies, and its trampoline breaks code that inspects the call frame
or signature. Put the logic that must be graded in free functions so it can actually be mutated.

## Record

State the base commit, the changed units, per-unit line and branch coverage, the mutation score on
the graded units, surviving mutants and their disposition, and what was not measured. This is the
evidence REVIEW and GATE consume.

## Red flags

- "The suite is green, so it's done." Green is not strength. Measure the diff.
- "Overall coverage is 90%." Overall hides the changed unit. Scope to the diff.
- "Mutation is too slow." Grade only the costly changed units, not the tree.
