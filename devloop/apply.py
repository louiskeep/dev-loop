"""Collect, parse and apply finished runs; reject the ones that cannot apply."""

import json
import os
import shutil
from pathlib import Path

from . import notes, runs, slices, store
from . import project as projmod

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
        for rd, req in runs.iter_runs(project):
            damaged = (rd / "exit").is_file() and not exit_valid(rd)
            if damaged and not runs.wrapper_alive(req):
                (rd / "exit").unlink(missing_ok=True)  # recovered below as a lost run
            if not (rd / "exit").is_file() and not runs.wrapper_alive(req):
                runs.kill_payload(req)
                create_exit(rd, code=-1, reason="lost")
                runs.remove_tree(project, rd.name, req)
            if (rd / "exit").is_file() and not runs.is_done(rd):
                done.append(rd.name)
    return done


def release(project, run_id) -> None:
    """The tick calls this after a node check proves a lost test run is really gone."""
    rd = store.run_dir(project, run_id)
    req = runs.read_request(project, run_id)
    if req.get("kind") != "test" or not (rd / "exit").is_file():
        raise runs.RunError("only an ended test run can be released")
    (rd / "released").write_text("")
    store.append_event(project, "run-released", run=run_id)


# --- results ------------------------------------------------------------------


def parse_result(run_dir: Path, kind: str) -> runs.Result:
    """Read result.md against the exit file. A contradiction is FAILED, never DONE."""
    if kind not in runs.KINDS:
        raise runs.RunError("unknown kind")
    hdr, body = store.read_result(store.out_dir(run_dir) / "result.md")
    ex = runs.read_exit(run_dir)
    status = hdr.get("status")
    body = body.strip()
    verdict = hdr.get("verdict") or None
    if verdict not in slices.VERDICTS:
        verdict = None
    if status not in runs.STATUSES:
        status = "FAILED"
    elif status in ("DONE", "BLOCKED") and ex.get("code") != 0:
        # A test that ran and failed (any normal non-zero exit the wrapper classed as a
        # failure) is a real NO-GO, not a broken run: it must replace any earlier GO at
        # that level and head. Whether it frees the tester slot is decided apart.
        red_test = (
            kind == "test"
            and status == "DONE"
            and verdict == "NO-GO"
            and ex.get("reason") == "exit"
        )
        if not red_test:
            status = "FAILED"
    elif (
        status == "DONE"
        and runs.KINDS[kind].reader not in ("claude", "exit")
        and verdict is None
    ):
        status = "FAILED"
    return runs.Result(
        status,
        hdr.get("head") or None,
        hdr.get("pr") or None,
        verdict,
        hdr.get("tests") or None,
        hdr.get("next") or None,
        body[:2000],
        ex.get("reason") or "",
    )


# --- apply --------------------------------------------------------------------


def _check_binding(cfg, req):
    """Reviewer, kind and purpose must still be an allowed pairing at apply time."""
    if req.get("kind") not in runs.REVIEW_KINDS:
        return
    spec = cfg["reviewers"].get(req.get("reviewer") or "")
    if spec is None or req["kind"] not in (spec.get("kinds") or [spec["kind"]]):
        raise runs.Rejected("binding", "reviewer and kind do not match the config")
    if req.get("purpose") not in ("plan", "code"):
        raise runs.Rejected("binding", "review run has no purpose")


def _mark_applied(project, rd, req):
    (rd / "applied").write_text("")
    runs.remove_tree(project, rd.name, req)


def _fail_key(req, reason=""):
    """Failure counter key.

    Writers are keyed by lineage, not by head: a builder that pushes a new head on each
    failed try would otherwise retry forever. A builder's lineage is the review round and
    the moment its phase was entered; a planner's is its plan path. Reviews and tests are
    keyed by the artifact they read. A clean exit with no verdict line counts apart.
    """
    kind = req["kind"]
    if kind in ("builder", "codex-builder"):
        base = f"{kind}:{req.get('lineage')}"
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
    if runs.sha_file(copy) != data.get("output_sha"):
        raise runs.Rejected(
            "repaired-copy", "the repaired copy does not match its recorded hash"
        )
    plan = slices.plan_file(s)
    live = runs.sha_file(plan) if plan.is_file() else None
    if live == data["output_sha"]:
        return data["output_sha"], None
    if live == data["input_sha"]:
        tmp = plan.with_name(f".{plan.name}.devloop{os.getpid()}")
        shutil.copyfile(copy, tmp)
        os.replace(tmp, plan)
        return data["output_sha"], None
    store.append_event(project, "repair-conflict", run=req["id"])
    return None, "conflict"


def _is_batch(req):
    batch = req.get("batch")
    return req.get("kind") == "test" and isinstance(batch, dict) and len(batch) > 1


def _apply_batch(project, key, run_id, req, res, ev_kind):
    """A green batch credits every member at its frozen head. A red one proves nothing
    about any single member, so no member gets a verdict or a failure count."""
    seq = req.get("seq")
    if res.verdict != "GO":
        store.append_event(
            project, "batch-red", run=run_id, keys=sorted(req["batch"]), key=key
        )
        return slices.apply_effects(project, key, run_id)
    outcome = None
    for mkey, mhead in req["batch"].items():
        try:
            got = slices.apply_effects(
                project,
                mkey,
                run_id,
                kind=ev_kind,
                verdict="GO",
                head=mhead,
                want_head=mhead,
                seq=seq,
            )
        except slices.SliceError:
            got = "stale"
        if got == "stale":
            store.append_event(project, "apply-mismatch", run=run_id, key=mkey)
        if mkey == key:
            outcome = got
    return outcome


def _apply_evidence(project, key, run_id, req, res, s, ev_kind):
    """Record a verdict. Returns (outcome, failed)."""
    kind = req["kind"]
    fkey = _fail_key(req)
    is_plan = req.get("purpose") == "plan" and kind in runs.REVIEW_KINDS
    guard = (
        {"want_plan_sha": req["plan_sha"]} if is_plan else {"want_head": req["head"]}
    )
    seq = req.get("seq")
    if "plan_lineage" in req:
        guard["want_lineage"] = req["plan_lineage"]
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
                seq=seq,
            ),
            False,
        )
    if _is_batch(req):
        return _apply_batch(project, key, run_id, req, res, ev_kind), False
    if ev_kind == "full" and req.get("covers"):
        outcome = slices.apply_effects(
            project,
            key,
            run_id,
            kind="full",
            verdict=res.verdict,
            head=req["head"],
            want_head=req["head"],
            seq=seq,
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
                    seq=seq,
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
            seq=seq,
            **guard,
        ),
        False,
    )


def _mark_rejected(project, run_id, why):
    rd = store.run_dir(project, run_id)
    (rd / "rejected").write_text(why)
    store.append_event(project, "apply-refused", run=run_id, why=why)
    try:
        req = runs.read_request(project, run_id)
    except Exception:  # noqa: BLE001 - a corrupt request must not stop the rejection
        req = None
    runs.remove_tree(project, run_id, req)


def apply_run(project, run_id) -> str:
    """Apply a finished run. A permanent refusal closes the run with no effects."""
    rd = store.run_dir(project, run_id)
    if (rd / "rejected").is_file():
        return "rejected"
    try:
        return _apply(project, run_id)
    except runs.Rejected as exc:
        _mark_rejected(project, run_id, exc.why)
        raise


def discard(project, run_id) -> None:
    """Owner only: close any stuck run, freeing its slot and worktree reservation."""
    store.require_interactive("discard")
    rd = store.run_dir(project, run_id)
    req = runs.read_request(project, run_id)
    if not req:
        raise runs.RunError("no such run")
    if not (rd / "exit").is_file():
        runs.kill_payload(req)
        create_exit(rd, code=-1, reason="discarded")
    if req.get("kind") != "duty":
        try:
            s = slices.load(project, runs.slice_key_of(req))
        except slices.SliceError:
            s = None
        if s and s["run"] == run_id:
            slices.set_run(project, s["key"], None)
    _mark_rejected(project, run_id, "discarded")


def _apply(project, run_id) -> str:
    rd = store.run_dir(project, run_id)
    req = runs.read_request(project, run_id)
    if not req:
        raise runs.RunError("no such run")
    if not (rd / "exit").is_file():
        raise runs.RunError("run has not ended")
    kind = req["kind"]
    if kind not in runs.KINDS:
        raise runs.RunError("unknown kind")
    cfg = projmod.load_project(project)
    if (rd / "applied").is_file():
        return "already"
    if not store.request_matches_ledger(project, run_id):
        raise runs.Rejected(
            "ledger", "the run request does not match the dispatch ledger"
        )
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
    key = runs.slice_key_of(req)
    try:
        s = slices.load(project, key)
    except slices.SliceError:
        store.append_event(project, "apply-mismatch", run=run_id, outcome="no-slice")
        _mark_applied(project, rd, req)
        return "stale"

    ev_kind = None
    if kind == "test":
        ev_kind = req["level"]
    elif kind in runs.REVIEW_KINDS:
        ev_kind = f"{req['purpose']}:{req['reviewer']}"
    failed = False
    fkey = _fail_key(req, reason)
    if reason in ("capped", "infra"):
        outcome = slices.apply_effects(project, key, run_id)
        store.append_event(project, f"run-{reason}", run=run_id, key=key)
    elif res.status == "FAILED" and _is_batch(req):
        outcome = _apply_batch(project, key, run_id, req, res, ev_kind)
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
        writer = (
            slices.writer_record(kind, res.head, req.get("plan_lineage", 0))
            if kind in slices.WRITER_KINDS and res.status == "DONE" and res.head
            else None
        )
        outcome = slices.apply_effects(project, key, run_id, writer=writer)
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
