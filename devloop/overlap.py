"""Which open slices in different rows are changing the same files.

The tick reads this before it starts a builder. It is advice, never a block: the
orchestrator decides the order and says so in next_action.
"""

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
    """Staged, unstaged and untracked paths in the slice's own worktree."""
    out = carry._git(path, "status", "--porcelain", "--untracked-files=all")
    if out is None:
        return set()
    return {
        ln[3:].split(" -> ")[-1].strip('"') for ln in out.splitlines() if len(ln) > 3
    }


def _cells(ln):
    return [c.strip() for c in ln.strip().strip("|").split("|")]


def _placement_files(s):
    """Every path the plan's Placement table names, or None when there is none to read.

    A File column is read even for names with no extension (Dockerfile), joined to a
    Directory column when the row has one. Paths in other cells count too: naming a
    neighbour as well only adds a false overlap, never hides a real one.
    """
    f = slices.plan_file(s)
    if f is None or not Path(f).is_file():
        return None
    files, inside, header = set(), False, None
    for ln in Path(f).read_text(errors="replace").splitlines():
        if ln.lstrip().startswith("#"):
            inside, header = "placement" in ln.lower(), None
            continue
        if not inside or not ln.lstrip().startswith("|"):
            continue
        if set(ln) <= set("|-: "):
            continue
        cells = _cells(ln)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        row = dict(zip(header, cells))
        folder = next((v for k, v in row.items() if "dir" in k), "").strip("`/ ")
        for k, v in row.items():
            for tok in re.split(r"[\s,;]+", v.replace("`", " ")):
                tok = tok.strip("()[]")
                if not tok:
                    continue
                if "file" in k and re.fullmatch(r"[\w.@+/-]+", tok):
                    files.add(f"{folder}/{tok}" if folder and "/" not in tok else tok)
                elif _PATH.fullmatch(tok):
                    files.add(tok)
    return files or None


def changed_files(cfg, s):
    """The files a slice changes: its branch diff, else its plan's table, else None."""
    repo_cfg, _rules, base = merge.repo_settings(cfg, s["repo"])
    path = s["worktree"] or repo_cfg["path"]
    found = []
    if s["branch"]:
        found.append(_branch_files(path, base, s["branch"]))
    if s["worktree"] and Path(s["worktree"]).is_dir():
        found.append(_worktree_files(s["worktree"]))
    found.append(_placement_files(s))
    known = [f for f in found if f is not None]
    # Planned files count with committed ones: a fresh branch has no diff yet.
    return set().union(*known) if any(known) else None


def overlaps(project, open_slices=None):
    """([(key_a, key_b, shared_paths)], [keys with unknown files])."""
    cfg = projmod.load_project(project)
    open_slices = [
        s for s in (open_slices or slices.list_slices(project)) if s["phase"] != "DONE"
    ]
    files = {}
    for s in open_slices:
        try:
            files[s["key"]] = changed_files(cfg, s)
        except (OSError, StopIteration, KeyError, ValueError, slices.SliceError):
            files[s["key"]] = None  # this slice is unknown; the other pairs still show
    pairs = []
    for a, b in combinations(open_slices, 2):
        if a["row"] == b["row"] or a["repo"] != b["repo"]:
            continue
        if files[a["key"]] is None or files[b["key"]] is None:
            continue
        shared = sorted(files[a["key"]] & files[b["key"]])
        if shared:
            pairs.append((a["key"], b["key"], shared))
    unknown = [k for k, v in files.items() if v is None]
    return pairs, unknown


def render(pairs, unknown):
    lines = ["## Overlap"]
    for a, b, shared in pairs:
        more = f" (+{len(shared) - SHOWN} more)" if len(shared) > SHOWN else ""
        lines.append(f"- {a} and {b}: {', '.join(shared[:SHOWN])}{more}")
    if unknown:
        lines.append(f"- files unknown: {', '.join(unknown)}")
    return lines if len(lines) > 1 else lines + ["- none"]
