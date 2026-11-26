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
    for cand in (branch, f"origin/{branch}"):
        mb = (carry._git(path, "merge-base", ref, cand) or "").strip()
        if mb:
            return carry._files(path, f"{mb}..{cand}")
    return None


def _placement_files(s):
    """File paths in the plan's Placement table, or None when there is none to read."""
    f = slices.plan_file(s)
    if f is None or not Path(f).is_file():
        return None
    lines, files, inside = (
        Path(f).read_text(errors="replace").splitlines(),
        set(),
        False,
    )
    for ln in lines:
        if ln.lstrip().startswith("#"):
            inside = "placement" in ln.lower()
        elif inside and ln.lstrip().startswith("|") and not set(ln) <= set("|-: "):
            for cell in ln.strip().strip("|").split("|"):
                cell = cell.strip().strip("`")
                if _PATH.fullmatch(cell):
                    files.add(cell)
    return files or None


def changed_files(cfg, s):
    """The files a slice changes: its branch diff, else its plan's table, else None."""
    repo_cfg, _rules, base = merge.repo_settings(cfg, s["repo"])
    path = s["worktree"] or repo_cfg["path"]
    if s["branch"]:
        files = _branch_files(path, base, s["branch"])
        if files is not None:
            return files
    return _placement_files(s)


def overlaps(project, open_slices=None):
    """([(key_a, key_b, shared_paths)], [keys with unknown files])."""
    cfg = projmod.load_project(project)
    open_slices = [
        s for s in (open_slices or slices.list_slices(project)) if s["phase"] != "DONE"
    ]
    files = {s["key"]: changed_files(cfg, s) for s in open_slices}
    pairs = []
    for a, b in combinations(open_slices, 2):
        if a["row"] == b["row"] or files[a["key"]] is None or files[b["key"]] is None:
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
