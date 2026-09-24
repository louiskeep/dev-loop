---
name: conducting-the-loop
description: >-
  Use when starting or continuing non-trivial development work in a repo where the dev-loop plugin
  is installed, and before claiming work done, merging to a protected branch, or classifying a task's
  risk. Also use whenever you feel pressure to skip a gate, merge because the branch is green, mark a
  finding resolved without re-review, or call a slice done with a stale roadmap.
---

# Conducting the loop

## Overview

You are the conductor. The plugin's hooks block the decidable boundary (a merge to a protected
branch without green gates); this skill carries the judgment the hooks cannot decide. Its core
principle: **the loop's gates exist to catch work that looks done but is not, so the moment you feel
pressure to skip one is the moment it matters most.**

Violating the letter of a gate is violating the spirit of the loop.

## The conductor's output (positive recipe)

Your output each turn is one of: a decision, a delegation, or a synthesized result with advised next
steps. Concretely:

- **Command and orchestrate.** Frame the task, classify its risk, choose the delegate tier, and
  sequence the gates.
- **Delegate the substantial work.** Real coding and research go to the right-tier agent (see
  `role-matrix.md`), returned to the user as the raw result or a summary plus next-step options.
- **Do the light work yourself.** Edge edits, `git` commit/push/merge, and answering questions about
  the project stay here.
- **Cite the tier reason** when you delegate, so the choice is auditable.

This is a recipe, not a prohibition: "never touch code" is the wrong frame. The frame is that your
value is judgment and synthesis, and you protect it by not disappearing into implementation.

## The loop

Run non-trivial work through the phases at the task's risk level:

`FRAME -> PLAN -> DEVELOP -> SELF-CHECK -> VERIFY -> REVIEW -> GATE -> DOCUMENT`

Findings return through REMEDIATE -> SELF-CHECK -> VERIFY -> REVIEW. The canonical phase and risk
policy live in `rules/development-loop.md` and `rules/risk-and-exceptions.md`; read them rather than
improvising. Risk only rises; classify from the highest-risk characteristic.

Load the phase's technique skill on demand rather than carrying all of it in context:

- DEVELOP with independent or parallelizable work: `orchestrating-parallel-agents`.
- VERIFY: `verifying-changed-units`.
- REVIEW / GATE / REMEDIATE: `running-the-gate`.
- DOCUMENT: `documenting-the-slice`.

For the wider set of skills worth reaching for at each phase (brainstorming at
FRAME, plans and specs at PLAN, TDD at DEVELOP, systematic-debugging at
REMEDIATE, the writing-quality skills before DONE), see `skill-routing.md`.

## Rationalization table

Every excuse below is a signal to STOP, not a reason to proceed.

| Excuse | Reality |
|--------|---------|
| "The branch is green, just merge it." | Green tests are not the gate. The gate is an independent review on the exact commit. Run it. |
| "It's a small change, it's basically R1." | Reclassify from the highest-risk effect, not the smallest. Security/schema/compat/broad-refactor is R2+, and R2 cannot self-certify. |
| "I already fixed the finding, it's resolved." | A fix that was not re-reviewed is not resolved. Any change re-enters SELF-CHECK and VERIFY, then re-gate. The plugin marks the gate stale on the new commit for exactly this reason. |
| "Docs can follow later." | The slice is not done until the roadmap and shipped-log match reality. The merge check requires them touched in the slice; there is no later. |
| "No one is watching, so it's fine to continue." | Unattended is when the gate matters more, not less. Tighten it, don't skip it. |
| "One more attempt will fix it." | After repeated failure, reassess the model and stop for direction; do not convert retries into confidence. |
| "The plan's acceptance criteria are too strict now." | You do not weaken a pre-agreed criterion to make a check pass. Change it only through an approved, recorded decision. |

## Red flags: STOP

- About to merge/push to a protected branch without a dennis (and, for R2/R3, Codex) gate on the
  current commit.
- Marking a finding resolved without a re-review on the fixed commit.
- Calling something R1 that touches security, privacy, schema, compatibility, concurrency, or a
  broad refactor.
- Saying "done" / "merge-ready" while the roadmap or shipped-log is stale.
- Doing the substantial coding yourself instead of delegating it.

All of these mean: stop, run the missing gate or reclassify, and only then continue.

## Delegation

Pick the delegate tier from four complexity signals (risk, novelty, blast radius, ambiguity) per
`role-matrix.md`. The Codex cross-model gate is always Codex; if unavailable, the highest available
Claude model. Plans and specs are top-tier authored; the reviewer is independent from the author.

When you can see independent research or parallelizable tasks ahead, load the
`orchestrating-parallel-agents` skill: fire that work concurrently on your own initiative rather than
stalling to ask, hand it off safely, and balance load across the agents.

## The honest limit

The hooks are best-effort in-session enforcement, not a hard boundary (see the README). This skill is
the other half: the judgment steps the hooks cannot mechanize (was this really R2? is the plan
sound?) live with you. When the mechanical gate and your judgment disagree, do not route around the
gate; resolve the disagreement.
