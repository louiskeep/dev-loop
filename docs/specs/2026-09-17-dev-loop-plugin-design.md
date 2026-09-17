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
    run-hook.cmd             # wrapper, resolves ${CLAUDE_PLUGIN_ROOT}
    gate_guard.py            # PreToolUse: block merge/push without a green gate
    loop_state.py            # state-file read/write + staleness logic + CLI
    done_claim_check.py      # Stop hook: flag done/merge claims without evidence
  rules/                     # dev-rules bundled as reference
  config.json                # local stack bindings (models, paths, Slack, toggles)
```

`config.json` isolates machine-specific bindings (model names, Codex
availability, Slack webhook, dev-rules path, gated-branch names, escape-hatch
toggle) so policy stays clean. Bundling `rules/` versions the rulebooks with the
enforcement that cites them.

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

### Loop-state file

One `.loop-state.json` per slice at repo root, gitignored and machine-local so it
survives context restarts (the autonomous-operation recoverability rule). All
evidence keys off git commits so it is tied to an exact artifact.

```json
{
  "slice": "slice-6-generation-throughput",
  "risk": "R2",
  "risk_rationale": "engine hot-path perf + native seam; blast radius across generation",
  "slice_base": "a1b2c3d",
  "gates": {
    "plan_review": { "status": "green", "reviewer": "codex", "ts": "..." },
    "dennis":      { "status": "green", "at_commit": "e4f5a6b", "ts": "..." },
    "codex":       { "status": "green", "at_commit": "e4f5a6b", "ts": "..." }
  },
  "remediation": { "open_findings": 0 },
  "docs_current": { "roadmap": true, "shipped_log": true }
}
```

`loop_state.py` is the shared library and CLI. The conductor and subagents record
results through it (for example `loop_state.py record-gate dennis --status green
--commit HEAD`), never by hand-editing. It computes staleness: a gate is stale
when its `at_commit` does not equal HEAD.

### Hooks

1. `gate_guard.py` (PreToolUse on Bash). Matches merge-to-main, push-to-main,
   `gh pr merge`, and force-push (not feature-branch pushes, which are
   recoverable checkpoints). Denies unless: dennis gate green and on HEAD; for
   R2/R3, Codex gate green and on HEAD; and both `docs_current` flags true. On
   deny it returns the specific missing evidence. This is the hard stop on
   self-certified done.

2. Re-gate invalidation (in `loop_state.py`, enforced by gate_guard). Any commit
   after a gate's `at_commit` makes it stale, so a patched-then-claimed-done
   slice reads stale and merge is blocked until re-gated. This enforces
   "remediation not re-reviewed."

3. `done_claim_check.py` (Stop hook). Scans the conductor's closing message for
   done / merge-ready / complete language; if the state file is missing, stale, or
   red, it blocks the stop and feeds back the gap. A text heuristic, the softest
   of the three, a backstop rather than a wall.

### Docs-current and risk classification

Docs-current is mechanical: a check runs `git diff --name-only <slice_base>..HEAD`
and sets the flags true only if the roadmap and shipped-log paths (from
`config.json`) appear.

Risk classification is the one mode that cannot be fully mechanized. The state
file forces an explicit `risk` plus rationale (no silent default) and requires
Codex for R2/R3, but catching an under-classification stays a dennis/Codex
checklist item. This is the honest boundary of "hard enforcement."

### Failure mode

Gated operations fail closed. A missing or corrupt state file, or a crashing
hook, blocks merge and push-to-main rather than letting an unreviewed change
through, with a clear message and a `config.json` toggle for the escape hatch.

## The conducting-the-loop discipline skill

The hooks own the decidable boundaries; this skill owns the judgment steps. It
follows the writing-skills rules: the description states triggering conditions
only (no workflow summary) so the conductor reads the body rather than
shortcutting it.

Contents:

- Conductor identity, loaded via a SessionStart hook so it is live every session.
  Stated as a positive recipe (the conductor's output is decisions, delegation
  calls, and synthesized results with next-step options; direct edits are limited
  to edge cleanup and git ops). "Never do heavy work" is a shaping rule, so a
  recipe binds better than a prohibition.
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

- `gate_guard` across the matrix: green-on-HEAD allows; stale, red, or missing
  denies; R1 needs dennis only; R2/R3 also needs Codex; docs flags false denies;
  fail-closed on missing or corrupt state.
- staleness: a commit after a gate makes it stale.
- `done_claim_check`: detects done/merge language, passes on clean, blocks on
  stale.
- docs-current diff check: roadmap plus shipped-log present is true, absent is
  false.

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

- Risk under-classification cannot be mechanically caught; it relies on the
  independent gate. Accepted.
- `done_claim_check` is a text heuristic and will have false negatives; it is a
  backstop, not the primary control (gate_guard is).
- Exact gated-branch set and the escape-hatch toggle behavior need to be fixed in
  `config.json` during implementation.
- Whether `.loop-state.json` should ever be committed for shared visibility, or
  stay machine-local, is deferred; default is machine-local and gitignored.

## Next step

Spec review by Cam, then the writing-plans skill to produce the implementation
plan.
