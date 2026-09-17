# Local Agent Stack Profile

> Local implementation mapping. This profile is descriptive, optional, and
> subordinate to durable policy and repository instructions.

## Purpose

The durable rulebooks describe capabilities and roles. This profile records the
current local names that may implement them so those names do not become policy.
Availability changes do not alter the required gates or authorization rules.

## Capability mapping

| Durable role or capability | Current local mapping | Notes |
|---|---|---|
| Cross-model plan and final soundness review | Codex, `gpt-5.6-sol` | Two checkpoints in the local loop: reviews the Opus-authored plan BEFORE it is handed to the Sonnet builder (DEVELOP does not start until its plan findings are addressed), and reviews the finished product AFTER the dennis gate is green (before merge). Policy requires the role only where a repository or risk gate specifies it. |
| Fresh-context adversarial review | dennis, Opus | Independent from the author. |
| Planning and difficult technical judgment | Opus | Local high-capability mapping. |
| Routine implementation from an established guide | Sonnet | Local builder mapping. |
| Routine documentation and mechanical synthesis | barry, Haiku | Local documentation mapping. |
| Final-review fallback when Codex is unavailable | Fable | Fallback only; not a planning or build tier. |

## Optional local integrations

- Local search, knowledge indexes, skills, and graph tools can support discovery
  and framing when available.
- A messaging integration may carry a human authorization or status update only
  when the task requests or authorizes communication. It is never itself an
  authorization rule.
- Tool availability does not grant permission for destructive, external, or
  otherwise unauthorized actions.

## Maintenance

Update this profile when local capability names or availability change. Keep
durable rulebooks role- and tool-neutral.
