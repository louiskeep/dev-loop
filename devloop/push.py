"""Push a headless writer's commits once its worker has exited.

The worker cannot push itself: a project-level PreToolUse hook may block `git push` in a
worker's session. The loop pushes instead, as a plain git process no hook sees. The push
is fast-forward only, targets the slice branch only, and never touches a base branch.
"""

import os
import subprocess

TIMEOUT_S = 120
WRITER_KINDS = ("builder", "codex-builder")


def _git(worktree, *args):
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=TIMEOUT_S,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def push_slice_branch(req):
    """Push the worktree HEAD to the slice branch. Returns (head, failure_reason).

    failure_reason is None when the head is on the remote (pushed now or already there).
    """
    wt, branch = req.get("worktree"), req.get("branch")
    if not wt or not branch:
        return None, "no worktree or slice branch to push"
    bases = {b for b in (req.get("bases") or "").split(",") if b}
    if branch in bases or branch in ("main", "master"):
        return None, f"refused: {branch} is a base branch"
    try:
        head = _git(wt, "rev-parse", "HEAD")
        if head.returncode != 0:
            return None, "cannot read the worktree head"
        local = head.stdout.strip()
        cur = _git(wt, "symbolic-ref", "--short", "-q", "HEAD")
        if cur.stdout.strip() != branch:
            return None, f"worktree is not on the slice branch {branch}"
        ref = f"refs/heads/{branch}"
        ls = _git(wt, "ls-remote", "--heads", "origin", ref)
        if ls.returncode != 0:
            return None, "remote unreachable"
        remote = ls.stdout.split()[0] if ls.stdout.split() else ""
        if remote == local:
            return local, None
        if remote:
            if _git(wt, "fetch", "-q", "origin", ref).returncode != 0:
                return None, "cannot fetch the remote branch"
            anc = _git(wt, "merge-base", "--is-ancestor", remote, local)
            if anc.returncode != 0:
                return None, "remote branch has commits not in the worktree"
        pushed = _git(wt, "push", "-q", "origin", f"HEAD:{ref}")
        if pushed.returncode != 0:
            return None, "push rejected or failed"
        return local, None
    except (OSError, subprocess.SubprocessError):
        return None, "git failed"
