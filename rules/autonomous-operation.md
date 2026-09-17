# Autonomous Operation

> Overlay for loops, scheduled runs, and long unattended work. It tightens the
> [development loop](development-loop.md); it never relaxes risk or authority.

## When this applies

Use whenever a human is not approving each action. Read it before continuing an
unattended sequence, including after a context restart.

## Rules

DO record scope, risk, stop conditions, evidence, decisions, and recovery state
outside transient context.

DO keep a working, recoverable checkpoint before each significant next step.

DO require independent review for all work whose risk policy requires it.

DO stop for human direction when requirements are materially ambiguous, scope
expands, the same approach repeatedly fails, or the next action needs new
authority.

DO stop immediately before any R3 side effect until explicit human authorization
is available.

DON'T convert elapsed time, repeated retries, or tool availability into evidence
or authority.

DON'T mutate external state merely because an unattended task asked to finish.

## Red flags

- "One more attempt will probably fix it." Reassess the model and stop after
  repeated failure.
- "No one is watching, so it is safe to continue." Tighten the gate instead.

## Done when

- [ ] State and evidence permit a safe handoff or continuation.
- [ ] No stop condition was crossed without human direction.
- [ ] The working state is recoverable and limitations are recorded.
