# Delegation

> Guidance for using a capability, independent contributor, or review handoff.
> It defines roles, not local tool or model implementations.

## When this applies

Use when work can be isolated, needs fresh-context judgment, or an existing
capability can safely replace custom effort.

## Rules

DO choose the lightest capable mechanism after checking available capabilities.

DO give a delegate the goal, scope, constraints, success criteria, relevant
pointers, and required return shape.

DO bound a builder's brief explicitly: name what it may change, the acceptance
gates it must satisfy, and the named gates or assertions it may NOT weaken,
narrow, skip, or delete. An unbounded builder under a green-the-suite incentive
will loosen the gate that stands between it and "done".

DO bound a builder's EXECUTION, not just its edits. A builder runs ONLY the
named tests for its task, never the whole suite (the orchestrator or CI owns the
full-suite run). A builder NEVER launches a background or detached run: a
long run started inside a subagent detaches from it, so the agent's later
resumes cannot observe completion and it loops indefinitely burning budget. If a
check needs longer than a single foreground command, the builder reports that
and hands it back. Give the builder a tool-call or time budget and require it to
stop and report on reaching it rather than grinding. A scoped port of a proven
prototype should cost minutes, not a full-suite marathon.

DO assign roles by capability: builder, planner, reviewer, documenter, or
operator. Keep the roles independent when the risk policy requires it.

DO independently verify material delegated findings before acting on them.

DO treat a divergence from a reference or oracle as build work to fix at the
source. A builder that cannot reach parity reports the divergence; it does not
route around it, and the orchestrator does not relay a route-around as a fix.

DO preserve evidence and decision context at handoff boundaries.

DON'T delegate authority the requester or operator does not have.

DON'T treat a plausible delegated conclusion as verified evidence.

DON'T accept a delegate's "benign", "expected", or "consistent with the existing
path" justification for a divergence. Reproduce it against the exact gate and
artifact the merge enforces before relaying it; a divergence is presumed real
until proven equal at that gate.

DON'T require delegation when a proportional inline pass is safer and clearer.

## Red flags

- "The delegate said so." Verify the source or evidence.
- "More delegates means more confidence." Match independence and review depth
  to risk.
- "The difference is benign / matches the existing path." Prove it at the merge
  gate, not in prose.
- A build's diff loosens a gate (a dropped equality or metadata flag, a
  values-only comparison, a removed or skipped case, a widened tolerance) next to
  a "now green" claim. The green is suspect; re-run the original gate.

## Done when

- [ ] The mechanism and roles fit the task risk.
- [ ] The handoff includes sufficient scoped context.
- [ ] Material findings were verified before use.
