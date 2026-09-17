# Security

> Guidance for trust boundaries, authentication, authorization, secure defaults,
> and sensitive-data handling.

## When this applies

Use when work touches input, identity, permissions, sensitive data, secrets,
dependencies, or a trust boundary.

## Rules

DO inventory trust boundaries, assets, actors, entry points, and plausible abuse
before changing security-sensitive behavior.

DO distinguish authentication from authorization and enforce authorization at
the trusted decision point.

DO validate untrusted input, encode output for its destination, and use secure
defaults and least privilege.

DO use safe parameter binding for every query language or interpreter interface;
never assemble untrusted values into executable query text.

DO use maintained, vetted cryptographic implementations and their documented
safe defaults; do not create custom cryptographic primitives or protocols.

DO keep secrets out of source, artifacts, logs, errors, and test evidence. See
[config, secrets, and supply chain](config-secrets-and-supply-chain.md).

DO assess dependency provenance and known risk before adoption or update.

DON'T trust a client assertion or network location as authorization.

DON'T concatenate untrusted input into a query, command, or interpreter source.

DON'T substitute an unreviewed cryptographic helper for a maintained library.

DON'T disclose sensitive data in diagnostics or review evidence.

## Red flags

- "It is internal." Identify the trust boundary and authorization decision.
- "The error is useful for debugging." Redact sensitive data before recording it.

## Done when

- [ ] Trust boundaries and authorization decisions are explicit.
- [ ] Secure defaults, validation, and least privilege are verified.
- [ ] Query binding and cryptographic implementation choices are safe and vetted.
- [ ] Sensitive data is absent from artifacts and evidence.
