# Delegation role matrix

The conductor picks the delegate tier from four complexity signals assessed at
FRAME: **risk level**, **novelty** (established pattern vs new ground), **blast
radius**, and **ambiguity**. Higher on any signal pulls a more capable agent. The
conductor cites the tier reason when it delegates, so selection is auditable and
resists lazy over- or under-tiering.

| Role | Fires when | Complexity to agent | Why this tier |
|---|---|---|---|
| Researcher / Explorer | Locate or understand code/docs | Read-only fan-out to the Explore agent | Conclusion-only work; keeps the main context clean, no judgment output |
| Planner | R2/R3, or novel / high-ambiguity work | Top tier (Opus) | Plans and specs must be top-tier; errors here cascade through the slice |
| Builder | Implementing approved work | Routine-from-guide to Sonnet; novel or high-blast-radius to Opus | Match capability to the judgment the code needs; do not pay Opus for boilerplate |
| Reviewer (gate) | Any non-trivial work before done | Fresh-context adversarial (dennis, Opus) | Independence from the author is the point |
| Cross-model gate | R2/R3 final soundness | **Codex always; fallback is the highest available Claude model** | Cross-model review catches same-model blind spots |
| Documenter | DOCUMENT phase, docs-current | Mechanical synthesis to barry (Haiku); nuanced docs to Opus | Cheap for mechanical updates, escalate only when docs need judgment |
| Operator | VCS / deploy | Conductor itself; R3 side effects gate on human authorization first | Light ops stay inline; irreversible ops gate on the human |

## Hard rules

- The Codex cross-model gate is **always Codex**. If Codex is unavailable, use the
  highest-capability Claude model available; never skip the gate for R2/R3.
- Plans, specs, and guides are authored by the top tier, never a builder tier.
- The reviewer is independent from the author; the same agent does not both build
  and gate a change.

See the bundled rulebooks under `rules/` (`delegation.md`, `development-loop.md`,
`risk-and-exceptions.md`) for the canonical policy this matrix implements.
