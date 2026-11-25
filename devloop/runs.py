"""Runs: dispatch, admission, liveness, result parsing and apply.

A run is a directory under `runs/`. Its `request.json` is frozen at admission; the
wrapper adds the process fields and the exit file. Evidence reaches a slice only
through `apply_run`, which reads the frozen request, never the slice's phase.
"""

import collections
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
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
    d = store.state_dir(project) / "runs"
    d.mkdir(exist_ok=True)
    return d


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
    for rd in sorted(_runs_dir(project).iterdir()):
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


def live_runs(project) -> list:
    return [(rd.name, req) for rd, req in iter_runs(project) if holds_slot(rd, req)]


def writer_runs(project) -> list:
    return [
        (rd.name, req)
        for rd, req in iter_runs(project)
        if KINDS.get(req.get("kind"), KINDS["docs"]).writes_worktree and not is_done(rd)
    ]


# --- collect ------------------------------------------------------------------


def create_exit(rd, *, code, reason) -> bool:
    """First writer wins, and `exit` is never visible half-written.

    The record goes to a temp file first; os.link then publishes it under the final name
    only if that name is free, so a crash cannot leave an empty or partial `exit`.
    """
    tmp = rd / f".exit.{os.getpid()}.tmp"
    tmp.write_text(
        json.dumps({"code": code, "reason": reason, "ended_at": store.now_iso()})
    )
    try:
        os.link(tmp, rd / "exit")
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)
    return True


def exit_valid(rd) -> bool:
    """An exit record with an int code and a reason; anything else is damaged."""
    try:
        ex = store.read_json(rd / "exit")
    except store.StoreError:
        return False
    return isinstance(ex.get("code"), int) and isinstance(ex.get("reason"), str)


def collect(project) -> list:
    """Mark dead no-exit runs lost; return finished runs not yet applied."""
    done = []
    # Under the admission lock so a run still being launched is never read half-made.
    with store.locked(store.state_dir(project) / "admit"):
        for rd, req in iter_runs(project):
            damaged = (rd / "exit").is_file() and not exit_valid(rd)
            if damaged and not wrapper_alive(req):
                (rd / "exit").unlink(missing_ok=True)  # recovered below as a lost run
            if not (rd / "exit").is_file() and not wrapper_alive(req):
                kill_payload(req)
                create_exit(rd, code=-1, reason="lost")
                remove_tree(project, rd.name, req)
            if (rd / "exit").is_file() and not is_done(rd):
                done.append(rd.name)
    return done


def release(project, run_id) -> None:
    """The tick calls this after a node check proves a lost test run is really gone."""
    rd = store.run_dir(project, run_id)
    req = read_request(project, run_id)
    if req.get("kind") != "test" or not (rd / "exit").is_file():
        raise RunError("only an ended test run can be released")
    (rd / "released").write_text("")
    store.append_event(project, "run-released", run=run_id)


# --- results ------------------------------------------------------------------


def parse_result(run_dir: Path, kind: str) -> Result:
    """Read result.md against the exit file. A contradiction is FAILED, never DONE."""
    if kind not in KINDS:
        raise RunError("unknown kind")
    hdr = store.read_header(store.out_dir(run_dir) / "result.md")
    ex = read_exit(run_dir)
    status = hdr.get("status")
    body = ""
    try:
        text = (store.out_dir(run_dir) / "result.md").read_text(
            encoding="utf-8", errors="replace"
        )
        body = text.partition("\n---")[2].strip()
    except OSError:
        pass
    verdict = hdr.get("verdict") or None
    if verdict not in slices.VERDICTS:
        verdict = None
    if status not in STATUSES:
        status = "FAILED"
    elif status in ("DONE", "BLOCKED") and ex.get("code") != 0:
        # A test that ran and failed (exit 1) is a real NO-GO, not a broken run: it must
        # replace any earlier GO at that level and head.
        red_test = (
            kind == "test"
            and status == "DONE"
            and verdict == "NO-GO"
            and ex.get("code") == 1
            and ex.get("reason") == "exit"
        )
        if not red_test:
            status = "FAILED"
    elif (
        status == "DONE"
        and KINDS[kind].reader not in ("claude", "exit")
        and verdict is None
    ):
        status = "FAILED"
    return Result(
        status,
        hdr.get("head") or None,
        hdr.get("pr") or None,
        verdict,
        hdr.get("tests") or None,
        hdr.get("next") or None,
        body[:2000],
        ex.get("reason") or "",
    )


# --- admission ----------------------------------------------------------------


def mem_available_kb() -> int:
    fake = os.environ.get("DEVLOOP_FAKE_MEMAVAIL_KB")
    if fake:
        return int(fake)
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    return 0


def _git(cwd, *args, check=True):
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if check and r.returncode != 0:
        raise Refused(f"git {args[0]} failed")
    return r.stdout.strip()


def worktree_root(path) -> str:
    return os.path.realpath(_git(path, "rev-parse", "--show-toplevel"))


def _pause_or_cap(project):
    d = store.state_dir(project)
    if (d / "PAUSE").exists():
        raise Refused("paused")
    cap = d / "cap-until"
    if cap.is_file():
        until = None
        try:
            until = datetime.datetime.fromisoformat(cap.read_text().strip())
        except ValueError:
            pass
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=datetime.timezone.utc)
        # An unreadable cap file blocks: better a pause than a burst at a capped service.
        if until is None or until > datetime.datetime.now(datetime.timezone.utc):
            raise Refused("usage cap active")


def _sha_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _applied(project, run_id) -> bool:
    return (store.run_dir(project, run_id) / "applied").is_file()


def _predecessor(project, s, cfg, purpose, name):
    """The run that satisfied the previous reviewer in the roster, or Refused."""
    roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]][purpose]]
    names = [n for n, _ in roster]
    if name not in names:
        raise Refused(f"{name} is not in the {s['risk']} {purpose} roster")
    i = names.index(name)
    if i == 0:
        return None
    pname, pcap = roster[i - 1]
    if purpose == "code":
        e = (s["evidence"].get(s["head"]) or {}).get(f"code:{pname}")
        if not e or e["verdict"] != "GO" or not _applied(project, e["run"]):
            raise Refused(f"{pname} has no applied GO on this head yet")
        return e["run"]
    mine = sorted(
        (
            e
            for bucket in s["evidence"].values()
            for k, e in bucket.items()
            if k == f"plan:{pname}" and e.get("plan_path") == s["plan_path"]
        ),
        key=lambda e: e["at"],
    )
    gos = [e for e in mine if e["verdict"] == "GO"]
    rounds = s["plan_rounds"].get(s["plan_path"], {}).get(pname, 0)
    capped = pcap is not None and rounds >= pcap
    pick = gos[-1] if gos else (mine[-1] if mine and capped else None)
    if pick is None or not _applied(project, pick["run"]):
        raise Refused(f"{pname} has not finished the plan review yet")
    return pick["run"]


def _own_cap_reached(s, cfg, name):
    roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]]["plan"]]
    for i, (n, cap) in enumerate(roster):
        if n == name and cap and i < len(roster) - 1:
            return s["plan_rounds"].get(s["plan_path"], {}).get(name, 0) >= cap
    return False


def _fill(argv, mapping):
    return [
        re.sub(r"\{(\w+)\}", lambda m: str(mapping.get(m.group(1), m.group(0))), a)
        for a in argv
    ]


def _claude_argv(model, *extra):
    return [
        "claude",
        "-p",
        *extra,
        "--model",
        model,
        "--permission-mode",
        "auto",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def _duty_name(cfg, key):
    m = DUTY_KEY.fullmatch(key)
    if not m:
        raise Refused("bad duty key")
    for name in cfg.get("duties") or {}:
        if name.lower() == m.group(1).lower():
            return name
    raise Refused("unknown duty")


def _skeleton(kind, key):
    spec = KINDS[kind]
    return {
        "kind": kind,
        "key": key,
        "reviewer": None,
        "level": None,
        "select": [],
        "covers": {},
        "predecessor_run": None,
        "plan_sha": None,
        "head": None,
        "purpose": None,
        "host": None,
        "duty": None,
        "model": None,
        "deadline_s": spec.deadline_s,
        "idle_s": spec.idle_s,
        "slot": spec.slot,
        "reader": spec.reader,
        "infra_codes": [],
    }


def _require_commit(wt, head):
    """Pinned runs use a private checkout at the head, so the builder's worktree may be
    dirty or ahead. The commit only has to exist."""
    r = subprocess.run(
        ["git", "-C", str(wt), "cat-file", "-e", f"{head}^{{commit}}"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if r.returncode != 0:
        raise Refused("the slice head is not a commit in the worktree")


def _prepare_review(project, cfg, s, req, kind, reviewer, wt):
    if s["risk"] not in slices.TIERS:
        raise Refused("risk is not set")
    spec_r = cfg["reviewers"].get(reviewer or "")
    if spec_r is None:
        raise Refused("unknown reviewer")
    if kind not in (spec_r.get("kinds") or [spec_r["kind"]]):
        raise Refused(f"reviewer {reviewer} may not run {kind}")
    req["reviewer"] = reviewer
    purpose = "plan" if (kind == "codex-fix" or s["phase"] == "PLAN") else "code"
    req["purpose"] = purpose
    argv = (spec_r.get("argv") or {}).get(kind)
    if argv is None and kind == "dennis":
        argv = _claude_argv(cfg["models"]["reviewer"], "--agent", reviewer)
    if argv is None:
        raise Refused("no command configured for this reviewer and kind")
    req["argv_t"] = list(argv)
    if purpose == "plan":
        pf = slices.plan_file(s)
        if pf is None or not pf.is_file():
            raise Refused("plan file missing")
        req["plan_sha"] = slices.plan_sha(s)
        if _own_cap_reached(s, cfg, reviewer):
            raise Refused(f"{reviewer} is at its plan round cap")
    else:
        if not s["head"]:
            raise Refused("slice has no head")
        req["head"] = s["head"]
        _require_commit(wt, s["head"])
    req["predecessor_run"] = _predecessor(project, s, cfg, purpose, reviewer)
    if kind == "codex-fix":
        req["repair_out_t"] = spec_r.get("repair_out")
        if not req["repair_out_t"]:
            raise Refused("no repair_out configured")


def _prepare_test(project, cfg, s, req, level, select, covers, wt):
    spec_t = (cfg.get("tests") or {}).get(level or "")
    if not spec_t or not spec_t.get("argv"):
        raise Refused("no command configured for this test level")
    if not s["head"]:
        raise Refused("slice has no head")
    if select and level in ("t2", "full"):
        raise Refused(f"{level} runs the whole level; a selection cannot earn it")
    req.update(level=level, head=s["head"], purpose="code", select=list(select))
    req["argv_t"] = list(spec_t["argv"]) + [
        x for item in select for x in ("--select", item)
    ]
    req["deadline_s"] = spec_t.get("deadline_s", KINDS["test"].deadline_s)
    req["host"] = spec_t.get("host")
    req["infra_codes"] = list(spec_t.get("infra_codes", []))
    if covers and level != "full":
        raise Refused("only a full run covers other slices")
    frozen = {}
    for ckey in covers:
        other = slices.load(project, ckey)
        if other["repo"] != s["repo"]:
            raise Refused("a covered slice is in another repo")
        stacked = (
            bool(other["head"])
            and subprocess.run(
                [
                    "git",
                    "-C",
                    str(wt),
                    "merge-base",
                    "--is-ancestor",
                    other["head"],
                    s["head"],
                ],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )
        if not stacked:
            raise Refused(f"{ckey} head is not an ancestor of the tested head")
        frozen[ckey] = other["head"]
    req["covers"] = frozen


def _prepare(project, cfg, kind, key, worktree, reviewer, level, select, covers):
    """Validate everything dispatch must freeze. Returns (request skeleton, slice)."""
    req = _skeleton(kind, key)
    if kind == "duty":
        name = _duty_name(cfg, key)
        row, _, sl = key.partition("--")
        req.update(row=row, slice=sl, duty=name, purpose="code")
        req["deadline_s"] = cfg["duties"][name].get(
            "deadline_s", KINDS["duty"].deadline_s
        )
        req["argv_t"] = list(cfg["duties"][name]["argv"])
        req["worktree"] = str(worktree) if worktree else None
        return req, None
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        raise Refused("no such slice") from None
    req.update(row=s["row"], slice=s["slice"], plan_path=s["plan_path"])
    req["lineage"] = f"{s['round']}:{s['last_moved_at']}"
    if s["phase"] not in KIND_PHASES[kind]:
        raise Refused(f"{kind} does not run in {s['phase']}")
    wt = worktree or s["worktree"]
    if not wt or not Path(wt).is_dir():
        raise Refused("no worktree")
    req["worktree"] = str(wt)
    if kind in REVIEW_KINDS:
        _prepare_review(project, cfg, s, req, kind, reviewer, wt)
    elif kind == "test":
        _prepare_test(project, cfg, s, req, level, select, covers, wt)
    else:
        role = "builder" if kind == "builder" else "planner"
        model = cfg["models"][role]
        if role == "planner" and "opus" not in model.lower():
            raise Refused("planner and docs need an Opus model")
        req.update(model=model, argv_t=_claude_argv(model), head=s["head"])
        req["purpose"] = "author" if kind == "planner" else "code"
        if kind == "planner":
            if not s["plan_path"]:
                raise Refused("set plan_path first")
            pf = slices.plan_file(s)
            req["plan_sha"] = slices.plan_sha(s) if pf and pf.is_file() else None
    return req, s


def _check_admission(project, cfg, req, root):
    spec = KINDS[req["kind"]]
    if spec.slot:
        used = sum(
            1 for _, r in live_runs(project) if KINDS[r["kind"]].slot == spec.slot
        )
        if used >= cfg["capacity"][spec.slot]:
            raise Refused(f"no free {spec.slot} slot")
    if spec.writes_worktree:
        for rid, r in writer_runs(project):
            if r.get("root") == root:
                raise Refused(f"worktree is reserved by {rid}")
    for rd, r in iter_runs(project):
        if (
            req["kind"] in REVIEW_KINDS
            and req["purpose"] == "plan"
            and r.get("key") == req["key"]
            and r.get("purpose") == "plan"
            and r.get("kind") in REVIEW_KINDS
            and not is_done(rd)
        ):
            raise Refused("another plan review is still open for this slice")
        if req["kind"] == "duty" and r.get("key") == req["key"] and r["kind"] == "duty":
            raise Refused("duty already dispatched")


def dispatch(
    project,
    kind,
    key,
    *,
    brief,
    worktree=None,
    reviewer=None,
    level=None,
    select=(),
    covers=(),
    retry_of=None,
) -> str:
    if kind not in KINDS:
        raise Refused("unknown kind")
    cfg = projmod.load_project(project)
    brief = Path(brief)
    if not brief.is_file():
        raise Refused("brief file not found")
    if retry_of is not None and store.TOKEN.fullmatch(retry_of) is None:
        raise Refused("bad retry-of")
    req, s = _prepare(
        project, cfg, kind, key, worktree, reviewer, level, list(select), list(covers)
    )
    spec = KINDS[kind]
    body = brief.read_text(encoding="utf-8")

    with store.locked(store.state_dir(project) / "admit"):
        _pause_or_cap(project)
        if mem_available_kb() < cfg.get("min_free_gb", 2.5) * 1024 * 1024:
            raise Refused("memory is below the floor")
        req["root"] = worktree_root(req["worktree"]) if spec.writes_worktree else None
        _check_admission(project, cfg, req, req["root"])
        run_id = "{}.{}.{}".format(
            kind, key, time.strftime("%Y%m%dT%H%M%S") + secrets.token_hex(2)
        )
        rd = store.run_dir(project, run_id)
        rd.mkdir(parents=True)
        os.chmod(rd, 0o700)
        try:
            _freeze(project, req, s, rd, run_id, retry_of, body)
            if kind != "duty" and req["purpose"] in ("author", "plan"):
                slices.mark_plan_started(project, key)
            _launch(project, rd, run_id)
            if kind != "duty":
                slices.set_run(project, key, run_id)
        except BaseException:
            remove_tree(project, run_id, req)
            shutil.rmtree(rd, ignore_errors=True)
            raise
    store.append_event(
        project,
        "run-dispatched",
        run=run_id,
        run_kind=kind,
        key=key,
        slot=spec.slot or "",
    )
    return run_id


def _freeze(project, req, s, rd, run_id, retry_of, body):
    """Fill in paths, copy inputs, make the private checkout, write brief and request."""
    kind = req["kind"]
    spec = KINDS[kind]
    req.update(
        id=run_id,
        retry_of=retry_of,
        started_at=store.now_iso(),
        out=str(store.out_dir(rd) / "codex.out"),
        last_msg=str(store.out_dir(rd) / "final.md"),
        run_dir=str(store.out_dir(rd)),
        job=f"{store.state_dir(project).name}-{run_id}",
    )
    store.out_dir(rd).mkdir()
    plan_copy = None
    if req["plan_sha"] and s is not None:
        (rd / "plan").mkdir()
        src = slices.plan_file(s)
        plan_copy = rd / "plan" / src.name
        shutil.copyfile(src, plan_copy)
        os.chmod(plan_copy, 0o444)
        req["plan_copy"] = str(plan_copy)
        # Credit goes to the bytes the worker is given, not to the live file, which
        # may have changed since admission read it.
        req["plan_sha"] = hashlib.sha256(plan_copy.read_bytes()).hexdigest()
    if kind == "codex-fix":
        req["repair_out"] = os.path.expanduser(
            req.pop("repair_out_t").format(run_id=run_id, plan_name=plan_copy.name)
        )
    plan_review = req["purpose"] == "plan" and kind != "codex-fix"
    if spec.pinned and req["purpose"] == "code":
        tree = rd / "tree"
        made = subprocess.run(
            [
                "git",
                "-C",
                req["worktree"],
                "worktree",
                "add",
                "--detach",
                str(tree),
                req["head"],
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if made.returncode != 0:
            raise Refused("could not make the private checkout")
        req["tree"] = str(tree)
        work = cwd = tree
    elif plan_review:
        work, cwd = plan_copy.parent, store.out_dir(rd)
    elif (spec.writes_worktree and kind != "codex-fix") or kind == "docs":
        work = cwd = Path(req["worktree"])
    else:
        work, cwd = Path(req["worktree"] or rd), store.out_dir(rd)
    req["cwd"] = str(cwd)
    mapping = {
        "brief": rd / "brief.md",
        "worktree": work,
        "out": req["out"],
        "head": req["head"] or "",
        "run_dir": store.out_dir(rd),
        "run_id": run_id,
        "plan": plan_copy or "",
        "last_msg": req["last_msg"],
        "label": req["job"],
    }
    req["argv"] = _fill(req.pop("argv_t"), mapping)
    store.write_text(
        rd / "brief.md",
        build_brief(project, req["key"], kind, body, plan_copy=req.get("plan_copy")),
    )
    store.write_json(rd / "request.json", req)
    store.register_request(project, req["id"])


def _launch(project, rd, run_id):
    wrap = store.bin_dir() / "devloop-wrap"
    env = dict(os.environ)
    env["DEVLOOP_LIB"] = str(store.package_root())
    with open(rd / "wrap.log", "ab") as log:
        proc = subprocess.Popen(
            [str(wrap), project, run_id],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            env=env,
        )
    update_request(
        project,
        run_id,
        pid=proc.pid,
        pid_start=proc_start(proc.pid),
        boot_id=boot_id(),
    )


# --- briefs -------------------------------------------------------------------


def _section(text, kind):
    """The `### <kind>` block of a playbook, or ''."""
    out, on = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            on = line.strip() == f"### {kind}"
            continue
        if on:
            out.append(line)
    return "\n".join(out).strip()


def role_section(project, kind) -> str:
    cfg = projmod.load_project(project)
    paths = [store.skill_dir() / "PLAYBOOK.md"]
    if cfg.get("playbook_doc"):
        paths.append(Path(cfg["playbook_doc"]))
    parts = []
    for p in paths:
        try:
            sec = _section(p.read_text(encoding="utf-8"), kind)
        except OSError:
            sec = ""
        if sec:
            parts.append(sec)
    return "\n\n".join(parts)


HAND_BACK = (
    "Hand back: write `$DEVLOOP_RUN_DIR/result.md` last, atomically (temp file then "
    "rename). Header lines `key: value`, then a line `---`, then a short summary. Keys: "
    "`status` (DONE, BLOCKED or FAILED), `head` (the commit SHA you finished on), `pr`, "
    "`tests` (what you ran and the result), `next`. A DONE result must match your exit "
    "code; a contradiction counts as FAILED. Never put secrets or data values in it."
)
REVIEW_END = (
    "End your final message with one plain line, with no quotes, bold or backticks: "
    "VERDICT: GO, VERDICT: GO-WITH-FIXES or VERDICT: NO-GO. Only GO counts as GO."
)
REPAIR_END = (
    "End your final message with one plain line, with no quotes, bold or backticks: "
    "SELF-VERDICT: GO, SELF-VERDICT: GO-WITH-FIXES or SELF-VERDICT: NO-GO."
)
CONTRACTS = {
    "claude": HAND_BACK,
    "claude-verdict": REVIEW_END,
    "verdict": REVIEW_END,
    "selfverdict": REPAIR_END,
}


def build_brief(project, key, kind, body, plan_copy=None) -> str:
    cfg = projmod.load_project(project)
    try:
        template = (store.skill_dir() / "brief-template.md").read_text(encoding="utf-8")
    except OSError:
        raise RunError("brief template not found") from None
    rules = Path(cfg["rules_dir"])
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        s = None
    lessons = notes.lessons_for(project, s["row"]) if s else []
    values = {
        "risk": (s or {}).get("risk") or "unset",
        "rulebooks": "\n".join(f"- {rules / b}" for b in (s or {}).get("rulebooks", []))
        or "- none listed",
        "recurring_defects": str(rules / "catalogs" / "recurring-defects.md"),
        "standing_rules": cfg.get("brief_extra") or "",
        "node_directory": (
            (cfg.get("nodes") or {}).get("directory") or ""
            if kind in ("builder", "test")
            else ""
        ),
        "lessons": "\n".join(
            f"- {e['key']}: wrong: {e['wrong']} Instead: {e['instead']}"
            for e in lessons
        )
        or "none",
        "body": body
        + (
            f"\n\nThe plan to read is this frozen copy, not the live file: {plan_copy}\n"
            "Your verdict applies to exactly that file."
            if plan_copy and kind in REVIEW_KINDS
            else ""
        ),
        "contract": CONTRACTS.get(KINDS[kind].reader, ""),
        "role_section": role_section(project, kind),
    }
    # One pass over the template, so text inside `body` is never expanded again.
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


# --- apply --------------------------------------------------------------------


def _check_binding(cfg, req):
    """Reviewer, kind and purpose must still be an allowed pairing at apply time."""
    if req.get("kind") not in REVIEW_KINDS:
        return
    spec = cfg["reviewers"].get(req.get("reviewer") or "")
    if spec is None or req["kind"] not in (spec.get("kinds") or [spec["kind"]]):
        raise Rejected("binding", "reviewer and kind do not match the config")
    if req.get("purpose") not in ("plan", "code"):
        raise Rejected("binding", "review run has no purpose")


def _mark_applied(project, rd, req):
    (rd / "applied").write_text("")
    remove_tree(project, rd.name, req)


def _fail_key(req, reason=""):
    """Failure counter key.

    Writers are keyed by lineage, not by head: a builder that pushes a new head on each
    failed try would otherwise retry forever. A builder's lineage is the review round and
    the moment its phase was entered; a planner's is its plan path. Reviews and tests are
    keyed by the artifact they read. A clean exit with no verdict line counts apart.
    """
    kind = req["kind"]
    if kind == "builder":
        base = f"builder:{req.get('lineage')}"
    elif kind in ("planner", "codex-fix"):
        base = f"{kind}:{req.get('plan_path')}"
    else:
        base = "{}:{}".format(kind, req.get("head") or req.get("plan_sha") or "none")
    return base + (":no-verdict" if reason == "no-verdict" else "")


def _install_repair(project, rd, req, s):
    """Install the repaired plan once, and only over the file the repair read.

    repair.json is the wrapper's record, made after the payload ended. Its hash is
    checked against the control copy here, so a swapped copy never gets installed.
    """
    rj, copy = rd / "repair.json", rd / "repaired"
    if not rj.is_file() or not copy.is_file():
        return None, "missing"
    data = store.read_json(rj)
    if _sha_file(copy) != data.get("output_sha"):
        raise Rejected(
            "repaired-copy", "the repaired copy does not match its recorded hash"
        )
    plan = slices.plan_file(s)
    live = _sha_file(plan) if plan.is_file() else None
    if live == data["output_sha"]:
        return data["output_sha"], None
    if live == data["input_sha"]:
        tmp = plan.with_name(f".{plan.name}.devloop{os.getpid()}")
        shutil.copyfile(copy, tmp)
        os.replace(tmp, plan)
        return data["output_sha"], None
    store.append_event(project, "repair-conflict", run=req["id"])
    return None, "conflict"


def _apply_evidence(project, key, run_id, req, res, s, ev_kind):
    """Record a verdict. Returns (outcome, failed)."""
    kind = req["kind"]
    fkey = _fail_key(req)
    is_plan = req.get("purpose") == "plan" and kind in REVIEW_KINDS
    guard = (
        {"want_plan_sha": req["plan_sha"]} if is_plan else {"want_head": req["head"]}
    )
    if kind == "codex-fix" and res.verdict == "GO":
        sha, why = _install_repair(project, store.run_dir(project, run_id), req, s)
        if why == "missing":
            return slices.apply_effects(
                project, key, run_id, failure=fkey, **guard
            ), True
        if sha is None:
            return slices.apply_effects(project, key, run_id), False
        return (
            slices.apply_effects(
                project,
                key,
                run_id,
                kind=ev_kind,
                verdict="GO",
                plan_sha_=sha,
                want_plan_sha=sha,
            ),
            False,
        )
    if ev_kind == "full" and req.get("covers"):
        outcome = slices.apply_effects(
            project,
            key,
            run_id,
            kind="full",
            verdict=res.verdict,
            head=req["head"],
            want_head=req["head"],
        )
        for ckey, chead in req["covers"].items():
            try:
                slices.apply_effects(
                    project,
                    ckey,
                    run_id,
                    kind="full",
                    verdict=res.verdict,
                    head=chead,
                    want_head=chead,
                )
            except slices.SliceError:
                store.append_event(project, "apply-mismatch", run=run_id, key=ckey)
        return outcome, False
    return (
        slices.apply_effects(
            project,
            key,
            run_id,
            kind=ev_kind,
            verdict=res.verdict,
            head=None if is_plan else req["head"],
            plan_sha_=req["plan_sha"] if is_plan else None,
            **guard,
        ),
        False,
    )


def _mark_rejected(project, run_id, why):
    rd = store.run_dir(project, run_id)
    (rd / "rejected").write_text(why)
    store.append_event(project, "apply-refused", run=run_id, why=why)
    try:
        req = read_request(project, run_id)
    except Exception:  # noqa: BLE001 - a corrupt request must not stop the rejection
        req = None
    remove_tree(project, run_id, req)


def apply_run(project, run_id) -> str:
    """Apply a finished run. A permanent refusal closes the run with no effects."""
    rd = store.run_dir(project, run_id)
    if (rd / "rejected").is_file():
        return "rejected"
    try:
        return _apply(project, run_id)
    except Rejected as exc:
        _mark_rejected(project, run_id, exc.why)
        raise


def discard(project, run_id) -> None:
    """Owner only: close any stuck run, freeing its slot and worktree reservation."""
    store.require_interactive("discard")
    rd = store.run_dir(project, run_id)
    req = read_request(project, run_id)
    if not req:
        raise RunError("no such run")
    if not (rd / "exit").is_file():
        kill_payload(req)
        create_exit(rd, code=-1, reason="discarded")
    if req.get("kind") != "duty":
        try:
            s = slices.load(project, slice_key_of(req))
        except slices.SliceError:
            s = None
        if s and s["run"] == run_id:
            slices.set_run(project, s["key"], None)
    _mark_rejected(project, run_id, "discarded")


def _apply(project, run_id) -> str:
    rd = store.run_dir(project, run_id)
    req = read_request(project, run_id)
    if not req:
        raise RunError("no such run")
    if not (rd / "exit").is_file():
        raise RunError("run has not ended")
    kind = req["kind"]
    if kind not in KINDS:
        raise RunError("unknown kind")
    cfg = projmod.load_project(project)
    if (rd / "applied").is_file():
        return "already"
    if not store.request_matches_ledger(project, run_id):
        raise Rejected("ledger", "the run request does not match the dispatch ledger")
    res = parse_result(rd, kind)
    reason = res.reason

    if kind == "duty":
        store.append_event(
            project, "duty-done", run=run_id, duty=req.get("duty"), status=res.status
        )
        if res.status != "DONE":
            notes.problem(
                project,
                f"duty-failed-{req['duty']}",
                impact="a scheduled duty did not finish",
                fix="read the run log",
            )
        _mark_applied(project, rd, req)
        return "applied"

    _check_binding(cfg, req)
    key = slice_key_of(req)
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        store.append_event(project, "apply-mismatch", run=run_id, outcome="no-slice")
        _mark_applied(project, rd, req)
        return "stale"

    ev_kind = None
    if kind == "test":
        ev_kind = req["level"]
    elif kind in REVIEW_KINDS:
        ev_kind = f"{req['purpose']}:{req['reviewer']}"
    failed = False
    fkey = _fail_key(req, reason)
    if reason in ("capped", "infra"):
        outcome = slices.apply_effects(project, key, run_id)
        store.append_event(project, f"run-{reason}", run=run_id, key=key)
    elif res.status == "FAILED":
        failed = True
        guard = {}
        if ev_kind:
            guard = (
                {"want_plan_sha": req["plan_sha"]}
                if req["purpose"] == "plan"
                else {"want_head": req["head"]}
            )
        outcome = slices.apply_effects(project, key, run_id, failure=fkey, **guard)
    elif res.status == "BLOCKED" or ev_kind is None:
        outcome = slices.apply_effects(project, key, run_id)
        store.append_event(
            project, "run-done", run=run_id, key=key, status=res.status, run_kind=kind
        )
    else:
        outcome, failed = _apply_evidence(project, key, run_id, req, res, s, ev_kind)
    if outcome == "stale":
        store.append_event(
            project, "apply-mismatch", run=run_id, key=key, outcome="stale"
        )
    _mark_applied(project, rd, req)
    if failed:
        _maybe_park(project, key, fkey, reason)
    store.append_event(project, "run-applied", run=run_id, key=key, outcome=outcome)
    return outcome


def _maybe_park(project, key, fkey, reason=""):
    s = slices.load(project, key)
    count = s["failures"].get(fkey, 0)
    if count >= 2 and s["phase"] in slices.ACTIVE:
        # The first no-verdict park lets the tick resume once; the count survives the
        # resume, so a third no-verdict is a real failure and needs the owner.
        why = "no-verdict" if reason == "no-verdict" and count < 3 else "failed-twice"
        slices.move(project, key, "PARKED", why)
        notes.notify(project, f"Parked {key}: {why}.")
