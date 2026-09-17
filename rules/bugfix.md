# Bugfix

> Guidance for diagnosing and correcting unexpected behavior.

## When this applies

Use when observed behavior differs from the intended behavior.

## Rules

DO establish the expected behavior and gather the best available failure
evidence before changing the system.

DO prefer a fail-before reproduction or regression test, then prove the fix
changes that evidence for the right reason.

DO record why direct fail-before proof is unsafe, intermittent, unavailable, or
disproportionate, plus compensating validation.

DO find and correct the root cause rather than masking a symptom.

DO label an emergency mitigation as temporary and create follow-up work for the
root-cause repair.

DON'T guess through unrelated edits or suppress an error and call it fixed.

## Red flags

- "The failure cannot be reproduced, so no evidence is needed." Record the
  strongest evidence and compensating validation.
- "Increasing a timeout fixes it." Establish the failure mechanism first.

## Done when

- [ ] Expected behavior, root cause, and evidence are recorded.
- [ ] Fail-before proof or compensating evidence supports the correction.
- [ ] Temporary mitigation, if any, is labeled and followed up.
