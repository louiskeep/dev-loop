"""Which open slices in different rows are changing the same files.

The tick reads this before it starts a builder. It is advice, never a block: the
orchestrator decides the order and says so in next_action.
"""

import fnmatch
import re
from itertools import combinations
from pathlib import Path

from . import carry, merge, slices
from . import project as projmod

SHOWN = 5
_PATH = re.compile(r"[\w.@+-]+(?:/[\w.@+-]+)+|[\w-]+\.[A-Za-z0-9]{1,5}")


def _branch_files(path, base, branch):
    ref = carry.base_ref(path, base)
    if ref is None:
        return None
    for cand in (branch, f"origin/{branch}"):
        mb = (carry._git(path, "merge-base", ref, cand) or "").strip()
        if mb:
            return carry._files(path, f"{mb}..{cand}")
    return None


def _worktree_files(path):
    """Staged, unstaged and untracked paths in the slice's own worktree, None on failure.

    Renames are off so a moved file reports both its old and its new path.
    """
    out = carry._git(
        path, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all"
    )
    if out is None:
        return None
    return {e[3:] for e in out.split("\0") if len(e) > 3}


def _cells(ln):
    return [c.strip() for c in ln.strip().strip("|").split("|")]


_TARGET = re.compile(r"[\w.@+/*-]+")


def _resolve(tok, folder, repo):
    """A File cell entry as a repo path, joined to the row's directory when it is relative."""
    if not folder or tok.startswith(folder + "/"):
        return tok
    if "/" in tok and repo and (Path(repo) / tok).parent.is_dir():
        return tok
    return f"{folder}/{tok}"


def _placement_files(s, repo=None):
    """(paths, complete) from the plan's Placement table; None when there is no table.

    Only File columns name targets ("Sits beside" names neighbours, not edits). An entry
    is joined to the row's Directory column unless it already is a repo path. A row whose
    File cell holds something that is not a path makes the result incomplete.
    """
    f = slices.plan_file(s)
    if f is None or not Path(f).is_file():
        return None
    files, complete, seen, inside, header = set(), True, False, False, None
    for ln in Path(f).read_text(errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            inside, header = "placement" in ln.lower(), None
            continue
        if not inside or not ln.lstrip().startswith("|") or set(ln) <= set("|-: "):
            continue
        cells = _cells(ln)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        seen = True
        row = dict(zip(header, cells))
        folder = next((v for k, v in row.items() if "dir" in k), "").strip("`/ ")
        targets = [v for k, v in row.items() if "file" in k]
        toks = [
            t.strip("()[]")
            for v in targets
            for t in re.split(r"[\s,;]+", v.replace("`", " "))
            if t.strip("()[]")
        ]
        if not toks:
            complete = False
        for tok in toks:
            if _TARGET.fullmatch(tok):
                files.add(_resolve(tok, folder, repo))
            else:
                complete = False
    return (files, complete) if seen else None


def changed_files(cfg, s):
    """(paths, complete): branch diff, worktree changes and planned files together.

    `complete` is False when a source that should answer could not: a branch that could
    not be compared, an unreadable worktree, a plan entry that is not a path, or no
    branch and no plan at all. Known paths still count for the pairs.
    """
    repo_cfg, _rules, base = merge.repo_settings(cfg, s["repo"])
    path = s["worktree"] or repo_cfg["path"]
    files, complete = set(), True
    if s["branch"]:
        got = _branch_files(path, base, s["branch"])
        complete = complete and got is not None
        files |= got or set()
    if s["worktree"] and Path(s["worktree"]).is_dir():
        got = _worktree_files(s["worktree"])
        complete = complete and got is not None
        files |= got or set()
    planned = _placement_files(s, repo_cfg.get("path"))
    if planned is not None:
        files |= planned[0]
        complete = complete and planned[1]
    elif not s["branch"]:
        complete = False
    return files, complete


def _shared(a, b):
    """Paths both sets name; a planned glob (`test_remote_*.py`) matches a real path."""
    out = a & b
    for x, globs in ((a, b), (b, a)):
        for g in (g for g in globs if "*" in g):
            out |= {p for p in x if fnmatch.fnmatchcase(p, g)}
    return out


def overlaps(project, open_slices=None):
    """([(key_a, key_b, shared_paths)], [keys whose files are not fully known])."""
    cfg = projmod.load_project(project)
    open_slices = [
        s for s in (open_slices or slices.list_slices(project)) if s["phase"] != "DONE"
    ]
    files, unknown = {}, []
    for s in open_slices:
        try:
            files[s["key"]], complete = changed_files(cfg, s)
        except (OSError, StopIteration, KeyError, ValueError, slices.SliceError):
            files[s["key"]], complete = set(), False
        if not complete:
            unknown.append(s["key"])  # still compared on what is known
    pairs = []
    for a, b in combinations(open_slices, 2):
        if a["row"] == b["row"] or a["repo"] != b["repo"]:
            continue
        shared = sorted(_shared(files[a["key"]], files[b["key"]]))
        if shared:
            pairs.append((a["key"], b["key"], shared))
    return pairs, unknown


def render(pairs, unknown):
    lines = ["## Overlap"]
    for a, b, shared in pairs:
        more = f" (+{len(shared) - SHOWN} more)" if len(shared) > SHOWN else ""
        lines.append(f"- {a} and {b}: {', '.join(shared[:SHOWN])}{more}")
    if unknown:
        lines.append(f"- files unknown: {', '.join(unknown)}")
    return lines if len(lines) > 1 else lines + ["- none"]
