# Pre-release Codebase Audit

> A bounded, deliberate review of a whole codebase before a release cut. This
> rulebook is a thin process wrapper around established frameworks; use their
> content rather than re-deriving it here.

## When this applies

Invoke it on purpose, at a point where the whole system needs one honest verdict:

- before a first public, GA, or otherwise consequential release;
- at a major architecture milestone, such as a new execution model, storage
  layer, or trust boundary;
- at the start of a compliance or certification cycle;
- after a significant security incident, to find siblings of the root cause.

It does not replace per-change review. [Security](security.md) and
[code review](code-review.md) keep governing every change before and after the
audit.

| | Routine review | Comprehensive audit |
|---|---|---|
| Trigger | Every change | A release cut or milestone, deliberately chosen |
| Unit | A diff and its context | The codebase at one frozen revision |
| Question | Is this change safe to merge? | Is this system safe to release as claimed? |
| Output | A verdict on the change | One findings report and a remediation handoff |
| Changes code | Yes, through remediation | No. Report only; fixes run as separate efforts |

## Scope

**Owns:** when to run a comprehensive audit, its sizing, its phase sequence, and
the shape of its report and handoff. **Does not own:** finding severity labels
([code review](code-review.md)), change risk and gates
([risk and exceptions](risk-and-exceptions.md)), how fixes are built
([development loop](development-loop.md)), or the release decision
([release and rollback](release-and-rollback.md)).

## Established frameworks

Use these as the working material. Link to them in the report instead of
restating their content.

- **Review checklist:** the [OWASP Code Review Guide](https://owasp.org/www-project-code-review-guide/).
  For a web or API service that needs verifiable requirements, the
  [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
  is the closer fit. For other stacks, use the closest maintained checklist for
  the language or platform. The [CWE Top 25](https://cwe.mitre.org/top25/) is
  prioritization input, not a complete checklist.
- **Threat modeling:** STRIDE to identify threats against the system model and
  its trust boundaries. When the exercise must be driven by business impact,
  such as regulated data or a compliance scope, run PASTA at system scope; it is
  not a per-boundary substitute for STRIDE. The
  [OWASP Threat Modeling Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html)
  supplies the four framing questions.
- **Automated analysis:** general SAST (CodeQL, Semgrep with maintained
  rulesets), language-specific linters and security scanners, dependency
  vulnerability scanners, and secret scanners. See AUTOMATED PASS for categories.

Record the version, edition, or retrieval date of each checklist and framework
used. For ASVS, cite version-qualified requirement IDs.

## Sizing

Pick a size in SCOPE and record it. Size sets audit breadth only. Review gates
come from the risk level recorded under [risk and exceptions](risk-and-exceptions.md),
and size never lowers them. Scale breadth down freely; scale it up only for a
stated reason such as cryptography, multi-tenancy, untrusted input at scale, or
regulated data.

| Size | Typical codebase | Process |
|---|---|---|
| Small | Under ~10k lines, one component, library or tool | Half-page frame. Automated pass. One reviewer works through the checklist and an abbreviated STRIDE pass across each identified trust boundary. Report can be a few pages. |
| Medium | ~10k to ~100k lines, a few components or one service | One-page frame. Automated pass. Two to four concern-based review lanes, one STRIDE pass per trust boundary. |
| Large | Over ~100k lines, several trust boundaries, native or unsafe code, or crypto | One-page frame. Automated pass. Parallel lanes by concern, STRIDE per boundary, PASTA at system scope if business-risk driven. |

Line count is a hint, not the rule. A 5k-line cryptographic library can warrant
a Large threat-model pass on its core while staying Small everywhere else.

Set a budget (time, tokens, or reviewer-days) in SCOPE. Framing and planning
should take a small fraction of it, roughly a tenth. The rest goes to reading
code, gathering evidence, and writing findings.

## Flow

```text
SCOPE -> AUTOMATED PASS -> MANUAL REVIEW -> RATE -> REPORT -> HANDOFF
```

The audit is report-only. It freezes one revision, reads it, and produces
findings. It never edits the audited source.

## Phases

### SCOPE

Write one page, at most. It is the audit plan; there is no separate methodology
document. Record:

- the revision (an immutable identifier such as a commit hash) and the release
  phase it is being audited for;
- the boundary: which components, packages, and directories are in, which are
  out, and why, at directory or component granularity;
- the deployment model: library, CLI, self-hosted service, or SaaS; single- or
  multi-tenant; who runs it and with what privileges;
- the trust boundaries, the assets worth protecting, and the primary adversary;
- prior reviews and open findings to reconcile against;
- the risk level, required gates, and approver under
  [risk and exceptions](risk-and-exceptions.md);
- the size, the budget, the reviewers, and the stop condition.

Exit with the frame agreed by the owner. If it cannot be agreed within the
review bound in Rules, the scope is too broad; narrow it.

### AUTOMATED PASS

After SCOPE, and only as much architecture reading as tool configuration
needs, run tools before deep lane review so that reviewers do not spend
attention on what a tool already finds. Choose what fits the stack:

- general SAST: CodeQL or Semgrep;
- language linters and security scanners, for example Ruff's flake8-bandit
  (`S`) rules or Bandit for Python, clippy plus `cargo audit` or `cargo deny` for Rust, gosec
  and govulncheck for Go, ESLint security plugins for JavaScript;
- dependency vulnerability scanning: OSV-Scanner or the ecosystem's audit tool;
- secret scanning over the tree and history: gitleaks or trufflehog;
- infrastructure, container, and workflow scanners where those files ship;
- the project's own required gates (tests, type checks, lint) as the baseline.

Triage every tool result as confirmed, false positive, or accepted with reason
before it becomes a finding. Record tool names, versions, rulesets, and
commands. Exit with triaged output and a baseline-gate record. A failing
baseline gate is a finding, not a reason to stop the audit.

### MANUAL REVIEW

Organize reading by the chosen checklist's categories and by the threat model,
not by a custom coverage scheme:

1. Draw the data flow at the level of components and trust boundaries.
2. Run STRIDE across each identified trust boundary to produce abuse cases.
   When business-impact analysis is required, also run PASTA at system scope.
3. Assign concern-based lanes, such as authentication and authorization,
   input handling, cryptography and secrets, data integrity, concurrency and
   resource limits, packaging and install, and documented claims versus actual
   behavior. Each lane works through the matching checklist categories and the
   abuse cases that land in it.
4. Check that tests assert the safe behavior. A test that pins unsafe behavior
   keeps a suite green while a finding stays live.
5. Support each BLOCKER or HIGH candidate with the strongest safe evidence
   before rating it: a reproduction when safe and proportionate, otherwise a
   written static argument or compensating evidence with the gap stated, as
   [verification](verification.md) allows.

Each lane returns findings with evidence, plus a short statement of what it read
and what it did not. See [delegation](delegation.md) when lanes are delegated.
Exit with lane notes and candidate findings.

### RATE

Apply the [code review](code-review.md) severity definitions (BLOCKER, HIGH,
MEDIUM, LOW) with confidence. Record reachability as evidence by answering one
plain question against the frame: can the primary adversary reach this across a
stated trust boundary in the default configuration? Note any non-default
configuration, operator action, or out-of-scope boundary the answer depends on,
and state any condition that would change the rating in one sentence, for
example "HIGH; BLOCKER if a less-trusted user can select the file".

Do not build a reachability matrix or apply automatic downgrades. When a rating
is contested, write down the assumption that decides it and let the owner rule.

### REPORT

Produce a single findings report containing:

- a header with revision, date, release phase, scope, and change boundary;
- an audit recommendation: proceed to the release decision, or do not proceed
  until named blockers are resolved, stated in plain words;
- a findings table with ID, severity, problem, and required outcome;
- a detail section for each finding with evidence (a safe reproduction,
  written static argument, or compensating evidence with the gap stated),
  impact, reachability, and remediation direction (not a finished fix);
- strengths worth preserving;
- reconciliation against prior findings;
- a verification record with tools, versions, commands, and results;
- coverage limits, stated in prose.

Obtain the independent review the recorded risk level requires, at minimum one
adversarial check of the draft report aimed at wrong ratings, unsupported
claims, and obvious gaps. See [verification](verification.md) for evidence
wording. Exit with the report
published where the project keeps durable records.

### HANDOFF

Convert findings into owned work. Each BLOCKER and HIGH, or a tight group of
related ones, becomes its own effort under the
[development loop](development-loop.md), with risk classified by
[risk and exceptions](risk-and-exceptions.md). MEDIUM and LOW findings go to
the backlog with an owner. The release decision belongs to the
[release and rollback](release-and-rollback.md) owner, using the report as
evidence, not to the audit. A fix is verified against the original
reproduction or evidence before its finding is closed.

## Rules

DO spend most of the budget running the audit, not designing it.

DO audit one frozen revision and record later changes as out of scope.

DO bound review of the audit plan to the one-page frame and this rulebook, with
at most two rounds before the audit starts. If a round finds that the method
itself needs restructuring, or costs more than the one before it, narrow or
split the scope instead of expanding the method.

DON'T invent a bespoke coverage-accounting, reachability, or evidence system when
a checklist and a prose coverage-limits section answer the same question.

DON'T build tooling to audit the audit, such as manifest generators, close
checkers, or execution wrappers, unless the scope already contains an equivalent
recurring need.

DON'T fix findings inside the audit, and don't let fixes widen the audit.

DON'T apply the Large process to a Small codebase because the audit feels
important.

## Red flags

- "The plan needs one more round to close the method gaps." Stop and simplify.
- "We need a ledger to prove every file was read." Use the checklist and state
  coverage limits.
- "The review found gaps in our rubric, not in the code." The rubric is too big.
- "Tools will be noisy, so skip them." Triage the noise; manual time costs more.
- "The suite is green, so the behavior is safe." Check what the tests assert.

## Done when

- [ ] A one-page frame records revision, boundary, deployment model, adversary,
      risk level and gates, size, and budget.
- [ ] Automated analysis ran and its results were triaged before they became
      findings.
- [ ] Manual review followed a named checklist and threat-modeling framework,
      with each version, edition, or retrieval date recorded.
- [ ] Every BLOCKER and HIGH has the strongest safe evidence, with any gap stated.
- [ ] One report states the recommendation, ranked findings, verification
      record, and coverage limits, and it received the independent review its
      risk level requires.
- [ ] Each BLOCKER and HIGH has an owner and a separate remediation effort.
