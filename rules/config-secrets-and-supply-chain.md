# Config, Secrets, and Supply Chain

> Guidance for configuration, credential handling, reproducible inputs, and
> dependency provenance.

## When this applies

Use for configuration, credentials, build inputs, package dependencies, or
generated supply-chain artifacts.

## Scope

**Owns:** configuration precedence and validation, secret sourcing and rotation,
redaction, dependency provenance, lockfiles, vulnerability review, and update
strategy. **Does not own:** application authorization logic or general
build-versus-buy decisions.

## Rules

DO define configuration sources, precedence, defaults, validation, and failure
behavior before adding a setting.

DO obtain secrets through an approved injection mechanism; keep them out of
source, artifacts, command output, logs, diagnostics, and test fixtures.

DO define rotation, revocation, and access boundaries for credentials.

DO use reproducible dependency inputs, record provenance, maintain lockfiles
where the ecosystem supports them, and assess vulnerabilities and update paths.

DO redact sensitive values in evidence and provide safe diagnostic identifiers
instead.

DON'T use a configuration default that silently turns an unsafe operation on.

DON'T treat a package name alone as provenance or a lockfile as a vulnerability
review.

## Red flags

- "It is only an environment variable." Define precedence, validation, and safe
  failure behavior.
- "The secret is not in source." Check artifacts, logs, command output, and
  evidence.

## Done when

- [ ] Inputs are validated and precedence is explicit.
- [ ] Secrets stay out of artifacts and evidence, with a rotation path.
- [ ] Dependencies are reproducible, attributable, and maintainable.
