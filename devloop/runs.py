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
    "docs": Kind(None, 3600, 1200, "claude", False, False),
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


# --- reading run directories --------------------------------------------------


def _runs_dir(project):
    d = store.state_dir(project) / "runs"
    d.mkdir(exist_ok=True)
    return d


def read_request(project, run_id) -> dict:
    return store.read_json(store.run_dir(project, run_id) / "request.json")


def update_request(project, run_id, **fields) -> None:
    path = store.run_dir(project, run_id) / "request.json"
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
            req = store.read_json(rd / "request.json")
        except store.StoreError:
            unreadable = True
        if req and not unreadable:
            yield rd, req


def slice_key_of(req) -> str:
    return slices.slice_key(req["row"], req["slice"])


# --- liveness -----------------------------------------------------------------


def _proc_start(pid):
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError, TypeError):
        return None
    # The command name may hold spaces and parens; fields restart after the last ")".
    rest = text.rsplit(")", 1)[-1].split()
    return rest[19] if len(rest) > 19 else None


def _boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def _alive(pid, start, boot):
    if not pid or start is None or boot != _boot_id():
        return False
    return _proc_start(pid) == str(start)


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


def remove_tree(req) -> None:
    """Remove a pinned run's private checkout and its worktree registration."""
    tree, src = req.get("tree"), req.get("worktree")
    if not tree or not Path(tree).exists():
        return
    subprocess.run(
        ["git", "-C", src or ".", "worktree", "remove", "--force", tree],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    shutil.rmtree(tree, ignore_errors=True)


def _exit_of(rd) -> dict:
    return store.read_json(rd / "exit")


def _test_confirmed(rd) -> bool:
    """A test run frees its slot only when the payload itself said it finished."""
    ex = _exit_of(rd)
    return (rd / "released").is_file() or (
        ex.get("reason") == "exit" and ex.get("code") in (0, 1)
    )


def holds_slot(rd, req) -> bool:
    spec = KINDS.get(req.get("kind"))
    if spec is None:
        return False
    ended = (rd / "exit").is_file()
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
        if KINDS.get(req.get("kind"), KINDS["docs"]).writes_worktree
        and not (rd / "applied").is_file()
    ]


# --- collect ------------------------------------------------------------------


def create_exit(rd, *, code, reason) -> bool:
    """Create-exclusive: whoever writes `exit` first wins."""
    try:
        fd = os.open(rd / "exit", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(
            json.dumps({"code": code, "reason": reason, "ended_at": store.now_iso()})
        )
    return True


def collect(project) -> list:
    """Mark dead no-exit runs lost; return finished runs not yet applied."""
    done = []
    # Under the admission lock so a run still being launched is never read half-made.
    with store.locked(store.state_dir(project) / "admit"):
        for rd, req in iter_runs(project):
            if not (rd / "exit").is_file() and not wrapper_alive(req):
                kill_payload(req)
                create_exit(rd, code=-1, reason="lost")
                remove_tree(req)
            if (rd / "exit").is_file() and not (rd / "applied").is_file():
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
    hdr = store.read_header(run_dir / "result.md")
    ex = _exit_of(run_dir)
    status = hdr.get("status")
    body = ""
    try:
        text = (run_dir / "result.md").read_text(encoding="utf-8", errors="replace")
        body = text.partition("\n---")[2].strip()
    except OSError:
        pass
    verdict = hdr.get("verdict") or None
    if verdict not in slices.VERDICTS:
        verdict = None
    if status not in STATUSES:
        status = "FAILED"
    elif status in ("DONE", "BLOCKED") and ex.get("code") != 0:
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
        if _git(wt, "status", "--porcelain"):
            raise Refused("worktree has uncommitted changes")
        if not _git(wt, "rev-parse", "HEAD").startswith(s["head"]):
            raise Refused("worktree is not at the slice head")
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
    req.update(row=s["row"], slice=s["slice"])
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
            and not (rd / "applied").is_file()
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
            remove_tree(req)
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
        out=str(rd / "out"),
        last_msg=str(rd / "last_msg"),
        run_dir=str(rd),
        job=f"{store.state_dir(project).name}-{run_id}",
    )
    plan_copy = None
    if req["plan_sha"] and s is not None:
        (rd / "plan").mkdir()
        src = slices.plan_file(s)
        plan_copy = rd / "plan" / src.name
        shutil.copyfile(src, plan_copy)
        req["plan_copy"] = str(plan_copy)
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
        work = cwd = plan_copy.parent
    elif (spec.writes_worktree and kind != "codex-fix") or kind == "docs":
        work = cwd = Path(req["worktree"])
    else:
        work, cwd = Path(req["worktree"] or rd), rd
    req["cwd"] = str(cwd)
    mapping = {
        "brief": rd / "brief.md",
        "worktree": work,
        "out": req["out"],
        "head": req["head"] or "",
        "run_dir": rd,
        "run_id": run_id,
        "plan": plan_copy or "",
        "last_msg": req["last_msg"],
        "label": req["job"],
    }
    req["argv"] = _fill(req.pop("argv_t"), mapping)
    store.write_text(rd / "brief.md", build_brief(project, req["key"], kind, body))
    store.write_json(rd / "request.json", req)


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
        pid_start=_proc_start(proc.pid),
        boot_id=_boot_id(),
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
    "End your final message with one line: `VERDICT: GO`, `VERDICT: GO-WITH-FIXES` "
    "or `VERDICT: NO-GO`. Only `GO` counts as GO."
)
REPAIR_END = (
    "End your final message with one line: `SELF-VERDICT: GO`, "
    "`SELF-VERDICT: GO-WITH-FIXES` or `SELF-VERDICT: NO-GO`."
)
CONTRACTS = {
    "claude": HAND_BACK,
    "claude-verdict": REVIEW_END,
    "verdict": REVIEW_END,
    "selfverdict": REPAIR_END,
}


def build_brief(project, key, kind, body) -> str:
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
        "body": body,
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
        raise RunError("reviewer and kind do not match the config")
    if req.get("purpose") not in ("plan", "code"):
        raise RunError("review run has no purpose")


def _mark_applied(rd, req):
    (rd / "applied").write_text("")
    remove_tree(req)


def _fail_key(req):
    # Keyed by the artifact, so a new head or plan starts with a fresh retry.
    return "{}:{}".format(req["kind"], req.get("head") or req.get("plan_sha") or "none")


def _install_repair(project, rd, req, s):
    """Install the repaired plan once, and only over the file the repair read."""
    rj = rd / "repair.json"
    if not rj.is_file():
        out = Path(req["repair_out"])
        if not out.is_file():
            return None, "missing"
        shutil.copyfile(out, rd / "repaired")
        store.write_json(
            rj,
            {"input_sha": req["plan_sha"], "output_sha": _sha_file(rd / "repaired")},
        )
    data = store.read_json(rj)
    plan = slices.plan_file(s)
    live = _sha_file(plan) if plan.is_file() else None
    if live == data["output_sha"]:
        return data["output_sha"], None
    if live == data["input_sha"]:
        tmp = plan.with_name(f".{plan.name}.devloop{os.getpid()}")
        shutil.copyfile(rd / "repaired", tmp)
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


def apply_run(project, run_id) -> str:
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
        _mark_applied(rd, req)
        return "applied"

    _check_binding(cfg, req)
    key = slice_key_of(req)
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        store.append_event(project, "apply-mismatch", run=run_id, outcome="no-slice")
        _mark_applied(rd, req)
        return "stale"

    ev_kind = None
    if kind == "test":
        ev_kind = req["level"]
    elif kind in REVIEW_KINDS:
        ev_kind = f"{req['purpose']}:{req['reviewer']}"
    failed = False
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
        outcome = slices.apply_effects(
            project, key, run_id, failure=_fail_key(req), **guard
        )
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
    _mark_applied(rd, req)
    if failed:
        _maybe_park(project, key, _fail_key(req))
    store.append_event(project, "run-applied", run=run_id, key=key, outcome=outcome)
    return outcome


def _maybe_park(project, key, fkey):
    s = slices.load(project, key)
    if s["failures"].get(fkey, 0) >= 2 and s["phase"] in slices.ACTIVE:
        slices.move(project, key, "PARKED", "failed-twice")
        notes.notify(project, f"Parked {key}: a worker failed twice.")
