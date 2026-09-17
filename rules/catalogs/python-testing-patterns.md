# Python Testing Patterns

> Optional language-specific testing catalog. General testing policy remains in
> [testing](../testing.md).

## Exception-chain and locals-capture redaction

**Risk:** an exception may hide a secret in its message while retaining it in a
cause, context, or captured local frame.

**Test shape:** trigger the error path with a known marker that is not a local
variable in the test frame. Assert the marker is absent from formatted exception
output and from a traceback representation that captures locals.

**Evidence:** the test should fail before redaction or context cleanup and pass
afterward without weakening the captured-locals assertion.

**Implementation notes:** remove sensitive local references before raising,
avoid preserving a sensitive causal chain where it is not needed, and emit safe
error classifications rather than raw provider messages.

## Adding an entry

Add a recurring, portable bug class only. Describe the trap, the assertion that
catches it, its fail-before behavior, and safe implementation constraints. Keep
repository sightings in the repository that owns them.
