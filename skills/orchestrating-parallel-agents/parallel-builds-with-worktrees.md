# Parallel builds with worktrees

When two or more builder agents must write to the same repo at once, give each its own git worktree so
their edits never touch the same working tree. This is the isolation the hand-off contract calls for,
made concrete. For read-only research fan-out, skip all of this: those agents write nothing. Worktrees
are for concurrent writers.

## Setup

From the repo root, one worktree and branch per concurrent builder:

```bash
git worktree add ../wt-taskA -b build/taskA
git worktree add ../wt-taskB -b build/taskB
```

Each agent works only in its own path on its own branch. Disjoint working trees mean no write
collision even if the file sets overlap.

## Dispatch

In each agent's prompt, name its worktree path and branch and forbid leaving it, on top of the usual
contract (goal, scope, constraints, success criteria, pointers, return shape):

- "Work only in `../wt-taskA`. Commit to `build/taskA`. Do not touch any other path. Hand back your
  branch name and a summary."

## Integrate

When agents return, merge onto your own branch one at a time, so any conflict surfaces under your eye
rather than inside an agent:

```bash
git merge build/taskA
git merge build/taskB   # resolve any conflict here yourself
```

Then run the integrated whole through SELF-CHECK, VERIFY, REVIEW, GATE. The gate reviews the merged
commit, not the per-agent branches.

## Clean up

```bash
git worktree remove ../wt-taskA
git worktree remove ../wt-taskB
```
