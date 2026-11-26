"""Slices: phases, transitions, and the evidence that gates each move.

Only this module writes slice files. Every write is a load, check, replace under one
flock, so a refused move leaves the file byte for byte as it was.
"""

import contextlib
import copy
import hashlib
import re
import subprocess
from pathlib import Path

from . import project as projmod
from . import board, evidence, notes, store

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
# Review rounds never park a slice. A round is one REVIEW to REMEDIATE move. Rounds 1 to
# 3 are fixed by the Claude builder, rounds 4 to 6 by Codex, and the move that would be
# round 7 sends the slice back to PLAN to redesign.
CODEX_ROUNDS_FROM = 4
REDESIGN_AT = 7
# Parks that only the owner may lift.
OWNER_PARKS = ("failed-twice",)
HISTORY_KEEP = 200
HOLD_RELEASE_OPTION = "approve"

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
ID_RE = re.compile(_ID)
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
    "plan_lineage": _nat,
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
    "head_writer": lambda v: v is None or v in WRITER_KINDS or _writer_record(v),
    "owner_answered": _bool,
    "hold_question": _ostr,
    "hold_release_option": _ostr,
    "was_r3": _bool,
    "processed_run_ids": _strlist,
}


WRITER_KINDS = ("builder", "codex-builder")


def _writer_record(v) -> bool:
    """One record binds who wrote a head to the commit and the plan it was written for."""
    return (
        isinstance(v, dict)
        and v.get("kind") in (*WRITER_KINDS, "imported")
        and isinstance(v.get("sha"), str)
        and bool(v["sha"])
        and _nat(v.get("plan_lineage"))
    )


def writer_record(kind, sha, plan_lineage=0) -> dict:
    return {"kind": kind, "sha": sha, "plan_lineage": plan_lineage}


def _validate(s) -> dict:
    if not isinstance(s, dict):
        _bad("slice")
    s.setdefault("head_writer", None)  # files written before the field existed
    for name, check in _FIELDS.items():
        if name not in s or not check(s[name]):
            _bad(name)
    return s


def slice_key(row: str, slice_id: str) -> str:
    return f"{row}--{slice_id}"


def _slices_dir(project: str) -> Path:
    # Writers create it through store.locked; readers must not.
    return store.state_dir(project) / "slices"


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


def load_all(project: str):
    """Every slice that loads, plus the file stems of those that do not.

    The dashboard reads this way so one damaged file cannot hide the healthy ones.
    """
    good, bad = [], []
    for path in sorted(_slices_dir(project).glob("*.json")):
        try:
            good.append(load(project, path.stem))
        except (SliceError, store.StoreError):
            bad.append(path.stem)
    return good, bad


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
        "plan_lineage": 0,
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
        "head_writer": None,
        "owner_answered": False,
        "hold_question": None,
        "hold_release_option": None,
        "was_r3": risk == "R3",
        "processed_run_ids": [],
    }


def _create(project, row, slice_id, repo, risk=None, **overrides) -> dict:
    for part in (row, slice_id):
        if not isinstance(part, str) or ID_RE.fullmatch(part) is None:
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
        _save(project, s)
    store.append_event(project, "slice-new", key=s["key"], row=row, slice=slice_id)
    board.mirror(project, cfg, s)
    return s


def new_slice(project: str, row: str, slice_id: str, *, repo: str, risk=None) -> dict:
    return _create(project, row, slice_id, repo, risk)


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
    if head:
        head = resolve_head(project, head, worktree=worktree, repo=repo)

    merged = False
    if pr is not None and head:
        # The same repository slug, effective base and PR identity check the merge uses.
        from . import merge

        base = merge.repo_settings(cfg, repo)[2]
        merged = merge.confirm_pr(cfg, repo, pr, base, head) == "merged"

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
        if head:  # the owner vouches for this commit only, under the plan just approved
            overrides["head_writer"] = writer_record("imported", head)
    return _create(project, row, slice_id, repo, risk, **overrides)


# --- editing fields -----------------------------------------------------------


def plan_sha(s: dict) -> str:
    sha = plan_sha_or_none(s)
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


def plan_sha_or_none(s):
    f = _plan_file(s)
    if f is None or not f.is_file():
        return None
    return hashlib.sha256(f.read_bytes()).hexdigest()


def _rank(risk):
    return -1 if risk is None else RISKS.index(risk) if risk in RISKS else -1


def resolve_head(project: str, head: str, *, worktree=None, repo=None) -> str:
    """The full 40-hex commit a head names, resolved in the slice's repo.

    A short SHA or a branch name could mean a different commit later, so only the
    resolved form is stored.
    """
    cfg = projmod.load_project(project)
    path = worktree or next(
        (r["path"] for r in cfg["repos"] if r["name"] == repo), cfg["repos"][0]["path"]
    )
    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "rev-parse",
                "--verify",
                "--quiet",
                f"{head}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise SliceError("could not resolve the head") from None
    full = r.stdout.strip()
    if r.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", full) is None:
        raise SliceError("head is not a commit in the slice's repo")
    return full


def set_fields(project: str, key: str, **fields) -> dict:
    unknown = sorted(set(fields) - set(SETTABLE))
    if unknown:
        raise SliceError(f"unknown field: {', '.join(unknown)}")
    pending = load(project, key)
    if pending["merge_attempt"] and ("head" in fields or "pr" in fields):
        raise SliceError(
            "a merge is pending; head and pr cannot change until it settles"
        )
    if fields.get("head") is not None:
        current = load(project, key)
        fields["head"] = resolve_head(
            project,
            fields["head"],
            worktree=fields.get("worktree") or current["worktree"],
            repo=current["repo"],
        )
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


plan_file = _plan_file


def set_merge_state(project: str, key: str, *, attempt=..., unknown=None) -> None:
    """Merge bookkeeping: the attempt record and the unknown-state flag (merge command only)."""
    with _edit(project, key) as s:
        if attempt is not ...:
            s["merge_attempt"] = attempt
        if unknown is not None:
            s["merge_unknown"] = bool(unknown)


def merge_reset(project: str, key: str) -> None:
    """Owner only: clear a stuck merge attempt or unknown-state flag after reading the PR."""
    store.require_interactive("merge-reset")
    set_merge_state(project, key, attempt=None, unknown=False)
    store.append_event(project, "merge-reset", key=key)


def set_run(project: str, key: str, run_id) -> None:
    """Dispatch records the slice's current run; apply clears it."""
    with _edit(project, key) as s:
        s["run"] = run_id


def apply_effects(
    project: str,
    key: str,
    run_id: str,
    *,
    kind=None,
    verdict=None,
    head=None,
    plan_sha_=None,
    failure=None,
    want_head=None,
    want_plan_sha=None,
    seq=None,
    want_lineage=None,
    writer=None,
) -> str:
    """Everything a run changes on a slice, plus its ID, in ONE locked replace.

    Returns "replay" when the ID was already processed, "stale" when the slice moved
    on (new head or edited plan) and the result must not count, else "applied".
    """
    with _edit(project, key) as s:
        if run_id in s["processed_run_ids"]:
            return "replay"
        outcome = "applied"
        if want_head is not None and want_head != s["head"]:
            outcome = "stale"
        if want_plan_sha is not None and want_plan_sha != plan_sha_or_none(s):
            outcome = "stale"
        if want_lineage is not None and want_lineage != s["plan_lineage"]:
            outcome = "stale"  # dispatched before a redesign
        if outcome == "applied":
            if kind:
                if kind in TICK_KINDS or verdict not in VERDICTS:
                    raise SliceError("bad evidence")
                _put_evidence(s, kind, verdict, run_id, sha=head or plan_sha_, seq=seq)
            if failure:
                s["failures"][failure] = s["failures"].get(failure, 0) + 1
            if writer:
                s["head_writer"] = writer  # one record: kind, commit, lineage
        s["processed_run_ids"] = (s["processed_run_ids"] + [run_id])[-500:]
        if s["run"] == run_id:
            s["run"] = None
        return outcome


def mark_plan_started(project: str, key: str) -> None:
    """Called by dispatch under admission when the first plan run is created."""
    with _edit(project, key) as s:
        s["plan_frozen"] = True


# --- evidence -----------------------------------------------------------------


def _put_evidence(s, kind, verdict, run_id, *, sha, extra=None, seq=None):
    """Mutate `s` in place; the same run recorded twice changes nothing.

    `seq` is the run's dispatch sequence. A result from an earlier dispatch never
    replaces evidence from a later one for the same head and check, whatever order
    the two are applied in.
    """
    bucket = s["evidence"].setdefault(sha, {})
    prior = bucket.get(kind)
    if run_id is not None and prior is not None and prior.get("run") == run_id:
        return
    if seq is not None and prior is not None and (prior.get("seq") or 0) > seq:
        return
    entry = {"verdict": verdict, "run": run_id, "at": store.now_iso(), **(extra or {})}
    if seq is not None:
        entry["seq"] = seq
    if kind.startswith("plan:"):
        if not s["plan_path"]:
            raise SliceError("plan evidence needs a plan_path")
        entry["plan_path"] = s["plan_path"]
        entry["plan_lineage"] = s["plan_lineage"]
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
    head = resolve_head(
        project,
        head,
        worktree=load(project, key)["worktree"],
        repo=load(project, key)["repo"],
    )
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


# --- moving -------------------------------------------------------------------


def _move(project, key, to, reason, via_merge=False, ident=None):
    if to not in PHASES:
        raise TransitionError("unknown phase")
    cfg = projmod.load_project(project)
    reason = str(reason)[:500]
    with _edit(project, key) as s:
        frm = s["phase"]
        if via_merge:
            if (frm, to) != ("GATE", "DOCUMENT"):
                raise TransitionError("only GATE to DOCUMENT is a merge move")
            if ident is not None:
                # What GitHub confirmed must be what this slice still holds.
                if (s["repo"], s["pr"], s["head"]) != (
                    ident["repo"],
                    ident["pr"],
                    ident["head"],
                ):
                    raise TransitionError(
                        "the merged PR is not this slice's current PR and head"
                    )
                board.record_merge(project, ident, s)
                s["merge_attempt"], s["merge_unknown"] = None, False
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
        # GitHub already confirmed the merge, so a plan or evidence edit made after it
        # must not strand the slice at GATE.
        missing = (
            []
            if via_merge
            else [i.label for i in evidence.checks(project, s, cfg, to) if not i.ok]
        )
        if missing:
            raise TransitionError("; ".join(missing))

        parked_from = frm
        if (frm, to) == ("PLAN", "DEVELOP"):
            s["approved_plan_sha"] = plan_sha(s)
        if (frm, to) == ("REVIEW", "REMEDIATE"):
            s["round"] += 1
            if s["round"] >= REDESIGN_AT:
                # Six rounds did not converge: change the plan, keep the PR and worktree.
                to, reason = "PLAN", "redesign"
                s["round"], s["plan_rounds"] = 0, {}
                s["plan_frozen"], s["approved_plan_sha"] = False, None
                # Reviews of the old plan, even of the same bytes or path, stop counting:
                # the new lineage earns every plan review again, Dennis first.
                s["plan_lineage"] += 1
                # Tick records and code reviews of the old design go too, even on a head
                # that did not change.
                for sha, bucket in list(s["evidence"].items()):
                    for kind in [
                        k
                        for k in bucket
                        if k.startswith(("plan:", "code:")) or k in TICK_KINDS
                    ]:
                        del bucket[kind]
                    if not bucket:
                        del s["evidence"][sha]
        if to in ("PARKED", "HELD"):
            s["parked_from"], s["park_reason"] = parked_from, reason
            if to == "HELD" or reason in OWNER_PARKS:
                # Only this question and this option may lift the hold.
                s["hold_question"] = "hold:" + key
                s["hold_release_option"] = HOLD_RELEASE_OPTION
        elif frm in ("PARKED", "HELD"):
            s["parked_from"] = s["park_reason"] = None
            s["owner_answered"] = False
            s["hold_question"] = s["hold_release_option"] = None
        if to == "FRAME":
            s["approved_plan_sha"], s["plan_frozen"] = None, False
            if not store.is_headless():
                # A new lineage earns every review again. Only an interactive
                # reframe may reset the counts: a headless one could dodge the round ladder.
                s["round"], s["plan_rounds"], s["evidence"] = 0, {}, {}
        now = store.now_iso()
        s["phase"], s["last_moved_at"] = to, now
        s["history"] = (
            s["history"] + [{"at": now, "from": frm, "to": to, "reason": reason}]
        )[-HISTORY_KEEP:]
    if to == "HELD" or (to == "PARKED" and reason in OWNER_PARKS):
        notes.ensure_hold_question(project, key)
    store.append_event(
        project,
        "slice-moved",
        key=key,
        row=s["row"],
        slice=s["slice"],
        to=to,
        **{"from": frm},
    )
    board.mirror(project, cfg, s)
    return copy.deepcopy(s)


def move(project: str, key: str, to_phase: str, reason: str) -> dict:
    return _move(project, key, to_phase, reason)


def finish_merge(project: str, key: str, reason: str = "merged", ident=None) -> dict:
    """GATE to DOCUMENT; only the merge command calls this, after the PR is confirmed merged.

    With `ident` (repo, slug, pr, head) the identity is checked and the completion
    recorded in the same locked step as the move.
    """
    return _move(project, key, "DOCUMENT", reason, via_merge=True, ident=ident)


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
            hold_question=None,
            hold_release_option=None,
            phase=to,
            last_moved_at=now,
        )
    store.append_event(project, "slice-released", key=key, to=to)
    board.mirror(project, cfg, s)
    return copy.deepcopy(s)


def owner_answer(project: str, key: str) -> None:
    """Record the owner's answer on a held or owner-parked slice (interactive only)."""
    store.require_interactive("answering for a held slice")
    with _edit(project, key) as s:
        if s["phase"] == "HELD" or (
            s["phase"] == "PARKED" and s["park_reason"] in OWNER_PARKS
        ):
            s["owner_answered"] = True


_MOVED = {
    "validate_gate": evidence,
    "exit_checklist": evidence,
    "exit_items": evidence,
    "board_merged": board,
    "deliver_board": board,
    "merge_records": board,
    "merge_id": board,
    "pending_board": board,
    "row_close": board,
    "closable_rows": board,
    "close_row_checked": board,
    "drain_pending_board": board,
    "closure_records": board,
}


def __getattr__(name):
    """Keep the old public names importable from here after the split."""
    if name in _MOVED:
        return getattr(_MOVED[name], name)
    raise AttributeError(name)
