# Risk and Exceptions

> Canonical policy for risk classification, required gates, escalation, and
> exceptions. It does not grant authorization or define destructive mechanics.

## When this applies

Classify every non-trivial task during framing. Use this rulebook whenever risk
is uncertain, then raise the level for the highest-risk characteristic.

## Scope

**Owns:** risk classification, gates, phase collapse, escalation, and
exceptions. **Does not own:** destructive-operation mechanics or the identity
of a project authorizer.

## Risk levels

| Level | Trigger and examples | Required gates | Collapse |
|---|---|---|---|
| R0 | Trivial, reversible, no behavior or contract effect | Inline frame and approach, diff self-check, targeted verification | Plan on disk and independent review may collapse unless repository policy requires them. |
| R1 | Bounded, reversible work with limited blast radius | Recorded approach, self-check, targeted and project verification, independent review | Separate plan review and final cross-model gate may collapse. |
| R2 | Security or privacy logic, schema evolution, compatibility, architecture, concurrency, broad refactor, or controlled reversible release | Written reviewed plan, self-check, full relevant verification, adversarial independent review, exact-artifact gate | No author self-certification. |
| R3 | Destructive or irreversible operation, novel production mutation, credential rotation, force operation, material external cost, or consequential public claim | All R2 gates, tested recovery or rollback, and explicit human authorization immediately before the side effect | Authorization, recovery, verification, and independent review cannot collapse. |

Risk only rises. A reversible deployment through an established, observable,
pre-authorized path is R2; a novel, destructive, irreversible, or materially
costly production action is R3.

## Rules

DO record the risk level, required evidence, and approver before development
begins.

DO use the [development loop](development-loop.md) gates required by the level.

DO treat ambiguity, missing recovery evidence, and uncontrolled blast radius as
reasons to raise risk or pause for direction.

DO record an exception with the policy-domain ID, scope, risk level, rationale,
compensating controls, residual risk, approver, and expiry or follow-up.

DO require an independent approver for an exceptionable R0 or R1 process
default, an independent human owner for an R2 process deviation, and an
independent human owner for every exceptionable R3 process deviation.

DON'T average risks down across characteristics.

DON'T use an exception to grant authority that the actor does not already have.

DON'T waive immutable invariants, required R3 authorization, recovery,
verification, or independent review. These requirements remain non-waivable
even when an R3 process deviation has an independent human owner.

## Red flags

- "It is only a small change." Reclassify from its highest-risk effect.
- "We can document the exception afterward." Record it before deviating.
- "The tool allows it." Tool capability is not authorization.

## Done when

- [ ] Risk, evidence requirements, and approver are recorded.
- [ ] Required gates have evidence for the exact artifact.
- [ ] Any exception has a bounded, approved record and compensating evidence.
