# Cross-cutting Concerns

> Guidance for any rule that applies at many call sites: authorization, auditing,
> size caps, timeouts, file modes, clocks, foreign-key behavior, and any table
> that other code must agree with.

## When this applies

Use when a change adds, moves, or depends on a rule that more than one call site
must follow, or adds a site to such a rule (a new route, command, job type,
table, loader, client, or spawn). Pair it with [architecture](architecture.md)
for the structural choice and [testing](testing.md) for the guard test's depth.
Authorization rules also read [security](security.md).

## The problem and its names

A cross-cutting rule enforced by convention is written by hand at each call
site. Every new feature must remember to call the right helper, and nothing
fails when it forgets. The literature calls this scattering of a cross-cutting
concern, or shotgun surgery when one rule change needs edits in many files. In
security it is a missing policy enforcement point, which violates complete
mediation. "No central registry" is one symptom; the defect is that forgetting
is silent.

The fix has two parts and is incomplete without either:

1. One choke point that every site must pass through: a registry, a
   middleware, a base class or router subclass, a schema constraint, a session
   or ORM event, a typed wrapper, a single factory. The rule is then enforced by
   construction. The wrong form does not compile, does not start, or fails a
   test.
2. A guard test (an architectural fitness function) that walks the real
   artifact, not the source the author remembers: the mounted routes, the
   reflected schema, the import graph, the syntax tree. It fails on any site
   outside the choke point. Known exceptions live in a ratchet allowlist that
   may shrink but never grows without a reviewed change.

## Rules

DO name the rule and its sites before building. A plan that adds a site to a
rule, or adds a rule with more than one site, states the choke point, the
guard test, and the one mutation that must turn the guard red. See the PLAN
phase of the [development loop](development-loop.md).

DO enumerate sites with a walk, never a grep alone. Aliases, re-exports,
decorators, nested routers, and string-built names hide from a text search.
Build the app and walk its routes; migrate and reflect the schema; parse the
tree and resolve imports.

DO prove the walk sees a bypass before trusting its count. Seed one known
violation in a scratch copy and show the count rises; remove one real site and
show it falls. A walk that cannot see a seeded bypass is wrong.

DO prefer an established mechanism over hand-rolled machinery: a framework
dependency or router subclass, a web-server middleware, a database constraint or
ORM event, a typed secret, a client factory from the SDK, a maintained library.
Hand-rolled machinery that keeps growing is the smell this rulebook exists to
stop.

DO record "every caller on purpose" explicitly. A site that serves every user,
skips a cap, or bypasses a factory by design carries a named exemption in the
registry or allowlist with its reason. A reader must be able to tell a decision
from an omission.

DO keep one descriptor per variant. Where several tables must agree (a
registry, a capability map, a documentation matrix, a copy in another
repository), derive the others from one root or add a guard that fails when they
disagree. Export a shared list from its owning package rather than copying the
literal.

DO wire the guard where it runs. A guard that runs only on a developer's
machine is a convention. It belongs in the suite the merge gate executes, and
the gate must actually run.

DO rate a forgotten site by consequence, not by count. The question is what
happens to the product and to the people who can see the result when the next
feature forgets. That answer sets the priority and the phase.

DON'T treat a helper as a choke point. A helper every site is expected to call
is the convention form. It becomes construction only when a site that skips it
fails.

DON'T enumerate sites by hand inside the guard itself. A guard parametrized over
a hand-written list of routes or commands has the disease it guards against;
derive the list from the same walk.

DON'T add a registry for a rule with one site and no realistic second one.
Record the rule and move on.

## Red flags

- "Remember to also update X." A parallel table with no guard.
- "Each route handles that itself." Scattered enforcement; ask what fails when
  one does not.
- "The scan found zero." Confirm the scan sees a seeded violation.
- "The test walks the route list." Confirm the list holds the mounted routes
  after the framework's own nesting or lazy resolution.
- "It is checked in the handler body." Nothing outside the handler can see it;
  move the fact to a declaration the guard can read.

## Done when

- [ ] The rule's choke point is named and every site goes through it, or the
      exceptions are listed with reasons.
- [ ] A guard test walks the real artifact and fails on a bypass; its seeded
      mutation was shown red.
- [ ] The allowlist, if any, is a ratchet with a recorded size.
- [ ] The guard runs in the suite the merge gate executes.
- [ ] Any parallel table is derived from one root or guarded for agreement.
