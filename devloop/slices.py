"""Slices: phases, transitions, and the evidence that gates each move.

Only this module writes slice files. Every write is a load, check, replace under one
flock, so a refused move leaves the file byte for byte as it was.
"""

import contextlib
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import notes
from . import project as projmod
from . import store

SCHEMA_VERSION = 1
PHASES = (
    "FRAME",
    "PLAN",
    "DEVELOP",
    "SELF_CHECK",
    "VERIFY",
    "REVIEW",
    "REMEDIATE",
    "GATE",
    "DOCUMENT",
    "DONE",
    "PARKED",
    "HELD",
)
ACTIVE = PHASES[:9]
RISKS = ("R0", "R1", "R2", "R3")
TIERS = ("R0", "R1", "R2")
VERDICTS = ("GO", "GO-WITH-FIXES", "NO-GO")
TICK_KINDS = ("selfcheck", "docs")
MAX_ROUNDS = 3
# Parks that only the owner may lift.
OWNER_PARKS = ("round-3", "failed-twice")
HISTORY_KEEP = 200

# The phase a slice normally leaves through; the checklist and `move` agree on it.
FORWARD = {
    "FRAME": "PLAN",
    "PLAN": "DEVELOP",
    "DEVELOP": "SELF_CHECK",
    "SELF_CHECK": "VERIFY",
    "VERIFY": "REVIEW",
    "REVIEW": "GATE",
    "GATE": "DOCUMENT",
    "DOCUMENT": "DONE",
}

SETTABLE = (
    "worktree",
    "branch",
    "pr",
    "head",
    "plan_path",
    "purpose",
    "next_action",
    "waiting_on",
    "risk",
    "rulebooks",
    "full_suite_required",
)

_ID = r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*"
_KEY = re.compile(f"{_ID}--{_ID}")
_ID_RE = re.compile(_ID)
_SHA = re.compile(r"[0-9a-f]{2,64}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_EVIDENCE_KIND = re.compile(
    r"(?:selfcheck|docs|t1|t2|full|(?:code|plan):[A-Za-z0-9_-]+)"
)


class SliceError(Exception):
    pass


class TransitionError(SliceError):
    pass


def _build_transitions() -> dict:
    t = {p: set() for p in PHASES}
    t["FRAME"] |= {"PLAN"}
    t["PLAN"] |= {"DEVELOP"}
    t["DEVELOP"] |= {"SELF_CHECK"}
    t["SELF_CHECK"] |= {"VERIFY", "REMEDIATE"}
    t["VERIFY"] |= {"REVIEW", "REMEDIATE"}
    t["REVIEW"] |= {"GATE", "REMEDIATE"}
    t["REMEDIATE"] |= {"SELF_CHECK", "PLAN"}
    t["GATE"] |= {"DOCUMENT", "SELF_CHECK", "REMEDIATE"}
    t["DOCUMENT"] |= {"DONE"}
    for p in ACTIVE:
        t[p] |= {"PARKED", "HELD"}
    # A tier change or a new plan restarts the lineage; the table in the plan
    # names FRAME as that destination but lists no way back to it.
    for p in ("PLAN", "DEVELOP", "SELF_CHECK", "VERIFY", "REVIEW", "REMEDIATE", "GATE"):
        t[p].add("FRAME")
    # A parked or held slice resumes only to the phase recorded at park time.
    t["PARKED"] = set(ACTIVE)
    t["HELD"] = set(ACTIVE)
    return t


TRANSITIONS = _build_transitions()


# --- the stored shape ---------------------------------------------------------


def _bad(name):
    # Names the field only: a corrupt value must not leak into the message.
    raise SliceError(f"bad slice field: {name}")


def _str(v):
    return isinstance(v, str)


def _ostr(v):
    return v is None or isinstance(v, str)


def _nat(v):
    return type(v) is int and v >= 0


def _bool(v):
    return type(v) is bool


def _strlist(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _match(rx, optional=False):
    def check(v):
        if v is None:
            return optional
        return isinstance(v, str) and rx.fullmatch(v) is not None

    return check


def _evidence(v):
    if not isinstance(v, dict):
        return False
    for sha, bucket in v.items():
        if not _match(_SHA)(sha) or not isinstance(bucket, dict):
            return False
        for kind, entry in bucket.items():
            if not _match(_EVIDENCE_KIND)(kind) or not isinstance(entry, dict):
                return False
            if entry.get("verdict") not in VERDICTS or not _ostr(entry.get("run")):
                return False
            if not _str(entry.get("at")):
                return False
    return True


def _plan_rounds(v):
    return isinstance(v, dict) and all(
        isinstance(rounds, dict) and all(_nat(n) for n in rounds.values())
        for rounds in v.values()
    )


def _history(v):
    return isinstance(v, list) and all(isinstance(x, dict) for x in v)


_FIELDS = {
    "schema_version": lambda v: type(v) is int and v == SCHEMA_VERSION,
    "key": _match(_KEY),
    "row": _match(re.compile(_ID)),
    "slice": _match(re.compile(_ID)),
    "phase": lambda v: isinstance(v, str) and v in PHASES,
    "risk": lambda v: v is None or (isinstance(v, str) and v in RISKS),
    "rulebooks": _strlist,
    "worktree": _ostr,
    "branch": _match(_BRANCH, optional=True),
    "repo": _str,
    "pr": lambda v: v is None or _nat(v),
    "head": _match(_SHA, optional=True),
    "plan_path": _ostr,
    "purpose": _ostr,
    "round": _nat,
    "plan_rounds": _plan_rounds,
    "evidence": _evidence,
    "full_suite_required": _bool,
    "run": _ostr,
    "waiting_on": _ostr,
    "next_action": _ostr,
    "last_moved_at": _str,
    "failures": lambda v: isinstance(v, dict) and all(_nat(n) for n in v.values()),
    "merge_unknown": _bool,
    "merge_attempt": lambda v: v is None or isinstance(v, dict),
    "park_reason": _ostr,
    "parked_from": lambda v: v is None or (isinstance(v, str) and v in ACTIVE),
    "history": _history,
    "approved_plan_sha": _match(_SHA, optional=True),
    "plan_frozen": _bool,
    "imported": _bool,
    "owner_answered": _bool,
    "was_r3": _bool,
    "processed_run_ids": _strlist,
}


def _validate(s) -> dict:
    if not isinstance(s, dict):
        _bad("slice")
    for name, check in _FIELDS.items():
        if name not in s or not check(s[name]):
            _bad(name)
    return s


def slice_key(row: str, slice_id: str) -> str:
    return f"{row}--{slice_id}"


def _slices_dir(project: str) -> Path:
    d = store.state_dir(project) / "slices"
    d.mkdir(exist_ok=True)
    return d


def slice_path(project: str, key: str) -> Path:
    if not isinstance(key, str) or _KEY.fullmatch(key) is None:
        raise SliceError("bad slice key")
    return _slices_dir(project) / f"{key}.json"


def load(project: str, key: str) -> dict:
    path = slice_path(project, key)
    if not path.is_file():
        raise SliceError("no such slice")
    unreadable = False
    try:
        data = store.read_json(path)
    except store.StoreError:
        unreadable = True
    if unreadable:
        raise SliceError("unreadable slice file")
    return _validate(data)


def _save(project: str, s: dict) -> None:
    _validate(s)
    store.write_json(slice_path(project, s["key"]), s)


@contextlib.contextmanager
def _edit(project: str, key: str):
    """Load, let the caller change, save. An exception skips the save."""
    path = slice_path(project, key)
    with store.locked(path):
        s = load(project, key)
        yield s
        _save(project, s)


def list_slices(project: str) -> list:
    out = []
    for path in sorted(_slices_dir(project).glob("*.json")):
        out.append(load(project, path.stem))
    return out


def _blank(row, slice_id, repo, risk) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "key": slice_key(row, slice_id),
        "row": row,
        "slice": slice_id,
        "phase": "FRAME",
        "risk": risk,
        "rulebooks": [],
        "worktree": None,
        "branch": None,
        "repo": repo,
        "pr": None,
        "head": None,
        "plan_path": None,
        "purpose": None,
        "round": 0,
        "plan_rounds": {},
        "evidence": {},
        "full_suite_required": False,
        "run": None,
        "waiting_on": None,
        "next_action": None,
        "last_moved_at": store.now_iso(),
        "failures": {},
        "merge_unknown": False,
        "merge_attempt": None,
        "park_reason": None,
        "parked_from": None,
        "history": [],
        "approved_plan_sha": None,
        "plan_frozen": False,
        "imported": False,
        "owner_answered": False,
        "was_r3": risk == "R3",
        "processed_run_ids": [],
    }


# --- board mirror -------------------------------------------------------------


def _board_call(project, cfg, args, **fields):
    board = cfg.get("board")
    if not board:
        return
    ok = False
    try:
        r = subprocess.run(
            board["cmd"] + args,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    if not ok:
        # Output is dropped on purpose: a failing tool may echo row text.
        store.append_event(project, "board-mirror-failed", **fields)
        notes.problem(
            project,
            "board-mirror-failed",
            impact="the board shows an older phase than the loop",
            fix="check the board command in project.json",
        )


def _mirror(project, cfg, s):
    board = cfg.get("board") or {}
    mapped = board.get("phase_map", {}).get(s["phase"])
    if mapped:
        _board_call(
            project,
            cfg,
            ["slice", s["row"], s["slice"], mapped],
            key=s["key"],
            phase=s["phase"],
        )


def row_close(project: str, row: str) -> None:
    if not isinstance(row, str) or _ID_RE.fullmatch(row) is None:
        raise SliceError("bad row id")
    cfg = projmod.load_project(project)
    _board_call(project, cfg, ["close", row], row=row)
    store.append_event(project, "row-closed", row=row)


# --- creating slices ----------------------------------------------------------


def _create(project, row, slice_id, repo, risk=None, **overrides) -> dict:
    for part in (row, slice_id):
        if not isinstance(part, str) or _ID_RE.fullmatch(part) is None:
            raise SliceError("bad row or slice id")
    if risk is not None and risk not in RISKS:
        _bad("risk")
    cfg = projmod.load_project(project)
    if repo not in [r["name"] for r in cfg["repos"]]:
        raise SliceError("unknown repo")
    s = _blank(row, slice_id, repo, risk)
    s.update(overrides)
    with store.locked(_slices_dir(project) / "_admission"):
        for other in list_slices(project):
            if other["key"] == s["key"]:
                raise SliceError("slice already exists")
            if other["row"] == row and other["phase"] != "DONE":
                raise SliceError("the row already has an open slice")
        _save(project, s)
    store.append_event(project, "slice-new", key=s["key"], row=row, slice=slice_id)
    _mirror(project, cfg, s)
    return s


def new_slice(project: str, row: str, slice_id: str, *, repo: str, risk=None) -> dict:
    return _create(project, row, slice_id, repo, risk)


def _pr_state(repo_path, pr):
    data = None
    try:
        r = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "state,baseRefName,headRefOid"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return data if isinstance(data, dict) else None


def import_slice(
    project: str,
    row: str,
    slice_id: str,
    *,
    repo: str,
    risk,
    rulebooks,
    worktree,
    branch,
    pr,
    head,
    plan_path,
    purpose,
    phase: str,
    plan_approved: bool = False,
) -> dict:
    """Bring a slice the old loop was running under this loop's rules (cutover only)."""
    store.require_interactive("import")
    if phase not in ACTIVE:
        raise SliceError("import needs the slice's phase in the old loop")
    cfg = projmod.load_project(project)
    repo_cfg = next((r for r in cfg["repos"] if r["name"] == repo), None)
    if repo_cfg is None:
        raise SliceError("unknown repo")

    merged = False
    if pr is not None and head:
        state = _pr_state(repo_cfg["path"], pr)
        oid = (state or {}).get("headRefOid", "")
        merged = bool(
            state
            and state.get("state") == "MERGED"
            and state.get("baseRefName") == repo_cfg.get("base", "main")
            and isinstance(oid, str)
            and (oid == head or (len(head) >= 7 and oid.startswith(head)))
        )

    if merged:
        target = "DOCUMENT"
    elif phase == "FRAME":
        target = "FRAME"
    elif not plan_approved:
        target = "PLAN"
    elif phase in ("PLAN",):
        target = "DEVELOP"
    elif phase in ("GATE", "DOCUMENT"):
        target = "REVIEW"
    else:
        target = phase

    overrides = {
        "phase": target,
        "rulebooks": list(rulebooks),
        "worktree": worktree,
        "branch": branch,
        "pr": pr,
        "head": head,
        "plan_path": plan_path,
        "purpose": purpose,
        "imported": True,
    }
    if target != "FRAME":
        if risk not in TIERS or not rulebooks:
            raise SliceError("import needs a risk and rulebooks")
        if target not in ("PLAN", "DEVELOP") and not (head and branch):
            raise SliceError("import needs a branch and head for this phase")
    if plan_approved:
        probe = {"plan_path": plan_path, "worktree": worktree}
        overrides["approved_plan_sha"] = plan_sha(probe)
    return _create(project, row, slice_id, repo, risk, **overrides)


# --- editing fields -----------------------------------------------------------


def plan_sha(s: dict) -> str:
    sha = _plan_sha_or_none(s)
    if sha is None:
        raise SliceError("plan file not found")
    return sha


def _plan_file(s):
    p = s.get("plan_path")
    if not p:
        return None
    path = Path(p)
    if not path.is_absolute() and s.get("worktree"):
        path = Path(s["worktree"]) / path
    return path


def _plan_sha_or_none(s):
    f = _plan_file(s)
    if f is None or not f.is_file():
        return None
    return hashlib.sha256(f.read_bytes()).hexdigest()


def _rank(risk):
    return -1 if risk is None else RISKS.index(risk) if risk in RISKS else -1


def set_fields(project: str, key: str, **fields) -> dict:
    unknown = sorted(set(fields) - set(SETTABLE))
    if unknown:
        raise SliceError(f"unknown field: {', '.join(unknown)}")
    with _edit(project, key) as s:
        if s["phase"] == "DONE":
            raise SliceError("slice is DONE")
        if s["phase"] == "HELD" and ("waiting_on" in fields or "risk" in fields):
            raise SliceError("a held slice changes only through the owner's answer")
        if "risk" in fields and fields["risk"] != s["risk"]:
            if s["phase"] != "FRAME":
                raise SliceError("risk can change only in FRAME")
            if _rank(fields["risk"]) < _rank(s["risk"]) and s["was_r3"]:
                # Lowering an R3 slice's risk is the owner's call, never the tick's.
                store.require_interactive("lowering the risk of an R3 slice")
            if fields["risk"] == "R3":
                s["was_r3"] = True
        if (
            "plan_path" in fields
            and fields["plan_path"] != s["plan_path"]
            and s["plan_frozen"]
        ):
            raise SliceError("plan_path is frozen once a plan run exists")
        if "full_suite_required" in fields and s["full_suite_required"]:
            if fields["full_suite_required"] is not True:
                raise SliceError("full_suite_required can only go from False to True")
        s.update(fields)
    return s


def mark_plan_started(project: str, key: str) -> None:
    """Called by dispatch under admission when the first plan run is created."""
    with _edit(project, key) as s:
        s["plan_frozen"] = True


# --- evidence -----------------------------------------------------------------


def _put_evidence(s, kind, verdict, run_id, *, sha, extra=None):
    """Mutate `s` in place; the same run recorded twice changes nothing."""
    bucket = s["evidence"].setdefault(sha, {})
    prior = bucket.get(kind)
    if run_id is not None and prior is not None and prior.get("run") == run_id:
        return
    entry = {"verdict": verdict, "run": run_id, "at": store.now_iso(), **(extra or {})}
    if kind.startswith("plan:"):
        if not s["plan_path"]:
            raise SliceError("plan evidence needs a plan_path")
        entry["plan_path"] = s["plan_path"]
        name = kind.split(":", 1)[1]
        rounds = s["plan_rounds"].setdefault(s["plan_path"], {})
        rounds[name] = rounds.get(name, 0) + 1
    bucket[kind] = entry


def record_evidence(
    project: str,
    key: str,
    kind: str,
    verdict: str,
    run_id: str,
    *,
    head=None,
    plan_sha=None,
) -> None:
    """Internal: `runs.apply_run` is the only caller. Tick records use the tick helpers."""
    if (head is None) == (plan_sha is None):
        raise SliceError("give exactly one of head or plan_sha")
    if not isinstance(kind, str) or _EVIDENCE_KIND.fullmatch(kind) is None:
        raise SliceError("bad evidence kind")
    if kind in TICK_KINDS:
        raise SliceError("selfcheck and docs are recorded by the tick, not by a run")
    if kind.startswith("plan:") != (plan_sha is not None):
        raise SliceError("plan evidence is keyed by plan_sha, other evidence by head")
    if verdict not in VERDICTS:
        raise SliceError("bad verdict")
    if not isinstance(run_id, str) or store.TOKEN.fullmatch(run_id) is None:
        raise SliceError("bad run id")
    with _edit(project, key) as s:
        _put_evidence(s, kind, verdict, run_id, sha=head or plan_sha)


def record_tick_evidence(
    project: str, key: str, kind: str, verdict: str, *, head: str
) -> None:
    """The tick's own record: no run dir, validated as its own type."""
    if kind not in TICK_KINDS or verdict not in ("GO", "NO-GO"):
        raise SliceError("bad tick evidence")
    with _edit(project, key) as s:
        if head != s["head"]:
            raise SliceError("head does not match the slice head")
        _put_evidence(
            s, kind, verdict, None, sha=head, extra={"source": "tick", "head": head}
        )


def selfcheck(project: str, key: str, verdict: str, *, head: str) -> None:
    if slice_phase(project, key) != "SELF_CHECK":
        raise SliceError("selfcheck is recorded in SELF_CHECK")
    record_tick_evidence(project, key, "selfcheck", verdict, head=head)


def docs(project: str, key: str, verdict: str, *, head: str) -> None:
    if verdict != "GO":
        raise SliceError("docs records GO only")
    if slice_phase(project, key) != "DOCUMENT":
        raise SliceError("docs is recorded in DOCUMENT")
    record_tick_evidence(project, key, "docs", verdict, head=head)


def slice_phase(project: str, key: str) -> str:
    return load(project, key)["phase"]


def _tick_ok(s, kind):
    e = (s["evidence"].get(s["head"]) or {}).get(kind) if s["head"] else None
    return bool(
        e
        and e["verdict"] == "GO"
        and e.get("source") == "tick"
        and e.get("head") == s["head"]
    )


def _verify_run(project, s, entry, *, kinds, expect, covers_ok=False):
    """Re-read the run dir behind an evidence entry. Returns (problem or None, request)."""
    run_id = entry.get("run")
    if not run_id:
        return "no run recorded", None
    unreadable = False
    req = {}
    try:
        rd = store.run_dir(project, run_id)
        req = store.read_json(rd / "request.json")
    except store.StoreError:
        unreadable = True
    if unreadable or not req:
        return f"run {run_id} has no readable request", None
    own = (req.get("row"), req.get("slice")) == (s["row"], s["slice"])
    covers = req.get("covers") if isinstance(req.get("covers"), dict) else {}
    if not own and not (covers_ok and s["key"] in covers):
        return f"run {run_id} belongs to another slice", None
    if not own and covers.get(s["key"]) != s["head"]:
        return f"run {run_id} froze a different head for this slice", None
    if not (rd / "applied").is_file():
        return f"run {run_id} is not applied", None
    if req.get("kind") not in kinds:
        return f"run {run_id} kind is not allowed here", None
    for field, want in expect.items():
        if field == "head" and not own:
            continue
        if req.get(field) != want:
            return f"run {run_id} {field} does not match", None
    if store.read_header(rd / "result.md").get("verdict") != entry["verdict"]:
        return f"run {run_id} result differs from the recorded verdict", None
    return None, req


def _item(label, problem):
    return (label if problem is None else f"{label} ({problem})", problem is None)


def _reviewer_kinds(cfg, name):
    spec = cfg["reviewers"][name]
    return tuple(spec.get("kinds") or [spec["kind"]])


def _test_item(project, s, level, label):
    e = (s["evidence"].get(s["head"]) or {}).get(level) if s["head"] else None
    if not e or e["verdict"] != "GO":
        return (label, False)
    problem, _ = _verify_run(
        project,
        s,
        e,
        kinds=("test",),
        expect={"level": level, "head": s["head"]},
        covers_ok=(level == "full"),
    )
    return _item(label, problem)


def _plan_items(project, s, cfg):
    f = _plan_file(s)
    cur = _plan_sha_or_none(s)
    items = [("plan file exists", cur is not None)]
    has_rules = False
    if f is not None and cur is not None:
        has_rules = "Rules consulted:" in f.read_text(
            encoding="utf-8", errors="replace"
        )
    items.append(("plan names the Rules consulted", has_rules))
    if s["risk"] not in TIERS:
        return items + [("risk is R0, R1 or R2", False)]
    roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]]["plan"]]
    prev_run = None
    for i, (name, cap) in enumerate(roster):
        last = i == len(roster) - 1
        label = f"plan review {name}"
        kinds = _reviewer_kinds(cfg, name)
        mine = [
            (sha, e)
            for sha, bucket in s["evidence"].items()
            for kind, e in bucket.items()
            if kind == f"plan:{name}" and e.get("plan_path") == s["plan_path"]
        ]
        mine.sort(key=lambda pair: pair[1]["at"])
        sat = None
        if cap and not last:
            rounds = s["plan_rounds"].get(s["plan_path"], {}).get(name, 0)
            gos = [p for p in mine if p[1]["verdict"] == "GO"]
            pick = gos[-1] if gos else (mine[-1] if mine and rounds >= cap else None)
            if pick is None:
                items.append((f"{label} (needs a GO or {cap} rounds)", False))
                prev_run = None
                continue
            problem, _ = _verify_run(
                project,
                s,
                pick[1],
                kinds=kinds,
                expect={"purpose": "plan", "reviewer": name, "plan_sha": pick[0]},
            )
            items.append(_item(label, problem))
            sat = pick[1]["run"] if problem is None else None
        else:
            e = (s["evidence"].get(cur) or {}).get(f"plan:{name}") if cur else None
            if not e or e["verdict"] != "GO":
                items.append((f"{label} GO on the current plan", False))
                prev_run = None
                continue
            problem, req = _verify_run(
                project,
                s,
                e,
                kinds=kinds,
                expect={"purpose": "plan", "reviewer": name, "plan_sha": cur},
            )
            if problem is None and i > 0 and req.get("predecessor_run") != prev_run:
                problem = "predecessor run does not match the previous reviewer"
            items.append(_item(f"{label} GO on the current plan", problem))
            sat = e["run"] if problem is None else None
        prev_run = sat
    return items


def _builder_run_ok(project, s):
    if s["imported"] and s["approved_plan_sha"]:
        return True
    runs = store.state_dir(project) / "runs"
    for rd in sorted(runs.glob("*")) if runs.is_dir() else []:
        unreadable = False
        req = {}
        try:
            req = store.read_json(rd / "request.json")
        except store.StoreError:
            unreadable = True
        if unreadable or req.get("kind") != "builder":
            continue
        if (req.get("row"), req.get("slice")) != (s["row"], s["slice"]):
            continue
        hdr = store.read_header(rd / "result.md")
        if (
            (rd / "applied").is_file()
            and hdr.get("status") == "DONE"
            and hdr.get("head") == s["head"]
        ):
            return True
    return False


def _gate_items(project, s, cfg):
    if not s["head"]:
        return [("head set", False)]
    items = []
    sha = _plan_sha_or_none(s)
    items.append(
        (
            "approved plan unchanged",
            bool(s["approved_plan_sha"]) and sha == s["approved_plan_sha"],
        )
    )
    items.append(("selfcheck GO on head", _tick_ok(s, "selfcheck")))
    items.append(_test_item(project, s, "t1", "t1 GO on head"))
    if (cfg.get("tests") or {}).get("t2"):
        items.append(_test_item(project, s, "t2", "t2 GO on head"))
    if s["full_suite_required"]:
        items.append(_test_item(project, s, "full", "full suite GO on head"))
    if s["risk"] not in TIERS:
        return items + [("risk is R0, R1 or R2", False)]
    prev_run, prev_at = None, None
    for i, entry in enumerate(cfg["reviews"][s["risk"]]["code"]):
        name, _cap = projmod.reviewer_entry(entry)
        label = f"code review {name} GO on head"
        e = (s["evidence"].get(s["head"]) or {}).get(f"code:{name}")
        if not e or e["verdict"] != "GO":
            items.append((label, False))
            prev_run = prev_at = None
            continue
        problem, req = _verify_run(
            project,
            s,
            e,
            kinds=_reviewer_kinds(cfg, name),
            expect={"purpose": "code", "reviewer": name, "head": s["head"]},
        )
        if problem is None and i > 0:
            if req.get("predecessor_run") != prev_run:
                problem = "predecessor run does not match the previous reviewer"
            elif prev_at is None or e["at"] < prev_at:
                problem = "recorded before the previous reviewer"
        items.append(_item(label, problem))
        prev_run, prev_at = (e["run"], e["at"]) if problem is None else (None, None)
    return items


def _checks(project, s, cfg, to):
    frm = s["phase"]
    if (frm, to) == ("FRAME", "PLAN"):
        return [
            ("risk is R0, R1 or R2 (R3 waits for the owner)", s["risk"] in TIERS),
            ("rulebooks listed", bool(s["rulebooks"])),
        ]
    if (frm, to) == ("FRAME", "HELD") and s["risk"] == "R3":
        return [("a question for the owner is open", bool(s["waiting_on"]))]
    if (frm, to) == ("PLAN", "DEVELOP"):
        return _plan_items(project, s, cfg)
    if (frm, to) == ("DEVELOP", "SELF_CHECK"):
        return [
            ("branch set", bool(s["branch"])),
            ("head set", bool(s["head"])),
            (
                "applied builder run DONE on head",
                bool(s["head"]) and _builder_run_ok(project, s),
            ),
        ]
    if (frm, to) == ("SELF_CHECK", "VERIFY"):
        return [("selfcheck GO on head", bool(s["head"]) and _tick_ok(s, "selfcheck"))]
    if (frm, to) == ("VERIFY", "REVIEW"):
        if not s["head"]:
            return [("head set", False)]
        return [_test_item(project, s, "t1", "t1 GO on head")]
    if (frm, to) in (("REVIEW", "GATE"), ("GATE", "DOCUMENT")):
        return _gate_items(project, s, cfg)
    if (frm, to) == ("DOCUMENT", "DONE"):
        return [("docs GO on head", bool(s["head"]) and _tick_ok(s, "docs"))]
    return []


def exit_checklist(project: str, key: str, to_phase=None) -> list:
    """Named exit items for a transition, met or missing; `move` refuses on the same list."""
    s = load(project, key)
    cfg = projmod.load_project(project)
    to = to_phase or FORWARD.get(s["phase"])
    return [] if to is None else _checks(project, s, cfg, to)


def validate_gate(project: str, key: str) -> list:
    """The one evidence check, shared by REVIEW to GATE and the merge command."""
    s = load(project, key)
    cfg = projmod.load_project(project)
    return [name for name, ok in _gate_items(project, s, cfg) if not ok]


# --- moving -------------------------------------------------------------------


def _move(project, key, to, reason, via_merge=False):
    if to not in PHASES:
        raise TransitionError("unknown phase")
    cfg = projmod.load_project(project)
    reason = str(reason)[:500]
    with _edit(project, key) as s:
        frm = s["phase"]
        if via_merge:
            if (frm, to) != ("GATE", "DOCUMENT"):
                raise TransitionError("only GATE to DOCUMENT is a merge move")
        elif (frm, to) == ("GATE", "DOCUMENT"):
            raise TransitionError(
                "GATE to DOCUMENT happens only through the merge command"
            )
        if to not in TRANSITIONS[frm]:
            raise TransitionError(f"{frm} to {to} is not allowed")
        if frm in ("PARKED", "HELD"):
            if to != s["parked_from"]:
                raise TransitionError(f"{frm} resumes only to {s['parked_from']}")
            gated = frm == "HELD" or s["park_reason"] in OWNER_PARKS
            if gated and not s["owner_answered"]:
                raise TransitionError(
                    "the owner must answer first (devloop-state answer)"
                )
        missing = [name for name, ok in _checks(project, s, cfg, to) if not ok]
        if missing:
            raise TransitionError("; ".join(missing))

        parked_from = frm
        if (frm, to) == ("PLAN", "DEVELOP"):
            s["approved_plan_sha"] = plan_sha(s)
        if (frm, to) == ("REVIEW", "REMEDIATE"):
            s["round"] += 1
            if s["round"] >= MAX_ROUNDS:
                to, reason, parked_from = "PARKED", "round-3", "REMEDIATE"
        if to in ("PARKED", "HELD"):
            s["parked_from"], s["park_reason"] = parked_from, reason
        elif frm in ("PARKED", "HELD"):
            s["parked_from"] = s["park_reason"] = None
            s["owner_answered"] = False
        if to == "FRAME":
            s["approved_plan_sha"], s["plan_frozen"] = None, False
            if not store.is_headless():
                # A new lineage earns every review again. Only an interactive
                # reframe may reset the counts: a headless one could dodge round 3.
                s["round"], s["plan_rounds"], s["evidence"] = 0, {}, {}
        now = store.now_iso()
        s["phase"], s["last_moved_at"] = to, now
        s["history"] = (
            s["history"] + [{"at": now, "from": frm, "to": to, "reason": reason}]
        )[-HISTORY_KEEP:]
    store.append_event(
        project,
        "slice-moved",
        key=key,
        row=s["row"],
        slice=s["slice"],
        to=to,
        **{"from": frm},
    )
    _mirror(project, cfg, s)
    return copy.deepcopy(s)


def move(project: str, key: str, to_phase: str, reason: str) -> dict:
    return _move(project, key, to_phase, reason)


def finish_merge(project: str, key: str, reason: str = "merged") -> dict:
    """GATE to DOCUMENT; only the merge command calls this, after the PR is confirmed merged."""
    return _move(project, key, "DOCUMENT", reason, via_merge=True)


def release_hold(project: str, key: str) -> dict:
    """Release a HELD slice after the owner's answer. R3 then runs under the R2 roster."""
    store.require_interactive("releasing a held slice")
    cfg = projmod.load_project(project)
    with _edit(project, key) as s:
        if s["phase"] != "HELD":
            raise SliceError("slice is not held")
        to = s["parked_from"] or "FRAME"
        if s["risk"] == "R3":
            s["risk"] = "R2"
        now = store.now_iso()
        s["history"] = (
            s["history"]
            + [{"at": now, "from": "HELD", "to": to, "reason": "owner answered"}]
        )[-HISTORY_KEEP:]
        s.update(
            waiting_on=None,
            parked_from=None,
            park_reason=None,
            owner_answered=False,
            phase=to,
            last_moved_at=now,
        )
    store.append_event(project, "slice-released", key=key, to=to)
    _mirror(project, cfg, s)
    return copy.deepcopy(s)


def owner_answer(project: str, key: str) -> None:
    """Record the owner's answer on a held or owner-parked slice (interactive only)."""
    store.require_interactive("answering for a held slice")
    with _edit(project, key) as s:
        if s["phase"] == "HELD" or (
            s["phase"] == "PARKED" and s["park_reason"] in OWNER_PARKS
        ):
            s["owner_answered"] = True
