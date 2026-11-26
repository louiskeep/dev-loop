"""Runs: dispatch, admission, liveness, result parsing and apply.

A run is a directory under `runs/`. Its `request.json` is frozen at admission; the
wrapper adds the process fields and the exit file. Evidence reaches a slice only
through `apply_run`, which reads the frozen request, never the slice's phase.
"""

import collections
import datetime
import hashlib
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from . import notes, slices, store
from . import project as projmod

Kind = collections.namedtuple(
    "Kind", "slot deadline_s idle_s reader writes_worktree pinned"
)

# One table maps every run kind to its slot, deadline and result reader.
KINDS = {
    "planner": Kind("planner", 7200, 1200, "claude", True, False),
    "builder": Kind("builder", 14400, 1200, "claude", True, False),
    "codex-builder": Kind("builder", 14400, 3600, "exit", True, False),
    "dennis": Kind("reviewer", 5400, 1200, "claude-verdict", False, True),
    "codex-review": Kind("reviewer", 3600, 3600, "verdict", False, True),
    "codex-fix": Kind("planner", 3600, 3600, "selfverdict", True, False),
    "test": Kind("tester", 7200, 1200, "testexit", False, True),
    "docs": Kind(None, 3600, 1200, "claude", True, False),
    "duty": Kind(None, 3600, 1200, "exit", False, False),
}
REVIEW_KINDS = ("dennis", "codex-review", "codex-fix")
# A run only makes sense in these phases; anything else is a stale or confused tick.
KIND_PHASES = {
    "planner": ("PLAN",),
    "codex-fix": ("PLAN",),
    "builder": ("DEVELOP", "REMEDIATE"),
    "codex-builder": ("REMEDIATE",),
    "dennis": ("PLAN", "REVIEW"),
    "codex-review": ("PLAN", "REVIEW"),
    "test": ("VERIFY", "REVIEW"),
    "docs": ("DOCUMENT",),
}
STATUSES = ("DONE", "BLOCKED", "FAILED")
DUTY_KEY = re.compile(r"DUTY-([A-Za-z0-9][A-Za-z0-9_]*)--[A-Za-z0-9][A-Za-z0-9._-]*")

Result = collections.namedtuple(
    "Result", "status head pr verdict tests next summary reason"
)


class Refused(Exception):
    """Dispatch refused; the message is the reason code printed to the caller."""


class RunError(Exception):
    pass


class Rejected(RunError):
    """A permanent apply refusal: the run can never be applied, so it is closed."""

    def __init__(self, why, message):
        super().__init__(message)
        self.why = why


def is_done(rd) -> bool:
    """Applied or rejected: nothing more will happen to this run."""
    return (rd / "applied").is_file() or (rd / "rejected").is_file()


# --- reading run directories --------------------------------------------------


def _runs_dir(project):
    # Readers never create it: the dashboard reads a project that may not have run yet.
    return store.state_path(project) / "runs"


# Process facts change after dispatch, so they live beside the frozen request.
PROC_KEYS = ("pid", "pid_start", "boot_id", "payload_pgid", "payload_start")


def _merged(rd) -> dict:
    req = store.read_json(rd / "request.json")
    if req:
        proc = store.read_json(rd / "proc.json")
        req.update({k: v for k, v in proc.items() if k in PROC_KEYS})
    return req


def read_request(project, run_id) -> dict:
    return _merged(store.run_dir(project, run_id))


def update_request(project, run_id, **fields) -> None:
    """Record process facts. The request itself is frozen and read-only."""
    if not set(fields) <= set(PROC_KEYS):
        raise RunError("only process fields may change after dispatch")
    path = store.run_dir(project, run_id) / "proc.json"
    with store.locked(path):
        data = store.read_json(path)
        data.update(fields)
        store.write_json(path, data)


def iter_runs(project):
    root = _runs_dir(project)
    for rd in sorted(root.iterdir()) if root.is_dir() else []:
        if not rd.is_dir():
            continue
        unreadable = False
        req = {}
        try:
            req = _merged(rd)
        except store.StoreError:
            unreadable = True
        if req and not unreadable:
            yield rd, req


def slice_key_of(req) -> str:
    return slices.slice_key(req["row"], req["slice"])


# --- liveness -----------------------------------------------------------------


def proc_start(pid):
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError, TypeError):
        return None
    # The command name may hold spaces and parens; fields restart after the last ")".
    rest = text.rsplit(")", 1)[-1].split()
    return rest[19] if len(rest) > 19 else None


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def _alive(pid, start, boot):
    if not pid or start is None or boot != boot_id():
        return False
    return proc_start(pid) == str(start)


def wrapper_alive(req) -> bool:
    return _alive(req.get("pid"), req.get("pid_start"), req.get("boot_id"))


def payload_alive(req) -> bool:
    return _alive(req.get("payload_pgid"), req.get("payload_start"), req.get("boot_id"))


def kill_payload(req) -> None:
    pgid = req.get("payload_pgid")
    if pgid and payload_alive(req):
        try:
            os.killpg(int(pgid), signal.SIGKILL)
        except OSError:
            pass


def remove_tree(project, run_id, req=None) -> None:
    """Remove a run's private checkout and its worktree registration.

    The path is the dispatcher's own layout, <run dir>/tree, never a value read from the
    request, so a corrupt request cannot point the delete elsewhere.
    """
    rd = store.run_dir(project, run_id)
    tree = rd / "tree"
    if not tree.exists() and not tree.is_symlink():
        return
    if tree.is_symlink() or tree.resolve() != (rd.resolve() / "tree"):
        notes.problem(
            project,
            "bad-run-tree",
            impact="a run's private checkout was not removed",
            fix=f"inspect {run_id} by hand",
        )
        return
    src = (req or {}).get("worktree")
    subprocess.run(
        ["git", "-C", src or str(rd), "worktree", "remove", "--force", str(tree)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    shutil.rmtree(tree, ignore_errors=True)


def read_exit(rd) -> dict:
    try:
        return store.read_json(rd / "exit")
    except store.StoreError:
        return {}  # a damaged record reads as empty; collect repairs it once the wrapper is dead


def _test_confirmed(rd) -> bool:
    """A test run frees its slot only when the payload itself said it finished."""
    ex = read_exit(rd)
    return (rd / "released").is_file() or (
        ex.get("reason") == "exit" and ex.get("code") in (0, 1)
    )


def holds_slot(rd, req) -> bool:
    spec = KINDS.get(req.get("kind"))
    if spec is None:
        return False
    ended = (rd / "exit").is_file()
    if (rd / "rejected").is_file():
        return False
    if spec.writes_worktree and not (rd / "applied").is_file():
        return True  # reserved until applied, not only while the process lives
    if req["kind"] == "test":
        return not (ended and _test_confirmed(rd))
    return not ended and (wrapper_alive(req) or payload_alive(req))


def run_state(rd, req):
    """What a slot-holding run is doing: executing, awaiting-apply, awaiting-release.

    None when it holds no slot. Only an executing run owns a card; the other two are
    finished or lost work that still reserves capacity until someone acts.
    """
    if not holds_slot(rd, req):
        return None
    if not (rd / "exit").is_file() and (wrapper_alive(req) or payload_alive(req)):
        return "executing"
    return "awaiting-release" if req["kind"] == "test" else "awaiting-apply"


def slot_usage(project) -> dict:
    """Slots held per pool name (`other` for kinds with no pool)."""
    used = {}
    for rd, req in iter_runs(project):
        if holds_slot(rd, req):
            slot = KINDS[req["kind"]].slot or "other"
            used[slot] = used.get(slot, 0) + 1
    return used


RESERVE_WINDOW_S = 300


def _project_names():
    root = store.state_root()
    return sorted(
        d.name
        for d in (root.iterdir() if root.is_dir() else [])
        if d.is_dir() and store.NAME.fullmatch(d.name)
    )


def _frozen_requests(name):
    """(run dir, request) for each run of a project, read from the frozen request only.

    The process facts in proc.json are not needed to know what a run reserved, and a
    damaged one must not hide a reservation from every other project's admission.
    """
    root = _runs_dir(name)
    for rd in sorted(root.iterdir()) if root.is_dir() else []:
        if not rd.is_dir():
            continue
        try:
            req = store.read_json(rd / "request.json")
        except (store.StoreError, OSError):
            continue
        if req:
            yield rd, req


def _utc(moment):
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def reserved_mb(now=None) -> int:
    """Memory that runs started in the last five minutes may still claim, on this host.

    MemAvailable is host-wide, so every project under the state root counts. A worker's
    memory is not in MemAvailable until it has grown, so admission and the dashboard both
    subtract these reservations. A run that has ended holds nothing. The amount is the one
    frozen into the run at dispatch; a run from before that field uses its own project's
    estimate, never the caller's. Times without an offset are read as UTC.
    """
    now = _utc(now or datetime.datetime.now(datetime.timezone.utc))
    cutoff = now - datetime.timedelta(seconds=RESERVE_WINDOW_S)
    total = 0
    for name in _project_names():
        owner_cfg = None
        for rd, req in _frozen_requests(name):
            try:
                started = _utc(datetime.datetime.fromisoformat(req["started_at"]))
            except (KeyError, ValueError, TypeError):
                continue
            if started < cutoff or (rd / "exit").is_file():
                continue
            frozen = req.get("memory_mb")
            if type(frozen) is int and frozen >= 0:
                total += frozen
                continue
            if not isinstance(req.get("kind"), str):
                continue
            if owner_cfg is None:
                try:
                    owner_cfg = projmod.load_project(name)
                except (projmod.ConfigError, store.StoreError, OSError):
                    owner_cfg = {}  # an unreadable config still reserves the default
            total += projmod.memory_mb(owner_cfg, req.get("kind"))
    return total


def live_runs(project) -> list:
    return [(rd.name, req) for rd, req in iter_runs(project) if holds_slot(rd, req)]


def writer_runs(project) -> list:
    return [
        (rd.name, req)
        for rd, req in iter_runs(project)
        if KINDS.get(req.get("kind"), KINDS["docs"]).writes_worktree and not is_done(rd)
    ]


def sha_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def __getattr__(name):
    """Keep the old public names importable from here after the split.

    Resolved lazily: dispatch and apply import this module, so importing them at the
    top would be circular.
    """
    if not name.startswith("_"):
        from . import apply, dispatch

        for mod in (dispatch, apply):
            if hasattr(mod, name):
                return getattr(mod, name)
    raise AttributeError(name)
