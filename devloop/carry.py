"""Decide whether a new slice head keeps the old head's evidence.

The one place that runs the git questions. A head change keeps evidence only when the new
commit is a plain merge of the base branch into the old head and the base side touches
none of the files the slice changed. Anything else is a reset, as before.
"""

import subprocess

TIMEOUT = 60


def _git(path, *args):
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _files(path, *rng):
    out = _git(path, "diff", "--name-only", "--no-renames", *rng)
    return None if out is None else {ln for ln in out.splitlines() if ln}


def base_ref(path, base):
    """Fetch the base (best effort) and name its remote-tracking ref."""
    _git(path, "fetch", "-q", "origin", base)
    return f"origin/{base}"


def carry_reason(path, base, old, new):
    """None when evidence on `old` may carry to `new`, else the reason it may not."""
    if not old or old == new:
        return "no head change"
    parents = (_git(path, "rev-list", "--parents", "-n", "1", new) or "").split()[1:]
    if len(parents) != 2 or parents[0] != old:
        return "not a merge of the base into the old head"
    ref = base_ref(path, base)
    if _git(path, "merge-base", "--is-ancestor", parents[1], ref) is None:
        return "the second parent is not on the base branch"
    # The merge commit must hold nothing beyond a clean merge: redo it and compare trees.
    redone = (_git(path, "merge-tree", "--write-tree", old, parents[1]) or "").split()
    actual = (_git(path, "rev-parse", f"{new}^{{tree}}") or "").strip()
    if not redone or redone[0] != actual:
        return "the merge commit holds more than a clean merge"
    mb = (_git(path, "merge-base", old, ref) or "").strip()
    if not mb:
        return "no merge base"
    own = _files(path, f"{mb}..{old}")
    incoming = _files(path, f"{old}..{new}")
    if own is None or incoming is None:
        return "could not read the diffs"
    clash = sorted(own & incoming)
    if clash:
        return f"the base side touches a slice file ({clash[0]})"
    return None
