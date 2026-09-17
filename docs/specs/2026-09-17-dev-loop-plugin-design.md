# dev-loop plugin: design

- Date: 2026-09-17
- Status: proposal (design approved in brainstorm; awaiting spec review before planning)
- Author: cam (with Claude)

## Context

The development loop Cam runs (FRAME through DOCUMENT, at a risk level, with
independent gates and delegated roles) already exists, but it lives in five
scattered, host-local places: the `~/dev-rules/` rulebooks, agent definitions
(dennis, barry), `settings.json` hooks, standalone skills, and memory
conventions. Because the pieces are separate and mostly advisory, the loop can
drift or be skipped. The goal of this plugin is to package the process into one
versioned unit and put mechanical teeth where the loop actually gets skipped.

## Goals

- Make the four observed drift modes harder to skip:
  1. Self-certified "done" (merge without an independent gate on the exact artifact).
  2. Remediation not re-reviewed (findings patched, fix never re-gated).
  3. Risk under-classification (R2/R3 treated as R1, collapsing required gates).
  4. Docs/roadmap drift (slice closed with a stale roadmap or shipped-log).
- Establish a conductor model: the main thread commands, talks to the user, does
  light work, and orchestrates; substantial coding and research are delegated.
- One versioned source of truth that keeps policy (rulebooks) and enforcement
  (hooks) evolving together.

## Non-goals

- Portability and public sharing are secondary. It is acceptable to hardcode
  Cam's stack through a local config file. The rulebooks stay tool-neutral, but
  the plugin binds them to concrete local agents.
- Not a full loop state machine (Approach C). That is more scaffolding than a
  single-user tool needs. Revisit only if Approach B proves leaky.

## Decisions locked in the brainstorm

- Driver: consistency and enforcement.
- Enforce all four drift modes.
- Conductor role: commands, user interaction, light work (edge cleanup, small
  edits, git commit/push/merge, project Q&A), and orchestration. Heavy coding
  and research are delegated and returned as the raw result, or a summary plus
  advised next steps with remediation options.
- Delegation is role-based with complexity-tiered selection: more complex work
  goes to a more capable agent, with the reason recorded. The Codex cross-model
  gate is always Codex; if Codex is unavailable the fallback is the highest
  available Claude model.
- Enforcement approach: B (hooks for the decidable boundaries, a discipline
  skill for the judgment steps).
- Distribution: standalone git repo, imported into Claude Code as a plugin via a
  marketplace manifest.

## Architecture: plugin layout

```
dev-loop/
  .claude-plugin/
    plugin.json              # name, version, description
    marketplace.json         # install source for .55 / .116 / cloud
  skills/
    conducting-the-loop/
      SKILL.md               # conductor discipline skill (judgment steps)
      role-matrix.md         # complexity-tiered delegation reference
  agents/
    dennis.md  barry.md      # moved in from ~/.claude/agents (gate + docs)
  hooks/
    hooks.json               # hook registrations
    run-hook.sh              # wrapper, resolves ${CLAUDE_PLUGIN_ROOT} (Linux/macOS; this is a Linux-target personal plugin)
    gate_guard.py            # PreToolUse: block merge/push without a green gate
    loop_state.py            # state-file read/write + staleness logic + CLI
    done_claim_check.py      # Stop hook: flag done/merge claims without evidence
  rules/                     # dev-rules bundled as reference
  config.json                # plugin defaults (protected branches, doc paths, gate policy, escape hatch)
```

`config.json` holds the plugin's default policy: `protected_branches`,
`roadmap_paths`, `shipped_log_paths`, `codex_required_risks`, and `escape_hatch`.
A per-repo `.loop-config.json` at the target repo root overrides these. Bundling
`rules/` versions the rulebooks with the enforcement that cites them.

## Conductor and complexity-tiered delegation

The conductor (main thread) owns intake, risk framing, delegation, gate
sequencing, git operations, project Q&A, and edge cleanup. Everything
substantial is delegated and returned as a raw result or a summary plus
next-step options.

The conductor picks the delegate tier from four complexity signals assessed at
FRAME: risk level, novelty (established pattern vs new ground), blast radius, and
ambiguity. Higher on any signal pulls a more capable agent.

| Role | Fires when | Complexity to agent | Why this tier |
|---|---|---|---|
| Researcher / Explorer | Locate or understand code/docs | Read-only fan-out to Explore agent | Conclusion-only; keeps main context clean, no judgment output |
| Planner | R2/R3, or novel / high-ambiguity work | Top tier to Opus | Plans and specs must be top-tier; errors here cascade |
| Builder | Implementing approved work | Routine-from-guide to Sonnet; novel or high-blast-radius to Opus | Match capability to judgment needed; do not pay Opus for boilerplate |
| Reviewer (gate) | Any non-trivial work before done | Fresh-context adversarial to dennis (Opus) | Independence from author is the point |
| Cross-model gate | R2/R3 final soundness | Codex always; fallback highest available Claude | Cross-model catches same-model blind spots |
| Documenter | DOCUMENT phase, docs-current | Mechanical synthesis to barry (Haiku); nuanced docs to Opus | Cheap for mechanical, escalate only when docs need judgment |
| Operator | VCS / deploy | Conductor itself; R3 side effects gate on human auth first | Light ops stay inline; irreversible ops gate on Cam |

The rubric and these reasons live in `role-matrix.md`; the conductor cites the
tier reason when it delegates, so selection is auditable and resists lazy over-
or under-tiering.

## Enforcement

### What the enforcement is (and is not)

This is strong workflow enforcement, not a hard git security boundary. A local
`PreToolUse` command hook only fires when Claude Code invokes the Bash tool; a
hook that times out, fails to start, has a bad interpreter, or lives in an
invalid manifest lets the tool proceed (Claude Code's documented behavior). It
also does nothing about a merge run from another terminal, another tool, or CI.
The real wall for a protected branch is **server-side branch protection** (or a
pre-receive hook) on the remote. The plugin's `README` states this plainly and
documents wiring server-side protection as the recommended complement. Inside a
Claude Code session, the plugin makes the four drift modes hard to do by
accident or under pressure, which is the stated driver (consistency). It does
not claim to make them impossible.

Accepted scope (Cam, 2026-09-17): the in-session guard is best-effort. It stops
the common and accidental protected-branch operations and the ones a conductor
reaches for under pressure. It deliberately does not try to be airtight against a
caller actively hiding a push from it (interpreter wrappers like `bash -c '...'`,
transient config aliases like `git -c alias.x=push x`, exotic `push.default`
settings). That is an unwinnable arms race and the wrong threat model for a
single trusted operator. The guard denies the cheap obfuscations it can recognize
and fails closed on ambiguity; the authoritative boundary for a determined or
external actor is server-side branch protection. This is a scope decision, not a
defect, and will surface in review as an accepted residual.

### Loop-state file

One `.loop-state.json` at the target repo root, keyed off git commit SHAs so
evidence is tied to an exact artifact. It is machine-local; the `init` command
adds it to the target repo's `.gitignore`. It is read and written only through
`loop_state.py`, which validates a versioned schema and rejects malformed or
incomplete state (a malformed file fails closed at the merge check).

```json
{
  "schema_version": 1,
  "slice": "slice-6-generation-throughput",
  "risk": "R2",
  "risk_rationale": "engine hot-path perf + native seam; blast radius across generation",
  "slice_base": "a1b2c3d",
  "gates": {
    "plan_review": { "status": "green", "reviewer": "codex",  "ts": "..." },
    "dennis":      { "status": "green", "reviewer": "dennis", "at_commit": "e4f5a6b", "ts": "..." },
    "codex":       { "status": "green", "reviewer": "codex",  "at_commit": "e4f5a6b", "ts": "..." }
  },
  "remediation": { "open_findings": 0 }
}
```

Every recorded gate carries `reviewer`, `ts`, and (for the artifact gates) the
`at_commit` it attests to. `risk` is required and validated against `R0`-`R3`; a
missing or unknown risk fails the merge check closed rather than silently
skipping the Codex requirement. Docs-current is not a stored boolean (see below).

### Accepted decision: gate independence is procedural, not cryptographic

A caller could in principle write `dennis green` without dennis having run. The
plugin records who, when, and which commit, but it does not cryptographically
prove the review happened. This is an accepted design decision, not an unsolved
gap, and it is right-sized to the threat model:

- The operator is a single trusted person (Cam) plus the agents he directs. The
  threat this plugin exists to counter is the *conductor skipping the gate under
  pressure*, not an adversary forging review records. Making a skip require an
  explicit, visible, commit-bound state write (which the merge hook then checks)
  already defeats the accidental/pressure skip.
- The standing product constraint is self-hosted single-org, do not overbuild,
  defer adversarial/internet-scale hardening. A signed-attestation or
  mandatory-CI integrity layer would be exactly that over-build.

If the threat model ever changes (untrusted contributors, shared CI), the correct
answer is server-side branch protection with required status checks and required
reviewers, or an externally minted commit-bound signed attestation. That is
documented in the README as the upgrade path. The in-session plugin does not
attempt it.

### What the gate attests to, and which commit is checked

Each artifact gate records the `at_commit` it attests to (the tip dennis/Codex
actually read). The merge check does not compare against the pre-command `HEAD`;
it resolves the commit the operation would land on the protected branch and
requires every required gate's `at_commit` to equal that landing commit:

- Direct push of a ref to a protected branch: the pushed commit must equal every
  required gate's `at_commit`.
- Fast-forward merge into a protected branch: the merged tip must equal them.
- A merge that creates a new merge commit is, by definition, a commit no gate has
  seen, so it is blocked until re-gated on that merge commit.
- Compound commands that both mutate history and push a protected ref (for
  example `git merge x && git push origin main`) are blocked outright; the two
  steps must be separate so each is gated against the right commit.

Any new commit after the gated one changes the landing commit, so the gates no
longer match and the merge is blocked. That is how "remediation not re-reviewed"
is caught.

### Hooks

1. `gate_guard.py` (PreToolUse on Bash). It does not decide by substring match.
   It shlex-tokenizes the command, resolves the target repo via `git rev-parse
   --show-toplevel`, and runs a strict argv parser that allows only a small
   canonical set and denies anything else in (or possibly in) the
   push/merge/pull family. Supported checkable forms: `git push [remote]
   <src>:<protected>` or `<protected>` (only `--force`/`--force-with-lease`
   options), a bare/remote-only push resolved via `push.default` and
   `remote.<remote>.push` (denied when `matching` or a configured push refspec
   makes it ambiguous), and `git merge --ff-only <ref>` into a protected branch
   (the source deref'd with `^{commit}` so annotated tags resolve). It **fails
   closed on anything else that touches the family**: `git pull` on a protected
   branch, non-`--ff-only` merges, delete/empty/wildcard/multiple refspecs, a
   destination in a non-branch ref namespace (still `refs/...` after stripping
   `refs/heads/`; plain slash branches like `feat/x` are allowed when not
   protected), any `git -C`/`--git-dir`/`--work-tree` repo-retargeted command
   other than a read-only safe builtin (`status`, `log`, `diff`, `fetch`, ...),
   since the target repo's aliases/config cannot be resolved from cwd, any token
   carrying a shell metacharacter or expansion, unbalanced
   quotes, real interpreter/binary wrappers (`bash`, `sudo`, `env`, ...),
   configured git aliases, and `gh pr merge` (which validates the local checkout,
   not the PR head). A leading `DEVLOOP_OVERRIDE=` and any leading `VAR=val`
   env-assignment prefixes (for example `GIT_SSH=x git ...`) are first stripped and
   the underlying `git` command is then assessed normally (so an env-prefixed push
   is gated, not blanket-denied); this normalization is distinct from a real
   wrapper, which is denied. Feature-branch pushes with no protected-branch target,
   and non-family git commands, are allowed. The audited
   escape hatch (below) is the sole override.

2. Re-gate invalidation (in `loop_state.py`, enforced by gate_guard): a landing
   commit that does not equal every required gate's `at_commit` is blocked.

3. `done_claim_check.py` (Stop hook). It reads `last_assistant_message`; if it
   claims done/merge-ready and the state is missing, stale, or red, it emits a
   warning back to the model via `hookSpecificOutput.additionalContext` so the
   model re-checks before the user acts. This is a deliberate product choice:
   Stop hooks in this build *can* block (`decision: block`), but blocking on a
   text heuristic risks false-positive turn-stalls, so this layer warns rather
   than blocks. `gate_guard` is the wall; this is the visible backstop.

### Docs-current

Docs-current is computed live at the merge check, never stored as a settable
boolean. `is_mergeable` runs `git diff --name-only <slice_base>..<landing_commit>`
(the same landing commit the gates are checked against) and requires the
configured roadmap and shipped-log paths to appear in the changed set. There is no `set-docs-current true true` bypass; the only way to satisfy it
is to actually touch those docs in the slice.

### Risk classification

Risk classification is the one mode that cannot be mechanized. The state file
forces an explicit, validated `risk` plus rationale (missing/unknown fails
closed) and requires Codex for R2/R3, but catching an *under*-classification
stays a dennis/Codex checklist item. This is the honest boundary of the
in-session enforcement.

### Failure model

For the outermost handler, gated operations fail closed: any internal error on a
command the guard has classified as a protected-branch operation results in a
DENY (exit 2), never an allow. The audited escape hatch is explicit and logged:
it activates only when `config.escape_hatch` is true AND the environment variable
`DEVLOOP_OVERRIDE=<reason>` is set on the operation; the guard then allows the op
and appends `{ts, command, reason, landing_commit}` to `.loop-audit.log` in the
repo. With `escape_hatch` false (the default) the variable is ignored. There is
no silent bypass. The honest caveat from "What the enforcement is" still holds: a
hook that never runs (timeout, startup failure, invalid manifest, operation
outside the Bash tool) cannot fail closed at all, which is why server-side
protection is the real boundary.

## The conducting-the-loop discipline skill

The hooks own the decidable boundaries; this skill owns the judgment steps. It
follows the writing-skills rules: the description states triggering conditions
only (no workflow summary) so the conductor reads the body rather than
shortcutting it.

Contents:

- Conductor identity. Primary load is the skill's own description trigger. A
  SessionStart `command` hook (matching `startup|resume|clear|compact|fork`)
  reinforces it by emitting the conductor preamble through
  `hookSpecificOutput.additionalContext`, the mechanism that actually reaches the
  model. Task 0 verifies this empirically before the plan depends on it. Stated as
  a positive recipe (the conductor's output is decisions, delegation calls, and
  synthesized results with next-step options; direct edits are limited to edge
  cleanup and git ops). "Never do heavy work" is a shaping rule, so a recipe binds
  better than a prohibition.
- Phase sequence at risk level, referencing the bundled `rules/` rather than
  restating them.
- Complexity-tiered delegation, referencing `role-matrix.md`, requiring the
  conductor to cite the tier reason when delegating.
- Rationalization tables and red-flag lists, one row per drift mode, built from
  the RED baseline. The two judgment-heavy modes live here: risk
  under-classification (the mode hooks cannot catch) and self-certified done
  under pressure (skill plus hook together). Docs-drift and re-review point at
  their hooks.

The split matches the writing-skills rule: automate mechanical constraints, and
document judgment calls.

## Testing plan

Two surfaces, tested differently.

Hooks (mechanical) get ordinary pytest coverage:

- `gate_guard` command classification: a bypass/false-deny matrix covering push
  of a ref to a protected branch, force-push, fast-forward merge, `git -C`, `cd &&`,
  subdirectory invocation, worktrees, `--all`/`--mirror`, compound `merge && push`,
  `gh pr merge`, `push origin main:feature`, comments/quoted text, and plain
  feature-branch pushes. Ambiguous forms must DENY.
- landing-commit check: allows when the landing commit equals every required
  gate's `at_commit`; denies when a new commit has been added since; R1 needs
  dennis only, R2/R3 also needs Codex.
- fail-closed: missing, malformed, or schema-invalid state denies a classified
  protected-branch op; a guard exception on such an op denies.
- `done_claim_check`: detects done/merge language, silent on clean, emits an
  `additionalContext` warning on missing/stale/red, honors `stop_hook_active`.
- docs-current computed live: roadmap plus shipped-log touched in
  `slice_base..<landing_commit>` is required; a slice that did not touch them
  cannot merge. Test with a landing commit distinct from `HEAD`.

The discipline skill uses RED-GREEN-REFACTOR with subagents, per the
writing-skills Iron Law (test before writing the skill):

1. RED: run a conductor subagent without the skill under combined pressure
   ("three sprints behind, main is green, just merge it") and capture the
   verbatim rationalizations for each drift mode. If a no-skill control does not
   exhibit a failure, do not write guidance for it.
2. GREEN: write the skill to counter those rationalizations; micro-test the
   wording against the no-guidance control (5+ reps) before the expensive full
   scenarios.
3. REFACTOR: re-run under maximum pressure, add counters for any new loophole,
   until it complies.

## Distribution

Standalone git repo (`~/dev-loop-plugin`). Imported into Claude Code as a plugin
through `.claude-plugin/marketplace.json`, so it can be installed on .55, .116,
and cloud agents from the same source.

## Open questions and known limits

- In-session enforcement is not a hard boundary: a hook that never runs, or an
  operation outside the Bash tool or from CI, is not gated. Server-side branch
  protection is the real wall and is documented as the recommended complement.
- Gate independence is procedural, not proven: the record ties a gate to a
  reviewer, commit, and time, but cannot prove the review ran. Mitigated by the
  conductor delegating gates to the dennis/Codex agents.
- Risk under-classification cannot be mechanically caught; it relies on the
  independent gate. Accepted.
- `done_claim_check` is a text heuristic with false negatives; it warns via
  `additionalContext`, it is not the wall (gate_guard is).
- `gate_guard` supports a defined set of direct git forms and fails closed on the
  rest; `gh pr merge` is not gated in-session (blocked with guidance) because the
  local checkout is not the PR head. PR-merge gating, if wanted, belongs in
  server-side/CI enforcement.
- Exact protected-branch set and the escape-hatch toggle live in `config.json`
  (plugin defaults) and `.loop-config.json` (per-repo override).

## Next step

Spec review by Cam, then the writing-plans skill to produce the implementation
plan.
