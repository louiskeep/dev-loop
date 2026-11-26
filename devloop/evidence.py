"""Gate and exit evidence: re-reads run dirs to decide what a slice has earned."""

import collections
import hashlib

from . import project as projmod
from . import runs, slices, store


# One checklist entry. `code` and `reason` come from fixed tables (CODES, REASONS), so a
# reader can show them without any run id, reviewer name or free text; `reviewer` is the
# configured reviewer name, which readers turn into a role label.
Item = collections.namedtuple(
    "Item", "code label ok reviewer reason", defaults=(None, None)
)

CODES = (
    "risk",
    "rulebooks",
    "question-open",
    "plan-file",
    "plan-outstanding",
    "plan-rules",
    "plan-review",
    "branch",
    "head",
    "builder-run",
    "selfcheck",
    "t1",
    "t2",
    "full",
    "no-outstanding",
    "no-row-in-document",
    "plan-unchanged",
    "code-review",
    "docs",
)
# First matching fragment of a problem text wins; the fragment never reaches a reader.
REASONS = (
    ("no run recorded", "no-run"),
    ("no readable request", "unreadable"),
    ("belongs to another slice", "other-slice"),
    ("froze a different head", "other-head"),
    ("does not match the ledger", "ledger"),
    ("is not applied", "not-applied"),
    ("kind is not allowed", "wrong-kind"),
    ("result differs", "verdict-differs"),
    ("does not match", "mismatch"),
    ("selection, not the whole level", "selection"),
    ("predecessor run", "order"),
    ("recorded before", "order"),
)
REASON_WORDS = tuple(sorted({w for _, w in REASONS}))


def _reason(problem):
    for fragment, word in REASONS:
        if fragment in problem:
            return word
    return "invalid"


def _tick_ok(s, kind):
    e = (s["evidence"].get(s["head"]) or {}).get(kind) if s["head"] else None
    return bool(
        e
        and e["verdict"] == "GO"
        and e.get("source") == "tick"
        and e.get("head") == s["head"]
    )


def _verify_run(project, s, entry, *, kinds, expect, covers_ok=False, batch_ok=False):
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
    via = {}
    for ok, field in ((covers_ok, "covers"), (batch_ok, "batch")):
        if ok and isinstance(req.get(field), dict):
            via.update(req[field])
    if not own and s["key"] not in via:
        return f"run {run_id} belongs to another slice", None
    if not own and via[s["key"]] != s["head"]:
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


def _item(code, label, problem, reviewer=None):
    return Item(
        code,
        label if problem is None else f"{label} ({problem})",
        problem is None,
        reviewer,
        None if problem is None else _reason(problem),
    )


def _reviewer_kinds(cfg, name):
    spec = cfg["reviewers"][name]
    return tuple(spec.get("kinds") or [spec["kind"]])


def _docs_only(project, s):
    """The slice head changes documentation only, so QA is skipped (owner 2026-11-26)."""
    from . import carry, merge

    cfg = projmod.load_project(project)
    repo_cfg, _rules, base = merge.repo_settings(cfg, s["repo"])
    return carry.docs_only(s["worktree"] or repo_cfg["path"], base, s["head"])


def _test_item(project, s, level, label):
    if level == "t1" and s["head"] and _docs_only(project, s):
        return Item(level, "t1 not needed: the diff is docs only", True)
    e = (s["evidence"].get(s["head"]) or {}).get(level) if s["head"] else None
    if not e or e["verdict"] != "GO":
        return Item(level, label, False)
    problem, req = _verify_run(
        project,
        s,
        e,
        kinds=("test",),
        expect={"level": level, "head": e.get("carried_from") or s["head"]},
        covers_ok=(level == "full"),
        batch_ok=(level == "t1"),
    )
    if problem is None and level in ("t2", "full") and req.get("select"):
        problem = "the run tested a selection, not the whole level"
    return _item(level, label, problem)


def _plan_items(project, s, cfg):
    f = slices.plan_file(s)
    cur = slices.plan_sha_or_none(s)
    items = [
        Item("plan-file", "plan file exists", cur is not None),
        Item(
            "plan-outstanding",
            "no plan review or repair outstanding",
            not _plan_outstanding(project, s),
        ),
    ]
    has_rules = False
    if f is not None and cur is not None:
        has_rules = "Rules consulted:" in f.read_text(
            encoding="utf-8", errors="replace"
        )
    items.append(Item("plan-rules", "plan names the Rules consulted", has_rules))
    if s["risk"] not in slices.TIERS:
        return items + [Item("risk", "risk is R0, R1 or R2", False)]
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
                items.append(
                    Item(
                        "plan-review",
                        f"{label} (needs a GO or {cap} rounds)",
                        False,
                        name,
                    )
                )
                prev_run = None
                continue
            problem, _ = _verify_run(
                project,
                s,
                pick[1],
                kinds=kinds,
                expect={"purpose": "plan", "reviewer": name, "plan_sha": pick[0]},
            )
            items.append(_item("plan-review", label, problem, name))
            sat = pick[1]["run"] if problem is None else None
        else:
            e = (s["evidence"].get(cur) or {}).get(f"plan:{name}") if cur else None
            if (
                not e
                or e["verdict"] != "GO"
                or e.get("plan_lineage", 0) != s["plan_lineage"]
            ):
                items.append(
                    Item("plan-review", f"{label} GO on the current plan", False, name)
                )
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
            items.append(
                _item("plan-review", f"{label} GO on the current plan", problem, name)
            )
            sat = e["run"] if problem is None else None
        prev_run = sat
    return items


def senior_reviewers(cfg) -> list:
    """Reviewers declared `dennis` that may also run the dennis kind they must run here."""
    return [
        n
        for n, spec in cfg["reviewers"].items()
        if spec.get("kind") == "dennis" and "dennis" in _reviewer_kinds(cfg, n)
    ]


def _applied_writer_runs(project, s):
    """Applied, ledger-true DONE builder runs that produced the slice's current head."""
    runs_dir = store.state_dir(project) / "runs"
    found = []
    for rd in sorted(runs_dir.glob("*")) if runs_dir.is_dir() else []:
        try:
            req = store.read_json(rd / "request.json")
        except store.StoreError:
            continue
        if req.get("kind") not in slices.WRITER_KINDS:
            continue
        if (req.get("row"), req.get("slice")) != (s["row"], s["slice"]):
            continue
        if req.get("plan_lineage", 0) != s["plan_lineage"]:
            continue  # a builder run for an earlier plan does not count after a redesign
        # The exit file overrides a DONE header, so a failed run is not credited.
        res = runs.parse_result(rd, req["kind"])
        if (
            (rd / "applied").is_file()
            and store.request_matches_ledger(project, rd.name)
            and res.status == "DONE"
            and res.head == s["head"]
        ):
            found.append(req["kind"])
    return found


def head_writer(project, s):
    """Who wrote the slice's current head: `builder`, `codex-builder`, `imported`, or None.

    None means unproven, and every reader treats it as the strictest case. The stored
    record counts only for the commit and plan lineage it names; otherwise the answer is
    recovered from applied runs, which also covers slices stored before the record held
    a commit. A head moved by hand to another commit matches no run, so it stays None.
    """
    if not s["head"]:
        return None
    rec = s["head_writer"]
    if (
        isinstance(rec, dict)
        and rec["sha"] == s["head"]
        and rec["plan_lineage"] == s["plan_lineage"]
    ):
        return rec["kind"]
    found = _applied_writer_runs(project, s)
    if found:
        return "codex-builder" if "codex-builder" in found else found[-1]
    return None


def _claude_written(project, s) -> bool:
    return head_writer(project, s) in ("builder", "imported")


def senior_missing(project, cfg, s) -> bool:
    """A head not written by a Claude builder needs a Senior (dennis kind) reviewer."""
    return not _claude_written(project, s) and not senior_reviewers(cfg)


def senior_slot(project, cfg, s):
    """The reviewer that must hold the Senior seat on this head, or None."""
    if _claude_written(project, s) or not senior_reviewers(cfg):
        return None
    return code_roster(project, cfg, s)[0][0]


def code_reviewer_kinds(project, cfg, s, name) -> tuple:
    """The run kinds that count for a code reviewer; the Senior seat takes only dennis."""
    if name == senior_slot(project, cfg, s):
        return ("dennis",)
    return _reviewer_kinds(cfg, name)


def code_roster(project, cfg, s) -> list:
    """The code reviewers for this head, in order: `[(name, cap)]`.

    Owner rule: Codex is not the only reviewer of code Codex wrote, so a head not proven
    to come from a Claude builder gets a dennis-kind reviewer first, even in tiers that
    list none. Dispatch order checks and the gate both read this one list.
    """
    roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]]["code"]]
    if _claude_written(project, s):
        return roster
    dennis = senior_reviewers(cfg)
    if not dennis:
        return roster
    listed = [n for n, _ in roster if n in dennis]
    first = listed[0] if listed else dennis[0]
    own = [e for e in roster if e[0] == first] or [(first, None)]
    return own + [e for e in roster if e[0] != first]


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
        via = {}
        for field in ("covers", "batch"):
            if isinstance(req.get(field), dict):
                via.update(req[field])
        code_run = req["kind"] == "test" or req.get("purpose") == "code"
        if code_run and (mine or via.get(s["key"]) == s["head"]):
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
        return [Item("head", "head set", False)]
    items = [
        Item(
            "no-outstanding",
            "no test or review outstanding on head",
            not _outstanding(project, s),
        ),
        # A row's docs finish before its next slice merges.
        Item(
            "no-row-in-document",
            "no other slice of this row is in DOCUMENT",
            not _row_in_document(project, s),
        ),
    ]
    sha = slices.plan_sha_or_none(s)
    items.append(
        Item(
            "plan-unchanged",
            "approved plan unchanged",
            bool(s["approved_plan_sha"]) and sha == s["approved_plan_sha"],
        )
    )
    items.append(Item("selfcheck", "selfcheck GO on head", _tick_ok(s, "selfcheck")))
    items.append(_test_item(project, s, "t1", "t1 GO on head"))
    if (cfg.get("tests") or {}).get("t2"):
        items.append(_test_item(project, s, "t2", "t2 GO on head"))
    if s["full_suite_required"]:
        items.append(_test_item(project, s, "full", "full suite GO on head"))
    if s["risk"] not in slices.TIERS:
        return items + [Item("risk", "risk is R0, R1 or R2", False)]
    if head_writer(project, s) is None:
        items.append(
            Item("builder-run", "an applied builder run wrote this head", False)
        )
    if senior_missing(project, cfg, s):
        items.append(
            Item(
                "code-review",
                "Senior review (dennis kind) of a head not written by Claude: no such reviewer configured",
                False,
            )
        )
    prev_run, prev_at = None, None
    for i, (name, _cap) in enumerate(code_roster(project, cfg, s)):
        label = f"code review {name} GO on head"
        e = (s["evidence"].get(s["head"]) or {}).get(f"code:{name}")
        if not e or e["verdict"] != "GO":
            items.append(Item("code-review", label, False, name))
            prev_run = prev_at = None
            continue
        problem, req = _verify_run(
            project,
            s,
            e,
            kinds=code_reviewer_kinds(project, cfg, s, name),
            expect={
                "purpose": "code",
                "reviewer": name,
                "head": e.get("carried_from") or s["head"],
            },
        )
        if problem is None and i > 0:
            if req.get("predecessor_run") != prev_run:
                problem = "predecessor run does not match the previous reviewer"
            elif prev_at is None or e["at"] < prev_at:
                problem = "recorded before the previous reviewer"
        items.append(_item("code-review", label, problem, name))
        prev_run, prev_at = (e["run"], e["at"]) if problem is None else (None, None)
    return items


def checks(project, s, cfg, to):
    frm = s["phase"]
    if (frm, to) == ("FRAME", "PLAN"):
        return [
            Item(
                "risk",
                "risk is R0, R1 or R2 (R3 waits for the owner)",
                s["risk"] in slices.TIERS,
            ),
            Item("rulebooks", "rulebooks listed", bool(s["rulebooks"])),
        ]
    if (frm, to) == ("FRAME", "HELD") and s["risk"] == "R3":
        return [
            Item(
                "question-open",
                "a question for the owner is open",
                bool(s["waiting_on"]),
            )
        ]
    if (frm, to) == ("PLAN", "DEVELOP"):
        return _plan_items(project, s, cfg)
    if (frm, to) == ("DEVELOP", "SELF_CHECK"):
        return [
            Item("branch", "branch set", bool(s["branch"])),
            Item("head", "head set", bool(s["head"])),
            Item(
                "builder-run",
                "applied builder run DONE on head",
                head_writer(project, s) is not None,
            ),
        ]
    if (frm, to) == ("SELF_CHECK", "VERIFY"):
        return [
            Item(
                "selfcheck",
                "selfcheck GO on head",
                bool(s["head"]) and _tick_ok(s, "selfcheck"),
            )
        ]
    if (frm, to) == ("VERIFY", "REVIEW"):
        if not s["head"]:
            return [Item("head", "head set", False)]
        return [_test_item(project, s, "t1", "t1 GO on head")]
    if (frm, to) in (("REVIEW", "GATE"), ("GATE", "DOCUMENT")):
        return _gate_items(project, s, cfg)
    if (frm, to) == ("DOCUMENT", "DONE"):
        return [
            Item("docs", "docs GO on head", bool(s["head"]) and _tick_ok(s, "docs"))
        ]
    return []


def exit_items(project: str, key: str, to_phase=None) -> list:
    """Exit items for a transition, met or missing; `move` refuses on the same list."""
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    to = to_phase or slices.FORWARD.get(s["phase"])
    return [] if to is None else checks(project, s, cfg, to)


def exit_checklist(project: str, key: str, to_phase=None) -> list:
    """`[(label, ok)]` for the same items; the labels are pinned by tests and the playbook."""
    return [(i.label, i.ok) for i in exit_items(project, key, to_phase)]


def validate_gate(project: str, key: str) -> list:
    """The one evidence check, shared by REVIEW to GATE and the merge command."""
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    return [i.label for i in _gate_items(project, s, cfg) if not i.ok]
