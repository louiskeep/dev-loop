"""One t1 run for up to four slices.

The run tests a commit that merges every member's head onto the base. The test tool
picks its tests from the diff of that tree against the base, so the selection is the
union of the members' own. A green run credits each member at its own head; a red run
says nothing about any one member, so the orchestrator splits the batch.
"""

import subprocess

from . import carry, merge, runs, slices

MAX_MEMBERS = 4
REF = "refs/devloop/batch/"
_IDENT = ["-c", "user.name=devloop", "-c", "user.email=devloop@localhost"]


def _git(path, *args):
    return subprocess.run(
        ["git", *_IDENT, "-C", str(path), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=carry.TIMEOUT,
        check=False,
    )


def _has_live_test(project, key):
    for rd, req in runs.iter_runs(project):
        if req.get("kind") != "test" or not runs.holds_slot(rd, req):
            continue
        if runs.slice_key_of(req) == key or key in (req.get("batch") or {}):
            return True
    return False


def members_of(project, lead, extra, level, select):
    """Check the batch and return {key: head}, the lead included."""
    if level != "t1":
        raise runs.Refused("only a t1 run can test a batch")
    if select:
        raise runs.Refused("a batch runs the whole selection; --select is not allowed")
    keys = [lead["key"], *extra]
    if len(set(keys)) != len(keys):
        raise runs.Refused("a slice appears twice in the batch")
    if len(keys) > MAX_MEMBERS:
        raise runs.Refused(f"a batch holds at most {MAX_MEMBERS} slices")
    frozen = {}
    for key in keys:
        try:
            s = lead if key == lead["key"] else slices.load(project, key)
        except slices.SliceError:
            raise runs.Refused(f"{key} is not a slice") from None
        if s["repo"] != lead["repo"]:
            raise runs.Refused(f"{key} is in another repo")
        if not s["head"]:
            raise runs.Refused(f"{key} has no head")
        if s["phase"] != "VERIFY":
            raise runs.Refused(f"{key} is not in VERIFY")
        if key != lead["key"] and _has_live_test(project, key):
            raise runs.Refused(f"{key} already has a live test run")
        frozen[key] = s["head"]
    return frozen


def build(path, base, frozen):
    """The batch commit: parents are the base then each head in key order, its tree is
    the clean merge of all of them. Nothing in any worktree changes."""
    tip = carry.base_ref(path, base)
    if tip is None:
        raise runs.Refused("could not fetch the base branch")
    acc, tree = tip, None
    for key in sorted(frozen):
        head = frozen[key]
        if _git(path, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
            raise runs.Refused(f"{key} head is not a commit in the worktree")
        m = _git(path, "merge-tree", "--write-tree", acc, head)
        if m.returncode == 1:
            raise runs.Refused(f"{key} conflicts with the base or an earlier member")
        if m.returncode != 0:
            raise runs.Refused(f"could not merge {key}")
        tree = m.stdout.split()[0]
        step = _git(path, "commit-tree", tree, "-p", acc, "-p", head, "-m", "step")
        if step.returncode != 0:
            raise runs.Refused("could not build the batch commit")
        acc = step.stdout.strip()
    parents = [
        x for sha in (tip, *(frozen[k] for k in sorted(frozen))) for x in ("-p", sha)
    ]
    msg = "devloop t1 batch: " + " ".join(sorted(frozen))
    final = _git(path, "commit-tree", tree, *parents, "-m", msg)
    if final.returncode != 0:
        raise runs.Refused("could not build the batch commit")
    return final.stdout.strip()


def prepare(project, cfg, lead, extra, level, select, wt):
    frozen = members_of(project, lead, extra, level, select)
    _, _, base = merge.repo_settings(cfg, lead["repo"])
    return frozen, build(wt, base, frozen)


def keep(wt, run_id, commit):
    """Keep the batch commit reachable while the run's tree exists."""
    r = _git(wt, "update-ref", REF + run_id, commit)
    if r.returncode != 0:
        raise runs.Refused("could not keep the batch commit")


def drop(wt, run_id):
    _git(wt, "update-ref", "-d", REF + run_id)
