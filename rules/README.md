# dev-rules

Stack-agnostic development rulebooks. Use the routing table to select the
canonical policy and task rulebooks that match the work.

## How to use

1. Read [universal rules](00-universal.md) for precedence and immutable
   invariants, and [risk and exceptions](risk-and-exceptions.md) for risk,
   gates, and exceptions.
2. Read [autonomous operation](autonomous-operation.md) for loops, scheduled
   runs, or long unattended work.
3. Add every rulebook selected by the routing table before changing artifacts.
4. Follow the linked canonical rulebooks rather than duplicating their policy in
   a consumer configuration.

## Routing table

| Situation | Read |
|---|---|
| Any development work | [universal rules](00-universal.md) |
| Non-trivial or uncertain risk | [risk and exceptions](risk-and-exceptions.md) |
| End-to-end work and phase gates | [development loop](development-loop.md) |
| Reduced human oversight | [autonomous operation](autonomous-operation.md) |
| Delegation or capability selection | [delegation](delegation.md) |
| Repository edits or version control | [workspace and VCS](workspace-and-vcs.md) |
| A capability may already exist | [reuse first](reuse-first.md) |
| Unfamiliar source or broad change | [exploration](exploration.md) |
| Scope boundaries | [scope discipline](scope-discipline.md) |
| Success claims or checkpoints | [verification](verification.md) |
| New behavior or component | [feature development](feature-dev.md) |
| A defect | [bugfix](bugfix.md) |
| Structure without intended behavior change | [refactoring](refactoring.md) |
| A diff or change proposal | [code review](code-review.md) |
| Tests or test maintenance | [testing](testing.md) |
| A structural decision | [architecture](architecture.md) |
| APIs, CLIs, formats, events, or external consumers | [API and compatibility](api-and-compatibility.md) |
| Schema, migration, or data change | [data and migrations](data-and-migrations.md) |
| Release or deployment | [release and rollback](release-and-rollback.md) |
| Network, background, concurrency, or operational failure path | [observability and resilience](observability-and-resilience.md) |
| Configuration, credentials, build inputs, or dependencies | [config, secrets, and supply chain](config-secrets-and-supply-chain.md) |
| Security, authorization, or trust boundaries | [security](security.md) |
| Optimization or plausible performance impact | [performance](performance.md) |
| Delete, overwrite, force operation, or irreversible transform | [destructive operations](destructive-operations.md) |
| Docs, comments, decisions, or lifecycle | [documentation](documentation.md) |

## Supporting references

- [Local agent stack profile](profiles/local-agent-stack.md) maps durable roles
  to optional local capabilities. It is not policy.
- [Python testing patterns](catalogs/python-testing-patterns.md) and
  [schema-migration testing patterns](catalogs/schema-migration-testing-patterns.md)
  are optional catalogs.
- [Rulebook architecture](docs/dev-rules-architecture.md) explains ownership,
  enforcement, and document lifecycle.

## Wiring into a project

```text
Development rules: before non-trivial work, read /home/cam/dev-rules/00-universal.md,
/home/cam/dev-rules/risk-and-exceptions.md, and the task rulebooks from the
/home/cam/dev-rules/README.md routing table. Use the linked universal and risk
rulebooks as the canonical source for precedence, gates, and exceptions.
```

## Maintenance

Use [rules-manifest.json](rules-manifest.json) and
[`scripts/check_rules.py`](scripts/check_rules.py) to keep active documents,
links, ownership, and routing consistent. Add a rule only when a recurring need
has evidence; archive completed plans after promoting durable knowledge.
