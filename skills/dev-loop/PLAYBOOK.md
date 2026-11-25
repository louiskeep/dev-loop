# Dev loop playbook

The tick reads this page on every run. A project may add its own `playbook_doc`. That
file can add to this page or tighten it. It never loosens it.

Tags: `[code]` means `devloop-state` checks the item and the snapshot shows it as met or
missing. The tag text matches the checklist name in the snapshot. `[judgment]` means the
tick decides and writes its reason in `next_action`.

## Done state per phase

### FRAME

- risk is R0, R1 or R2 (R3 waits for the owner) [code]
- rulebooks listed [code]
- the row's goal, boundaries and definition of done are written down [judgment]
- existing tools and code were checked before anything custom is planned [judgment]

An R3 slice goes to HELD with a question. Only the owner releases it.

### PLAN

- plan file exists [code]
- plan names the Rules consulted [code]
- plan review GO on the current plan, in roster order [code]
- the plan states behavior, failure modes and acceptance tests [judgment]
- the plan states what it promises, what it does not, and its expected size [judgment]
- a plan that adds files has a placement table; one that adds a rule with many sites
  names the choke point and the mutation that turns its guard red [judgment]

### DEVELOP

- branch set [code]
- head set [code]
- applied builder run DONE on head [code]
- tests were written before the code they cover [judgment]
- lint and format are clean, and commits follow the project's message rule [judgment]
- the changelog has an entry when the repo keeps one [judgment]

### SELF_CHECK

- selfcheck GO on head [code]
- the tick read the diff: placement, duplicate mechanisms, size against the plan,
  changelog, sentries [judgment]
- every class in the recurring defects catalog was checked on this diff [judgment]

Record the read with `devloop-state selfcheck KEY GO --head SHA`. The head must be the
slice head.

### VERIFY

- t1 GO on head [code]
- mutations ran on core logic, and uncovered parts are stated [judgment]
- the test scope matches the change: impact selection, plus a full suite only when a
  trigger from the testing rules applies (set `full_suite_required`) [judgment]

### REVIEW

- code review GO on head, one entry per reviewer in the roster [code]
- t2 GO on head, when the project configures a t2 level [code]
- full suite GO on head, when `full_suite_required` is set [code]
- approved plan unchanged [code]
- a fix round that changed only tests, under about 50 lines, was checked from the diff
  instead of re-reviewed; any source change goes back to review [judgment]

### REMEDIATE

- each finding has a root-cause fix and a regression test [judgment]
- the slice returns through SELF_CHECK and VERIFY before it is reviewed again [judgment]

The third review round on one artifact parks the slice. Only the owner lifts that park.

### GATE

- approved plan unchanged [code]
- selfcheck GO on head [code]
- t1 GO on head [code]
- code review GO on head, for every reviewer in the roster [code]
- the merge command also asks GitHub: the base branch, the head, the required checks and
  the changelog entry. It refuses with the reason and merges nothing.
- the full suite was needed or not, per the testing triggers [judgment]
- the PR is rebased on the base branch when the project requires it [judgment]

Merge only with `devloop-merge PROJECT KEY`. A direct `gh pr merge` is blocked.

### DOCUMENT

- docs GO on head [code]
- the close-out list below is done [judgment]

### DONE

Nothing is left to do. The row closes when its last roadmap slice is DONE.

### PARKED

A parked slice names its reason. `round-3` and `failed-twice` wait for the owner. Other
parks resume to the recorded phase.

### HELD

An owner question is open. Do not work around it.

## Scenarios

| What happened | What to do | Command |
|---|---|---|
| A worker FAILED once | Dispatch the same run again with the first run as `--retry-of`. | `devloop-dispatch P KIND KEY --brief F --retry-of RUN` |
| A worker FAILED twice on one head | The slice is parked. Read the log, record the cause as a problem, ask the owner. | `devloop-state P problem KEY ...` |
| A worker hung or hit its deadline | It counts as FAILED. Check the log for a permission prompt or a loop. | `devloop-state P applied RUN` |
| The run ended with reason capped | No failure counts. Wait for `cap-until`, then dispatch again. | none until the cap clears |
| A test run ended with an infra code | No failure counts. The slot stays held until the node is checked. | `devloop-state P release RUN` |
| Dennis or Codex said NO-GO | Move the slice to REMEDIATE. The round counter goes up. | `devloop-state P slice move KEY REMEDIATE --reason ...` |
| A reviewer said GO-WITH-FIXES | It is not a GO. Treat it as NO-GO and fix the findings. | same as NO-GO |
| Plan round cap reached for Dennis | Dispatch Codex next. It repairs the plan with `codex-fix`. | `devloop-dispatch P codex-fix KEY --reviewer codex --brief F` |
| Round 3 on one artifact | The slice is parked. Ask the owner for a plan fix or a decision. | `devloop-state P ask KEY ... --wait` |
| Main is red | Stop merging. Fix main first. Park other merge work. | `devloop-state P problem main-red ...` |
| The PR is behind its base or conflicts | Rebase in the worktree, push, set the new head, return to SELF_CHECK. No round counts. | `devloop-state P slice move KEY SELF_CHECK --reason rebase` |
| Merge refused | Read the reason. Fix the named check. Do not retry blindly. | `devloop-merge P KEY` |
| Merge state unknown | Do not merge again. Run the merge command once more only to read the state. | `devloop-merge P KEY` |
| A slice has no live run and is older than 2 hours | Act on it now: dispatch the next step or park it with a named blocker. | see the snapshot |
| A node is idle and work is queued | Dispatch the queued work to fill the slot. | `devloop-dispatch ...` |
| Memory is low | Dispatch nothing new. Wait for the next tick. | none |
| An R3 action is needed | Ask the owner and wait. Never act on it. | `devloop-state P ask KEY ... --wait` |
| The owner overruled a default | Read the answer. Adjust the slice. | `devloop-state P answers-sync` |
| A repair conflict is reported | The plan changed during the repair. Nothing was installed. Dispatch the repair again. | `devloop-dispatch P codex-fix ...` |
| A test looks flaky | Rerun it once on the same head. If it differs, record a problem. Never edit the test to pass. | `devloop-state P problem flaky-... ...` |
| An imported slice shows a mismatch | Re-check the head and the plan. Run one builder pass or re-import with `--plan-approved`. | `devloop-state P slice show KEY` |

## Worker briefs

Every brief carries: the goal and the row, the risk and the rulebook paths, the plan path
and the acceptance tests it defines, the files in scope, the test depth to run, where to
run tests, the recurring-defects check, the standing rules, the lessons for the area,
what not to do, and the hand-back contract. The sections below add what each role needs.

### planner

Write the plan at the given path. Open with the rules you read. State behavior, failure
modes, acceptance tests, placement and size. Research established solutions before you
design. End with a result file.

### builder

Work only in the given worktree and branch. Write the named tests first and run them red.
Run T0 per edit and T1 per commit. Lint and format before each commit. Do not weaken an
agreed test. Do not push to the base branch. Hand back the head SHA you finished on.

### dennis

Review the exact head in the private checkout against the plan, the evidence and the
rules. List every finding in the first pass. Check each class in the recurring defects
catalog by name. Use the architecture and clean-code lenses. Do not edit files. End with
the verdict line.

### codex-review

Review the exact head in read-only mode. State what you checked and what you did not.
Findings need a way the failure happens in real use. End with the verdict line.

### codex-fix

Repair the plan copy you are given, and only that. Keep the plan's guarantees. Run your
own review until it is clean. End with the self-verdict line.

### test

Run the named command on the exact head. Report the failing IDs and nothing else. A node
or transport failure is not a test result.

### docs

Bring the docs back in step with what shipped. Do not change behavior or tests. Write
the merge note in plain language: no SHAs, paths or data values.

### duty

Run the scheduled command and exit with its code. Do not change slice state.

## Testing per phase and risk

The project's own test policy is in the file named by `tests` in the project config.
These are the minimum shapes.

| Phase | R0 | R1 | R2 |
|---|---|---|---|
| DEVELOP | T0 per edit, T1 per commit | same | same |
| VERIFY | T1 on the head | T1, mutations on core logic | T1, mutations, risk cells |
| REVIEW | one reviewer re-runs T1 | the reviewer re-runs T1 and its own mutations | two reviewers, each on the exact head |
| GATE | T1 and T2 on the head | T1 and T2 | T1 and T2, plus the full suite only on a trigger |

The full suite runs once on the merge head, never twice on one head. Claim "no new
failing id against the baseline", not "suite green". A new head needs new evidence.

## Close-out

- The merge was confirmed from GitHub before any board or state change.
- The board shows the slice merged.
- The merge note is posted, in plain language.
- Docs match what shipped, and the changelog entry is in.
- The row is closed when its last slice is DONE.
- Problems and lessons from this slice are recorded.
- The worktree is cleaned up only when it holds no work that is not pushed.
