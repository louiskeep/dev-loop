"""Board mirror and the persisted merge ledger (merges.json)."""

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


def row_close(project: str, row: str) -> None:
    if not isinstance(row, str) or slices.ID_RE.fullmatch(row) is None:
        raise slices.SliceError("bad row id")
    cfg = projmod.load_project(project)
    _board_call(project, cfg, ["close", row], row=row)
    store.append_event(project, "row-closed", row=row)


# --- creating slices ----------------------------------------------------------


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
            },
        )
        store.write_json(path, doc)


def deliver_board(project, ident) -> None:
    """Tell the board about a recorded merge once; a replay finds board_done set."""
    path = _merges_path(project)
    with store.locked(path):
        doc = store.read_json(path)
        rec = (doc.get("merges") or {}).get(merge_id(ident))
        if rec is None or rec["board_done"]:
            return
        if board_merged(project, rec["key"], rec["pr"]):
            rec["board_done"] = True
            store.write_json(path, doc)


def pending_board(project, key) -> list:
    """Idents of this slice's merges whose board call has not landed."""
    out = []
    for mid, rec in merge_records(project).items():
        if rec["key"] == key and not rec["board_done"]:
            slug, rest = mid.rsplit("#", 1)
            pr, head = rest.split("@", 1)
            out.append({"slug": slug, "pr": int(pr), "head": head})
    return out


def board_merged(project: str, key: str, pr) -> bool:
    """Tell the board a PR merged. The words are fixed; no model text reaches the board."""
    s = slices.load(project, key)
    cfg = projmod.load_project(project)
    return _board_call(
        project,
        cfg,
        ["merged", s["row"], s["slice"], str(pr), f"{s['row']} {s['slice']}"],
        key=key,
    )
