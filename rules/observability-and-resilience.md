# Observability and Resilience

> Guidance for diagnosable, bounded failure behavior at operational boundaries.

## When this applies

Use for network or process boundaries, background work, concurrency, partial
failure, or operationally significant failure paths.

## Scope

**Owns:** failure modes, safe structured telemetry, service signals, retries,
timeouts, idempotency, overload, partial failure, and concurrency behavior.
**Does not own:** secret storage or performance benchmarking.

## Rules

DO enumerate plausible failure modes, dependencies, time bounds, retry behavior,
idempotency requirements, and partial-success semantics.

DO emit structured, actionable signals that support service objectives without
leaking sensitive data.

DO bound retries with timeouts, backoff, budgets, and cancellation behavior.

DO design for overload, duplicate delivery, ordering, concurrency, and recovery
where the boundary requires it.

DO test failure handling with controlled faults or equivalent evidence.

DON'T retry indefinitely or hide a partial failure behind a generic success.

DON'T place credentials, personal data, or raw sensitive payloads in telemetry.

## Red flags

- "Retries make it reliable." Bound retries and define duplicate effects.
- "We can debug it from logs." Confirm the needed signal exists and is safe.

## Done when

- [ ] Failures are bounded, diagnosable, and safe to observe.
- [ ] Recovery and concurrency behavior are tested or otherwise evidenced.
- [ ] Telemetry supports decisions without leaking sensitive data.
