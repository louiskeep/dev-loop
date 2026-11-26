"""Decide whether a new slice head keeps the old head's evidence.

The one place that runs the git questions. A head change keeps evidence only when the new
commit is a plain merge of the freshly fetched base branch into the old head and the base
side touches none of the files the slice changed. Anything else is a reset, as before.

Test evidence is stricter. A clean merge of disjoint files can still break a test (main
renames a function the slice calls), so a test GO carries only when everything the base
brought in is documentation. Reviews of the slice's own diff carry either way.
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
    """Fetch the base and name its remote-tracking ref, or None when the fetch failed."""
    if _git(path, "fetch", "-q", "origin", base) is None:
        return None
    return f"origin/{base}"


# Files that no test runs. Anything else the base brings in makes the test GO stale.
DOC_SUFFIXES = (".md", ".rst")


def _is_doc(name):
    return name.endswith(DOC_SUFFIXES) or name.startswith("docs/")


def tests_carry(path, old, new):
    """True when the base side of the merge `new` brought in documentation only."""
    incoming = _files(path, f"{old}..{new}")
    return incoming is not None and all(_is_doc(f) for f in incoming)


def carry_reason(path, base, old, new):
    """None when evidence on `old` may carry to `new`, else the reason it may not."""
    if not old or old == new:
        return "no head change"
    parents = (_git(path, "rev-list", "--parents", "-n", "1", new) or "").split()[1:]
    if len(parents) != 2 or parents[0] != old:
        return "not a merge of the base into the old head"
    ref = base_ref(path, base)
    if ref is None:
        return "could not fetch the base"
    if _git(path, "merge-base", "--is-ancestor", parents[1], ref) is None:
        return "the second parent is not on the base branch"
    # The merge commit must hold nothing beyond a clean merge: redo it and compare trees.
    redone = (_git(path, "merge-tree", "--write-tree", old, parents[1]) or "").split()
    actual = (_git(path, "rev-parse", f"{new}^{{tree}}") or "").strip()
    if not redone or redone[0] != actual:
        return "the merge commit holds more than a clean merge"
    # The ancestor of the two real merge parents. The moving base tip may already contain
    # the old head, which would make the slice's own file set look empty.
    mb = (_git(path, "merge-base", old, parents[1]) or "").strip()
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
