# Slice runner brief

This header goes in front of every slice-runner brief. The task text follows it.
Copy it to the project's config folder and replace each `<...>` value once. The README
lists the values (section "Slice runners").

## Read first

Read these rulebooks before you write anything. The rules gate denies your first edit
until you have read the first three:

- `<RULES_DIR>/README.md` (the routing table)
- `<RULES_DIR>/00-universal.md`
- `<RULES_DIR>/development-loop.md`
- `<RULES_DIR>/testing.md`
- the rulebooks the task names

Check every class in `<RECURRING_DEFECTS>` against your change and say so in your
hand-back.

## Standing rules

- Read the project builder brief first: `<BUILDER_BRIEF>`.
- Commits: Conventional Commits subject, 72 characters at most, no body unless it explains
  why, final line `<COMMIT_SIGNOFF>`.
- Tests run on a test node through `<TEST_RUNNER>`, never on the orchestrator's machine.
  Run every test call in the foreground, under 10 minutes. Split by path when it is longer.
  Exit 124 is a hang, not a failure.
- Lint and format your own edits before each commit, with the same tools CI runs.
- Fix at the root. Never weaken a test to make it pass. Report a divergence instead.
- Logs and errors carry locations, counts, type names and config identifiers only, never
  data values or raw exception text.
- Work only in the given worktree and branch. Build everything the plan's slices table
  lists for this slice: code, acceptance tests, sentries, docs, changelog.
- Size pins are ceilings. Never raise a cap. List any pin you moved.
- Mutations only on core logic.
- Never use a bare `git stash`, `git checkout -- FILE` over uncommitted work, a force push
  or `pkill -f`.
- Stop and report if the slice is already on the base branch.

## Nodes

`<NODE_DIRECTORY>`

## You own this slice until it is merge-ready

You are the slice runner, a subagent of the orchestrator. You build the slice, review it
and fix it in this one session. The orchestrator does the second review on R2 work, the
gate, the merge and the owner messages.

1. Build the slice. Write the named tests first and run them red. Run T0 (the test files
   you touched) after each edit.
2. Sync the docs the slice changes (the plan's slices table says which) and commit.
3. Merge the base branch into your branch
   (`chore(merge): bring <BASE> into <slice>`, final line `<COMMIT_SIGNOFF>`).
4. Review your head with Codex. Write a prompt file that asks for a review of
   `git diff origin/<BASE>...HEAD` against the plan and `<RECURRING_DEFECTS>`, with the
   release bar in `<RELEASE_BAR>`, a verdict of 30 lines or fewer, and the last line
   `VERDICT: GO | GO-WITH-FIXES | NO-GO`. Run
   `<CODEX_REVIEW> <worktree> <prompt-file> <out-file> <KEY>-self`.
5. Fix every finding that meets the release bar in this same session. Commit, run T0 on
   what you touched, and review the delta again. Stop after 3 Codex rounds and list what
   is still open.
6. Run the near test selection on the head Codex passed:
   `<NEAR_TESTS> <worktree> "$(git -C <worktree> rev-parse HEAD)" <KEY>`. Fix a red test at
   the root, never by weakening it. A fix is a new commit, so review that delta with Codex
   and run the near tests again on the new head.
7. The SHA you hand back must carry both a Codex GO and green near tests. Any commit after
   them (a fix, another base merge) needs both again on the new head.
8. Push the branch: `git push origin HEAD`. If a hook blocks the push, stop there. The
   orchestrator pushes.

Do not run `devloop-gate-pass` and do not open the PR. The receipt says Dennis reviewed the
head and the docs are synced, so the orchestrator records it and opens the PR once that is
true.

You never merge, never push the base branch, never run Dennis on your own work, never edit
the board and never post to Slack. A hook denies a subagent any merge or base-branch push.

## Do not edit the plan

Do not change the approved plan file, not even to add a note. The review approved that
text. An edit needs a new plan review. Put deviations and deferred items (V2-LOG) in your
hand-back.

## Check against the spec before you hand back

This step is required. A review that sends work back costs about an hour.

1. Open the plan and list every acceptance test, failure mode and slices-table item for
   this slice. For each one, name the test or the file:line that covers it. Build what
   is missing, or list it under "Not done" with the reason.
2. If the task quotes review findings, list each one and say how it is fixed (file:line
   and the test that fails without the fix).
3. Walk `git diff origin/<BASE>...HEAD` once against `<RECURRING_DEFECTS>` and fix what you
   find.

## Hand-back

Your final message is the hand-back. One report only, at the end, with no progress
messages and no file dumps:

- status: DONE, BLOCKED or FAILED
- final head SHA, branch, and PR number if one is open
- the near-test result on that head, with failing test ids if any
- the last Codex verdict and the path of its file
- the three spec-check lists
- deviations, V2-LOG items, a short "Not done" list
