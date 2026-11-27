# Recurring defect classes

Status: current

Classes that independent review kept finding in real slices (Decoy, 2026-11). Builders check
each one in self-review. Reviewers check each one by name. Each entry gives the rule, the usual
miss, and the guard. Python examples; the rule is general.

1. **Anchored validation.** Validate whole values with `re.fullmatch` or `\Z`, never `$`. `$`
   also matches before a trailing newline, so `"v1\n"` passes. Guard: a test that feeds the value
   plus `"\n"` and expects a refusal.

2. **Strict wire and stored types.** A model that is persisted, sent or read back uses strict
   primitives (`StrictInt`, `StrictBool`, `Strict()` floats, or the library's strict mode). Lax
   parsing turns `True` into 1 and `"5"` into 5, so a corrupt record reads as a real one. Guard:
   for each primitive field, a bool, a numeric string and a wrong-typed value, all refused on
   write and on read.

3. **No exception context.** `raise X from None` hides the cause but keeps `__context__`, which
   can carry a path, value or OS message. Raise the safe error after the `except` block closes
   (save what you need in a variable). Guard: assert `__cause__ is None and __context__ is None`
   on every value-free error.

4. **Versioned read models.** A reader of a persisted or wire format:
   - refuses an unknown major version and a missing version tag;
   - tolerates unknown minors and extra fields as the contract says;
   - reads enums as checked tokens, never free `str`;
   - has one writer model and a reader derived from it, not hand-copied.

   Guard: cells for the next major, a missing tag, free text in an enum, and a value that is only
   valid in a later minor.

5. **Normalized guard scanners.** A sentry that matches commands, calls or imports must normalize
   before matching:
   - command basenames (`/usr/bin/pip` and `pip3.11` count as `pip`);
   - `--opt=value` and attached short values (`-rfile`, `-e.`);
   - import aliases, `import a.b` binding, `from x import y`, and unbound method forms.

   Guard: a seeded case per spelling that must fail. A rewrite or copy of a scanner in another
   repo keeps every rule of the original, or lists each dropped rule and why.

   Scope (owner, 2026-11-06): a guard scanner targets accidental mistakes, the spellings a
   person or tool would write by habit. List the covered shapes in the plan before building.
   Deliberate evasion (`eval`, command substitution, config-file indirection) is V2-LOG, so
   reviews stop at the listed shapes instead of adding a round for each new bypass.

6. **No reaching into private modules.** Import another package's public API, never its
   `_private` module. If the name is not public and you need it, export it from the owning
   package. Guard: import-layer contracts (LAYOUT slice). Until those exist, reviewers flag any new
   `from <other_package>._x import`.

7. **No hidden CI failures.** A check that must fail when its condition fails has:
   - no `|| true`, `; exit 0`, `set +e` or `continue-on-error`;
   - no custom shell that swallows the exit code;
   - no dry-run or report-only flag;
   - no narrowed input (for example an export missing `--all-extras`).

   Matrix legs use `fail-fast: false`. Guard: a sentry with a seeded case per hiding form.

8. **One input, one trust class per entry point.** When the same operation takes both
   program-controlled values and user-chosen values (file names, keys, labels), give each its own
   named entry point on one shared core. The strict rule for internal names must not reject what
   users could always pass, and the user path must not skip containment. Guard: caller tests with
   the user forms that worked before (spaces, leading `.` or `_`, non-ASCII).

9. **Mutations on the final head.** Rerun the slice's mutations after the last code change; a
   refactor after the mutation run voids it. Record the head each mutation ran on.

10. **Allowlisted helpers are still checked.** A helper added to a sentry's allowlist as a
    forwarding helper must have its callers checked by the same sentry (exact `(path, name)`
    identity, not a bare name). Otherwise the allowlist is a bypass.

11. **No raised pins.** Never raise a size, count, ratchet or allowlist pin in the same change
    that grows the pinned thing. Shrink the code, or record the exception for the owner.

Related: [cross-cutting concerns](../cross-cutting-concerns.md), [testing](../testing.md),
[security](../security.md), [api and compatibility](../api-and-compatibility.md).
