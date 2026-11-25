"""Dispatch: admission, argv, the frozen request, launch and the worker brief."""

import datetime
import hashlib
import shutil
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from . import project as projmod
from . import evidence, notes, runs, slices, store


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
        raise runs.Refused(f"git {args[0]} failed")
    return r.stdout.strip()


def worktree_root(path) -> str:
    return os.path.realpath(_git(path, "rev-parse", "--show-toplevel"))


def _pause_or_cap(project):
    d = store.state_dir(project)
    if (d / "PAUSE").exists():
        raise runs.Refused("paused")
    if store.cap_active(project):
        raise runs.Refused("usage cap active")


def _applied(project, run_id) -> bool:
    return (store.run_dir(project, run_id) / "applied").is_file()


def _predecessor(project, s, cfg, purpose, name):
    """The run that satisfied the previous reviewer in the roster, or runs.Refused."""
    if purpose == "code":
        roster = evidence.code_roster(project, cfg, s)
    else:
        roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]][purpose]]
    names = [n for n, _ in roster]
    if name not in names:
        raise runs.Refused(f"{name} is not in the {s['risk']} {purpose} roster")
    i = names.index(name)
    if i == 0:
        return None
    pname, pcap = roster[i - 1]
    if purpose == "code":
        e = (s["evidence"].get(s["head"]) or {}).get(f"code:{pname}")
        if not e or e["verdict"] != "GO" or not _applied(project, e["run"]):
            raise runs.Refused(f"{pname} has no applied GO on this head yet")
        return e["run"]
    mine = sorted(
        (
            e
            for bucket in s["evidence"].values()
            for k, e in bucket.items()
            if k == f"plan:{pname}"
            and e.get("plan_path") == s["plan_path"]
            and e.get("plan_lineage", 0) == s["plan_lineage"]
        ),
        key=lambda e: e["at"],
    )
    gos = [e for e in mine if e["verdict"] == "GO"]
    rounds = s["plan_rounds"].get(s["plan_path"], {}).get(pname, 0)
    capped = pcap is not None and rounds >= pcap
    pick = gos[-1] if gos else (mine[-1] if mine and capped else None)
    if pick is None or not _applied(project, pick["run"]):
        raise runs.Refused(f"{pname} has not finished the plan review yet")
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


def _claude_argv(cfg, kind, model, *extra):
    return [
        "claude",
        "-p",
        *projmod.mcp_args(cfg, kind),
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
    m = runs.DUTY_KEY.fullmatch(key)
    if not m:
        raise runs.Refused("bad duty key")
    for name in cfg.get("duties") or {}:
        if name.lower() == m.group(1).lower():
            return name
    raise runs.Refused("unknown duty")


def _skeleton(kind, key):
    spec = runs.KINDS[kind]
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
        raise runs.Refused("the slice head is not a commit in the worktree")


def _prepare_review(project, cfg, s, req, kind, reviewer, wt):
    if s["risk"] not in slices.TIERS:
        raise runs.Refused("risk is not set")
    spec_r = cfg["reviewers"].get(reviewer or "")
    if spec_r is None:
        raise runs.Refused("unknown reviewer")
    if kind not in (spec_r.get("kinds") or [spec_r["kind"]]):
        raise runs.Refused(f"reviewer {reviewer} may not run {kind}")
    req["reviewer"] = reviewer
    purpose = "plan" if (kind == "codex-fix" or s["phase"] == "PLAN") else "code"
    req["purpose"] = purpose
    argv = (spec_r.get("argv") or {}).get(kind)
    if argv is None and kind == "dennis":
        argv = _claude_argv(cfg, kind, cfg["models"]["reviewer"], "--agent", reviewer)
    if argv is None:
        raise runs.Refused("no command configured for this reviewer and kind")
    req["argv_t"] = list(argv)
    if purpose == "plan":
        pf = slices.plan_file(s)
        if pf is None or not pf.is_file():
            raise runs.Refused("plan file missing")
        req["plan_sha"] = slices.plan_sha(s)
        if _own_cap_reached(s, cfg, reviewer):
            raise runs.Refused(f"{reviewer} is at its plan round cap")
    else:
        if not s["head"]:
            raise runs.Refused("slice has no head")
        req["head"] = s["head"]
        _require_commit(wt, s["head"])
    req["predecessor_run"] = _predecessor(project, s, cfg, purpose, reviewer)
    if kind == "codex-fix":
        req["repair_out_t"] = spec_r.get("repair_out")
        if not req["repair_out_t"]:
            raise runs.Refused("no repair_out configured")


def _prepare_test(project, cfg, s, req, level, select, covers, wt):
    spec_t = (cfg.get("tests") or {}).get(level or "")
    if not spec_t or not spec_t.get("argv"):
        raise runs.Refused("no command configured for this test level")
    if not s["head"]:
        raise runs.Refused("slice has no head")
    if select and level in ("t2", "full"):
        raise runs.Refused(f"{level} runs the whole level; a selection cannot earn it")
    req.update(level=level, head=s["head"], purpose="code", select=list(select))
    req["argv_t"] = list(spec_t["argv"]) + [
        x for item in select for x in ("--select", item)
    ]
    req["deadline_s"] = spec_t.get("deadline_s", runs.KINDS["test"].deadline_s)
    req["host"] = spec_t.get("host")
    req["infra_codes"] = list(spec_t.get("infra_codes", []))
    if covers and level != "full":
        raise runs.Refused("only a full run covers other slices")
    frozen = {}
    for ckey in covers:
        other = slices.load(project, ckey)
        if other["repo"] != s["repo"]:
            raise runs.Refused("a covered slice is in another repo")
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
            raise runs.Refused(f"{ckey} head is not an ancestor of the tested head")
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
            "deadline_s", runs.KINDS["duty"].deadline_s
        )
        req["argv_t"] = list(cfg["duties"][name]["argv"])
        req["worktree"] = str(worktree) if worktree else None
        return req, None
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        raise runs.Refused("no such slice") from None
    req.update(row=s["row"], slice=s["slice"], plan_path=s["plan_path"])
    req["plan_lineage"] = s["plan_lineage"]
    req["lineage"] = f"{s['round']}:{s['last_moved_at']}"
    if s["phase"] not in runs.KIND_PHASES[kind]:
        raise runs.Refused(f"{kind} does not run in {s['phase']}")
    # The fixer follows the round ladder: Claude for rounds 1 to 3, Codex for 4 to 6.
    if s["phase"] == "REMEDIATE":
        late = s["round"] >= slices.CODEX_ROUNDS_FROM
        if kind == "builder" and late:
            raise runs.Refused(
                "rounds 4 and up are fixed by codex-builder, not builder"
            )
        if kind == "codex-builder" and not late:
            raise runs.Refused("codex-builder is for review round 4 and up")
    wt = worktree or s["worktree"]
    if not wt or not Path(wt).is_dir():
        raise runs.Refused("no worktree")
    req["worktree"] = str(wt)
    if kind in runs.REVIEW_KINDS:
        _prepare_review(project, cfg, s, req, kind, reviewer, wt)
    elif kind == "test":
        _prepare_test(project, cfg, s, req, level, select, covers, wt)
    elif kind == "codex-builder":
        argv = ((cfg["reviewers"].get("codex") or {}).get("argv") or {}).get(kind)
        if not argv:
            raise runs.Refused("no command configured for codex-builder")
        req.update(argv_t=list(argv), head=s["head"], purpose="code")
    else:
        role = "builder" if kind == "builder" else "planner"
        model = cfg["models"][role]
        if role == "planner" and "opus" not in model.lower():
            raise runs.Refused("planner and docs need an Opus model")
        req.update(model=model, argv_t=_claude_argv(cfg, kind, model), head=s["head"])
        req["purpose"] = "author" if kind == "planner" else "code"
        if kind == "planner":
            if not s["plan_path"]:
                raise runs.Refused("set plan_path first")
            pf = slices.plan_file(s)
            req["plan_sha"] = slices.plan_sha(s) if pf and pf.is_file() else None
    return req, s


RESERVE_WINDOW_S = 300


def _check_memory(project, cfg, kind):
    """Refuse when free memory, less what recent starts still have to claim and this
    kind's own estimate, would drop below the floor. A worker's memory is not in
    MemAvailable until it has grown, so a burst of starts needs the reservations."""
    reserved = 0
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=RESERVE_WINDOW_S
    )
    for rd, r in runs.iter_runs(project):
        try:
            started = datetime.datetime.fromisoformat(r["started_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if started >= cutoff and not (rd / "exit").is_file():
            reserved += projmod.memory_mb(cfg, r["kind"])
    free_mb = mem_available_kb() / 1024
    floor_mb = cfg.get("min_free_gb", 2.5) * 1024
    if free_mb - reserved - projmod.memory_mb(cfg, kind) < floor_mb:
        raise runs.Refused("memory is below the floor")


def _tick_id():
    value = os.environ.get("DEVLOOP_TICK_ID")
    return value if value and store.TOKEN.fullmatch(value) else None


def _check_tick_limit(project, cfg):
    """One tick may start only so many runs; a model that loops cannot flood the box."""
    tid = _tick_id()
    if tid is None:
        return
    made = sum(1 for _, r in runs.iter_runs(project) if r.get("tick_id") == tid)
    if made >= projmod.max_dispatch_per_tick(cfg):
        raise runs.Refused("tick-dispatch-limit")


def _check_admission(project, cfg, req, root):
    spec = runs.KINDS[req["kind"]]
    if spec.slot:
        used = sum(
            1
            for _, r in runs.live_runs(project)
            if runs.KINDS[r["kind"]].slot == spec.slot
        )
        if used >= cfg["capacity"][spec.slot]:
            raise runs.Refused(f"no free {spec.slot} slot")
    if spec.writes_worktree:
        for rid, r in runs.writer_runs(project):
            if r.get("root") == root:
                raise runs.Refused(f"worktree is reserved by {rid}")
    for rd, r in runs.iter_runs(project):
        if (
            req["kind"] in runs.REVIEW_KINDS
            and req["purpose"] == "plan"
            and r.get("key") == req["key"]
            and r.get("purpose") == "plan"
            and r.get("kind") in runs.REVIEW_KINDS
            and not runs.is_done(rd)
        ):
            raise runs.Refused("another plan review is still open for this slice")
        if req["kind"] == "duty" and r.get("key") == req["key"] and r["kind"] == "duty":
            raise runs.Refused("duty already dispatched")


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
    if kind not in runs.KINDS:
        raise runs.Refused("unknown kind")
    cfg = projmod.load_project(project)
    brief = Path(brief)
    if not brief.is_file():
        raise runs.Refused("brief file not found")
    if retry_of is not None and store.TOKEN.fullmatch(retry_of) is None:
        raise runs.Refused("bad retry-of")
    req, s = _prepare(
        project, cfg, kind, key, worktree, reviewer, level, list(select), list(covers)
    )
    spec = runs.KINDS[kind]
    body = brief.read_text(encoding="utf-8")

    with store.locked(store.state_dir(project) / "admit"):
        _pause_or_cap(project)
        _check_memory(project, cfg, kind)
        _check_tick_limit(project, cfg)
        req["root"] = worktree_root(req["worktree"]) if spec.writes_worktree else None
        _check_admission(project, cfg, req, req["root"])
        req["seq"] = store.next_seq(project)
        req["tick_id"] = _tick_id()
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
            runs.remove_tree(project, run_id, req)
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
    spec = runs.KINDS[kind]
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
            raise runs.Refused("could not make the private checkout")
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
    runs.update_request(
        project,
        run_id,
        pid=proc.pid,
        pid_start=runs.proc_start(proc.pid),
        boot_id=runs.boot_id(),
    )


# --- briefs -------------------------------------------------------------------


def _names_kind(line, kind) -> bool:
    """A level-3 heading that is exactly `### <kind>` or holds the kind in backticks
    followed by " kind", as in "### Developer (`builder` kind)". The backticks keep
    `codex-fix` from matching `codex-review` or a longer kind name."""
    heading = line.strip()
    return heading == f"### {kind}" or (
        heading.startswith("### ")
        and re.search(rf"`{re.escape(kind)}` kind\b", heading) is not None
    )


def _section(text, kind):
    """The playbook block whose heading names `kind`, or ''."""
    out, on = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            on = _names_kind(line, kind)
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
        raise runs.RunError("brief template not found") from None
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
            if kind in ("builder", "codex-builder", "test")
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
            if plan_copy and kind in runs.REVIEW_KINDS
            else ""
        ),
        "contract": CONTRACTS.get(runs.KINDS[kind].reader, ""),
        "role_section": role_section(project, kind),
    }
    # One pass over the template, so text inside `body` is never expanded again.
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
