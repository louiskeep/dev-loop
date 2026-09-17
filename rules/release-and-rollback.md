# Release and Rollback

> Guidance for sequencing a release, observing it, and recovering safely.

## When this applies

Use when a change will be deployed or released. Pair it with
[destructive operations](destructive-operations.md) when a release step is
destructive or irreversible.

## Scope

**Owns:** release sequencing, feature flags, canaries, rollback or roll-forward,
post-release checks, and release evidence. **Does not own:** destructive
authorization or observability implementation details.

## Rules

DO define the release unit, preconditions, release sequence, health checks,
owner, and decision points before execution.

DO choose a bounded exposure strategy appropriate to risk, such as staged
release, feature flag, or canary.

DO make rollback or roll-forward steps executable and verify their assumptions
before the release when feasible.

DO define post-release signals, observation duration, and abort criteria.

DO retain exact release evidence and the artifact identity used in the decision.

DON'T call a release reversible when dependent data, contracts, or side effects
cannot be safely restored.

DON'T substitute a monitoring dashboard for a defined recovery path.

## Red flags

- "We can roll back the binary." Check data, contracts, and external effects.
- "No alarms fired." Confirm the intended health signals and observation window.

## Done when

- [ ] Release, recovery, and health-check paths are executable.
- [ ] Exposure, stop criteria, and evidence are recorded.
- [ ] Post-release verification covers the intended behavior.
