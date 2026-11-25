"""Gate and exit evidence: re-reads run dirs to decide what a slice has earned."""

import hashlib

from . import project as projmod
from . import runs, slices, store


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
    if not store.request_matches_ledger(project, run_id):
        return f"run {run_id} request does not match the ledger", None
    if not (rd / "applied").is_file():
        return f"run {run_id} is not applied", None
    if req.get("kind") not in kinds:
        return f"run {run_id} kind is not allowed here", None
    for field, want in expect.items():
        if field == "head" and not own:
            continue
        got = req.get(field)
        if field == "plan_sha" and req.get("kind") == "codex-fix":
            # A repair is credited to the file it produced, not the one it read. The
            # hash is recomputed from the control copy, never read from repair.json.
            copy = rd / "repaired"
            got = (
                hashlib.sha256(copy.read_bytes()).hexdigest() if copy.is_file() else got
            )
        if got != want:
            return f"run {run_id} {field} does not match", None
    if (
        store.read_header(store.out_dir(rd) / "result.md").get("verdict")
        != entry["verdict"]
    ):
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
    problem, req = _verify_run(
        project,
        s,
        e,
        kinds=("test",),
        expect={"level": level, "head": s["head"]},
        covers_ok=(level == "full"),
    )
    if problem is None and level in ("t2", "full") and req.get("select"):
        problem = "the run tested a selection, not the whole level"
    return _item(label, problem)


def _plan_items(project, s, cfg):
    f = slices.plan_file(s)
    cur = slices.plan_sha_or_none(s)
    items = [
        ("plan file exists", cur is not None),
        ("no plan review or repair outstanding", not _plan_outstanding(project, s)),
    ]
    has_rules = False
    if f is not None and cur is not None:
        has_rules = "Rules consulted:" in f.read_text(
            encoding="utf-8", errors="replace"
        )
    items.append(("plan names the Rules consulted", has_rules))
    if s["risk"] not in slices.TIERS:
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
            if kind == f"plan:{name}"
            and e.get("plan_path") == s["plan_path"]
            and e.get("plan_lineage", 0) == s["plan_lineage"]
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
            if (
                not e
                or e["verdict"] != "GO"
                or e.get("plan_lineage", 0) != s["plan_lineage"]
            ):
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
        if unreadable or req.get("kind") not in ("builder", "codex-builder"):
            continue
        if (req.get("row"), req.get("slice")) != (s["row"], s["slice"]):
            continue
        if req.get("plan_lineage", 0) != s["plan_lineage"]:
            continue  # a builder run for an earlier plan does not count after a redesign
        hdr = store.read_header(store.out_dir(rd) / "result.md")
        if (
            (rd / "applied").is_file()
            and store.request_matches_ledger(project, rd.name)
            and hdr.get("status") == "DONE"
            and hdr.get("head") == s["head"]
        ):
            return True
    return False


def _row_in_document(project, s):
    return [
        o["key"]
        for o in slices.list_slices(project)
        if o["row"] == s["row"] and o["key"] != s["key"] and o["phase"] == "DOCUMENT"
    ]


def _outstanding(project, s):
    """Run IDs that could still replace evidence for this head: a test or review that
    is live, or finished but not applied. Their result is unknown, so the old GO cannot
    be trusted. A covering full run that lists this slice counts the same."""
    out = []
    for rd, req in runs.iter_runs(project):
        if runs.is_done(rd) or req.get("kind") not in _EVIDENCE_KINDS:
            continue
        mine = runs.slice_key_of(req) == s["key"] and req.get("head") == s["head"]
        covers = req.get("covers") if isinstance(req.get("covers"), dict) else {}
        code_run = req["kind"] == "test" or req.get("purpose") == "code"
        if code_run and (mine or covers.get(s["key"]) == s["head"]):
            out.append(rd.name)
    return out


def _plan_outstanding(project, s):
    """A plan review or repair for this slice that is live, or finished but not applied.

    Its verdict is unknown, so approving the plan now could approve bytes that already
    have a NO-GO waiting to be applied."""
    return [
        rd.name
        for rd, req in runs.iter_runs(project)
        if not runs.is_done(rd)
        and req.get("kind") in runs.REVIEW_KINDS
        and req.get("purpose") == "plan"
        and runs.slice_key_of(req) == s["key"]
    ]


_EVIDENCE_KINDS = ("test", "dennis", "codex-review", "codex-fix")


def _gate_items(project, s, cfg):
    if not s["head"]:
        return [("head set", False)]
    items = [
        ("no test or review outstanding on head", not _outstanding(project, s)),
        # A row's docs finish before its next slice merges.
        ("no other slice of this row is in DOCUMENT", not _row_in_document(project, s)),
    ]
    sha = slices.plan_sha_or_none(s)
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
    if s["risk"] not in slices.TIERS:
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


def checks(project, s, cfg, to):
    frm = s["phase"]
    if (frm, to) == ("FRAME", "PLAN"):
        return [
            (
                "risk is R0, R1 or R2 (R3 waits for the owner)",
                s["risk"] in slices.TIERS,
            ),
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
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    to = to_phase or slices.FORWARD.get(s["phase"])
    return [] if to is None else checks(project, s, cfg, to)


def validate_gate(project: str, key: str) -> list:
    """The one evidence check, shared by REVIEW to GATE and the merge command."""
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    return [name for name, ok in _gate_items(project, s, cfg) if not ok]
