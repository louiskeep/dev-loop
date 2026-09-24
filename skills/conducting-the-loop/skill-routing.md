# Skill routing

The conductor loads a technique skill on demand at each phase rather than
carrying all of them in context. This table maps the loop phases to the skills
worth reaching for. It is the skill companion to `role-matrix.md` (which maps
the agent tier per phase): the role matrix decides *who* runs a phase, this
decides *which skill* they load.

Scope: this is curated to the skills a coding loop actually reaches for. The
full catalog is much larger and carries domain skills (SEO, marketing, media,
integrations) plus a large set of structured-thinking modules. Invoke any of
those opportunistically when a task matches, per `superpowers:using-superpowers`;
do not force one into the loop where it does not fit.

Load a phase's skill only when that phase's work is non-trivial. Plugin skills
(`orchestrating-parallel-agents`, `running-the-gate`, `verifying-changed-units`,
`documenting-the-slice`) are listed where they fire. `superpowers:` and gstack
skills are marked; a bare name is a gstack skill.

| Phase | Reach for | When |
|---|---|---|
| FRAME | `superpowers:brainstorming` | Intent or requirements are unclear before creative or feature work. Run it before entering plan mode. |
| FRAME | `graphify`, `qmd`, `investigate` | Locating code, answering an architecture or reachability question, or root-causing a reported defect. `investigate` for the defect path. |
| PLAN | `superpowers:writing-plans`; `spec`; `plan-eng-review` / `plan-ceo-review` / `plan-design-review` / `autoplan` | Authoring a plan or spec, then reviewing it before build. Plans and specs are top-tier authored (see `role-matrix.md`). |
| PLAN | reference: `system-design`, `clean-architecture`, `domain-driven-design`, `ddia-systems` | A structural decision needs an established pattern. Survey before inventing. |
| DEVELOP | `superpowers:test-driven-development`; `orchestrating-parallel-agents` (plugin) / `superpowers:subagent-driven-development`; `superpowers:using-git-worktrees` | Implementing approved work. Parallel independent tasks go to bounded builder subagents, one writer per worktree. |
| DEVELOP | `refactoring-patterns`, `working-with-legacy-code`; domain skills (`frontend-design`, `dataviz`, `impeccable`) | Structure-only changes, or UI and visualization work that matches a domain skill. |
| SELF-CHECK | `simplify`; `code-review` (self, low effort); `avoid-ai-writing` / `humanizer` | Before handing to the gate: simplify the diff and sweep new comments, docs, and the PR body for AI-isms. |
| VERIFY | `verifying-changed-units` (plugin); `superpowers:verification-before-completion` | Measure coverage and mutation on the changed units. Never claim done on a stale or skipped check. |
| REVIEW / GATE | `running-the-gate` (plugin); `superpowers:requesting-code-review`; dennis then `codex`; `security-review` | Adversarial review before merge: dennis first, Codex as the final cross-model gate. Add `security-review` when a trust boundary changed. |
| DOCUMENT | `documenting-the-slice` (plugin); `document-release` / `document-generate`; `changelog-updates` | A slice is not done until the roadmap and shipped-log are current. barry for mechanical updates, top tier when docs need judgment. |
| REMEDIATE | `superpowers:systematic-debugging`; `investigate` | A gate finding or bug. Fix at the root cause, trace with `graphify` first, never symptom-patch. |
| SHIP | `ship` / `land-and-deploy` | Merge the base, run tests, review the diff, bump VERSION and CHANGELOG, open the PR. Irreversible steps gate on human authorization first. |
| ACROSS PHASES | `context-save` / `context-restore`; `council` | Persist or resume working context between sessions. `council` only on large, high-stakes jobs, and only when invoked. |

## Notes

- Priority when several apply: process skills first (`brainstorming`,
  `systematic-debugging` set the approach), then the implementation or domain
  skill carries it out.
- Plans, specs, and guides are authored by the top tier, never a builder tier.
- The R2/R3 gate is always dennis then Codex. Never skip it because the branch
  is green.
- Apply the writing-quality skills to any human-facing prose: comments, docs,
  READMEs, and commit and PR bodies, not only standalone documents.

## See also

- `role-matrix.md` for which agent tier runs each phase.
- `rules/development-loop.md` and `rules/delegation.md` for the canonical phase
  and delegation policy this routing serves.
