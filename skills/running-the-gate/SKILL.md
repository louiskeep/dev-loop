---
name: running-the-gate
description: >-
  Use when non-trivial work is complete and you are about to review, gate, or merge it, or when a
  reviewer returns findings you must remediate and re-gate. Also use whenever you feel pressure to
  merge on a review that predates the current commit or to mark a finding resolved without re-review.
---

# Running the gate

## Overview

The gate is an independent read of the exact commit, not a rerun of your own confidence. Delegate it;
do not self-review. Roles and tiers: `role-matrix.md`. Policy: `rules/code-review.md`,
`rules/development-loop.md`.

## Sequence

1. **REVIEW.** Dispatch dennis (fresh-context adversarial reviewer, Opus) on the exact artifact: the
   diff or commit, its plan or spec, and the VERIFY evidence.
2. **GATE.** For R2/R3, add the Codex cross-model gate on the same commit. Codex always; if it is
   unavailable, use the highest available Claude model, and never skip the gate. Cross-model review
   catches same-model blind spots.
3. **Merge only on a clean verdict for the exact commit.** Merge, push, and PR stay confirm-first.

## What the gate prompt must demand

Hand the reviewer, and require back:

- The exact artifact identifier (commit or diff), its goal, its plan, and the VERIFY evidence.
- Findings by severity (dennis: P0 blocks merge, P1 fix before merge, P2 advisory), each with the
  location, why it is wrong, the failure it causes, and a **root-cause remediation direction, not a
  symptom patch**. The reviewer identifies the fix; it does not write it.
- The verdict's checked *and* unchecked scope. "Looks fine" is not a verdict.
- **Every finding in one pass.** Tell the reviewer to list all findings it can find, not stop at the
  first blocker, and allow small read-only probes (a scratch dir, bounded memory) so runtime behavior
  gets checked, not only the text.
- **Risk-proportional severity.** A finding blocks only if it is plausible in real use of the system as
  deployed. Astronomically unlikely events go in the project's known-issues (tech debt) list as low,
  fixed only if they recur in production; they are neither blockers nor roadmap work. For
  anything rated high or above, the reviewer states how the failure would occur in real use.
- **A design check only where it applies.** When the change adds a variant, a module, or an
  abstraction, ask the reviewer to check it against the design principles in
  `rules/architecture.md`, and to raise a finding only for a concrete change pain it names (for
  example "adding one operator now edits five files"), never for not matching a pattern.
- **Judgement against the plan's pinned guarantee.** Findings outside the plan's stated guarantee and
  non-goals go in a separate out-of-scope list and become follow-up items, not blockers.

Ask for per-finding root-cause remediation explicitly. A gate that only lists problems leaves the fix
to guesswork.

## Handling findings (REMEDIATE)

- Fix at the source, not the symptom. Trace the cause before changing anything.
- Any artifact change re-enters SELF-CHECK and VERIFY, then re-review. The prior gate is **stale on
  the new commit**; re-gate on the fixed commit.
- Do not weaken a pre-agreed acceptance criterion to make the gate pass. Change it only through a
  recorded decision.
- Patch what was found. Do not redesign during remediation; a redesign is a new plan and needs human
  direction.
- Every re-gate prompt names the fix and asks the reviewer to check that fix for new breakage, not
  only to re-check the earlier findings.
- Count review rounds per plan or artifact. After three, stop for human direction: accept with
  documented gaps, narrow the scope, or continue.
- Ladder: Opus builds and fixes, Codex is the final gate, park for human direction after repeated
  failure. Do not convert retries into confidence.

## Red flags

- Merging on a gate that predates the current commit.
- A gate prompt that says "find problems" without demanding per-finding root-cause remediation.
- Marking a finding resolved without a re-review on the fixed commit.
- A fourth review round on the same slice without a human decision.
- Changing the design in response to findings instead of patching them.
- Self-reviewing in the main session instead of delegating to a fresh context.
