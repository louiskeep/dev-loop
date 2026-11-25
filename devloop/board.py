"""Board mirror and the persisted merge ledger (merges.json)."""

import hashlib
import subprocess

from . import project as projmod
from . import notes, slices, store


# --- board mirror -------------------------------------------------------------


def _board_call(project, cfg, args, **fields):
    board = cfg.get("board")
    if not board:
        return True
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
    return ok


def mirror(project, cfg, s):
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


def _board_show(project, cfg):
    """The board's own read-only view, or None when it cannot be read."""
    board = cfg.get("board")
    if not board:
        return None
    try:
        r = subprocess.run(
            board["cmd"] + ["show"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _closures_path(project):
    return store.state_dir(project) / "closures.json"


def closure_records(project) -> dict:
    return store.read_json(_closures_path(project)).get("closures", {})


def row_close(project: str, row: str) -> bool:
    """Close a row on the board once. Only a successful board call is a closure.

    The attempt is saved before the call. An attempt with no saved closure may or may not
    have reached the board, and the board's `close` posts again on every call, so it is
    marked ambiguous, raised once as a problem and never retried automatically.
    """
    if not isinstance(row, str) or slices.ID_RE.fullmatch(row) is None:
        raise slices.SliceError("bad row id")
    cfg = projmod.load_project(project)
    path = _closures_path(project)
    with store.locked(path):
        doc = store.read_json(path)
        rec = doc.setdefault("closures", {}).get(row)
        if rec and rec.get("closed"):
            return True
        if rec and rec.get("attempted"):
            if not rec.get("close_ambiguous"):
                rec["close_ambiguous"] = True
                store.write_json(path, doc)
                notes.problem(
                    project,
                    f"close-ambiguous-{row}",
                    impact="a row close may or may not have reached the board",
                    fix="check the board, then close the row there by hand if needed",
                )
            return False
        doc["closures"][row] = {"attempted": True, "closed": False}
        store.write_json(path, doc)
        ok = _board_call(project, cfg, ["close", row], row=row)
        if ok:
            doc["closures"][row] = {
                "attempted": True,
                "closed": True,
                "at": store.now_iso(),
            }
            store.write_json(path, doc)
            store.append_event(project, "row-closed", row=row)
        else:
            # The call returned a failure, so it is known not to have closed the row.
            doc["closures"][row] = {"attempted": False, "closed": False}
            store.write_json(path, doc)
        return ok


# --- merge ledger -------------------------------------------------------------


def _merges_path(project):
    return store.state_dir(project) / "merges.json"


def merge_id(ident) -> str:
    return f"{ident['slug']}#{ident['pr']}@{ident['head']}"


def merge_records(project) -> dict:
    return store.read_json(_merges_path(project)).get("merges", {})


def record_merge(project, ident, s) -> None:
    """Persist one completed merge, keyed by repo, PR and head. Idempotent."""
    path = _merges_path(project)
    with store.locked(path):
        doc = store.read_json(path)
        merges = doc.setdefault("merges", {})
        merges.setdefault(
            merge_id(ident),
            {
                "key": s["key"],
                "row": s["row"],
                "slice": s["slice"],
                "pr": ident["pr"],
                "at": store.now_iso(),
                "board_done": False,
                "board_attempted": False,
                "board_ambiguous": False,
            },
        )
        store.write_json(path, doc)


def delivery_id(mid) -> str:
    """A short id for one merge, sent to the board inside the WHAT text."""
    return hashlib.sha256(mid.encode()).hexdigest()[:8]


def deliver_board(project, ident) -> None:
    """Tell the board about a recorded merge once.

    The attempt is saved before the call. A record that was attempted but never marked
    done is checked against the board's own view for its delivery id; if the board cannot
    confirm it, the record is marked ambiguous and never retried by itself.
    """
    path = _merges_path(project)
    mid = merge_id(ident)
    with store.locked(path):
        doc = store.read_json(path)
        rec = (doc.get("merges") or {}).get(mid)
        if rec is None or rec["board_done"] or rec.get("board_ambiguous"):
            return
        if rec.get("board_attempted"):
            cfg = projmod.load_project(project)
            shown = _board_show(project, cfg)
            if shown is not None and f"[d:{delivery_id(mid)}]" in shown:
                rec["board_done"] = True
            else:
                rec["board_ambiguous"] = True
                notes.problem(
                    project,
                    "board-ambiguous",
                    impact="a merge may or may not have reached the board",
                    fix="check the board, then mark the row by hand if it is missing",
                )
            store.write_json(path, doc)
            return
        rec["board_attempted"] = True
        store.write_json(path, doc)
        if board_merged(project, rec["key"], rec["pr"], delivery_id(mid)):
            rec["board_done"] = True
            store.write_json(path, doc)


def drain_pending_board(project) -> int:
    """Called every tick: deliver or reconcile every merge the board has not confirmed."""
    n = 0
    for mid, rec in list(merge_records(project).items()):
        if rec["board_done"] or rec.get("board_ambiguous"):
            continue
        slug, rest = mid.rsplit("#", 1)
        pr, head = rest.split("@", 1)
        deliver_board(project, {"slug": slug, "pr": int(pr), "head": head})
        n += 1
    return n


def pending_board(project, key) -> list:
    """Idents of this slice's merges whose board call has not landed."""
    out = []
    for mid, rec in merge_records(project).items():
        if rec["key"] == key and not rec["board_done"]:
            slug, rest = mid.rsplit("#", 1)
            pr, head = rest.split("@", 1)
            out.append({"slug": slug, "pr": int(pr), "head": head})
    return out


def board_merged(project: str, key: str, pr, did: str = "") -> bool:
    """Tell the board a PR merged. The words are fixed; no model text reaches the board."""
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    return _board_call(
        project,
        cfg,
        [
            "merged",
            s["row"],
            s["slice"],
            str(pr),
            f"{s['row']} {s['slice']}" + (f" [d:{did}]" if did else ""),
        ],
        key=key,
    )
