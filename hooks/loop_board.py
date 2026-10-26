"""Program board for the dev-loop: a single JSON object, rendered as a kanban.

The board is one editable JSON document (`.loop-board.json`). The conductor
edits it directly, or through the small `add-question` / `set-phase` / `move`
subcommands here, for a few tokens and no prose-parsing. `render` turns that
JSON into a self-contained HTML kanban; viewing it costs no model tokens.

`refresh` seeds the board from data the loop already maintains, without
clobbering conductor edits: it regenerates `recently_completed` from the
shipped log (pure history), appends not-yet-done roadmap items to `next_up`,
and merges live `.loop-state.json` phase into matching `current` cards. It
never deletes an authored card, note, or question.

Kanban is the established board pattern (Trello/GitHub Projects): three
columns read left to right as done / doing / next.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

BOARD_FILENAME = ".loop-board.json"
SCHEMA_VERSION = 1
PHASES = [
    "FRAME",
    "PLAN",
    "DEVELOP",
    "SELF-CHECK",
    "VERIFY",
    "REVIEW",
    "REMEDIATE",
    "GATE",
    "DOCUMENT",
]
_COLUMNS = ("recently_completed", "current", "next_up")


class BoardError(Exception):
    pass


# --------------------------------------------------------------------------
# Model: load / validate / save
# --------------------------------------------------------------------------


def empty_board(title: str = "Program Board") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "generated_at": None,
        "columns": {c: [] for c in _COLUMNS},
        "questions": [],
    }


def validate_board(board: dict) -> None:
    if not isinstance(board, dict):
        raise BoardError("board is not an object")
    if board.get("schema_version") != SCHEMA_VERSION:
        raise BoardError(f"unsupported schema_version: {board.get('schema_version')!r}")
    cols = board.get("columns")
    if not isinstance(cols, dict) or set(cols) != set(_COLUMNS):
        raise BoardError(f"columns must be exactly {sorted(_COLUMNS)}")
    for name in _COLUMNS:
        if not isinstance(cols[name], list):
            raise BoardError(f"column {name} must be a list")
        for card in cols[name]:
            if not isinstance(card, dict) or not str(card.get("id", "")).strip():
                raise BoardError(f"each {name} card needs a non-empty id")
            phase = card.get("phase")
            if phase is not None and phase not in PHASES:
                raise BoardError(f"card {card.get('id')} has unknown phase {phase!r}")
    if not isinstance(board.get("questions"), list):
        raise BoardError("questions must be a list")


def load_board(path: Path) -> dict:
    if not path.exists():
        raise BoardError(f"no board at {path}; run 'init' first")
    try:
        board = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise BoardError(f"corrupt board file: {exc}") from exc
    validate_board(board)
    return board


def save_board(path: Path, board: dict) -> None:
    validate_board(board)
    path.write_text(json.dumps(board, indent=2) + "\n")


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _find(cards: list[dict], card_id: str) -> dict | None:
    return next((c for c in cards if c.get("id") == card_id), None)


# --------------------------------------------------------------------------
# Seeding from existing data (non-destructive)
# --------------------------------------------------------------------------

# "## 2026-10-26 (engine): cross-column PR1, ..." -> date, scope, title.
_SHIPPED_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})(?:\s+\(([^)]*)\))?\s*:\s*(.+?)\s*$")
# "- **A-4 File-access authorization.**" -> id, title. IDs look like A-4, B-12, D2.
_TASK_RE = re.compile(r"^\s*-\s+\*\*([A-Z]+-?\d+[a-z]?)\s+(.+?)\.?\*\*")


def _section(text: str, heading: str) -> str:
    """Return the body of the markdown section whose heading contains `heading`."""
    lines = text.splitlines()
    out: list[str] = []
    level: int | None = None
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            depth = len(m.group(1))
            if level is None:
                if heading.lower() in m.group(2).lower():
                    level = depth
                continue
            if depth <= level:  # next sibling/parent heading ends the section
                break
        elif level is not None:
            out.append(line)
    return "\n".join(out)


def parse_shipped(text: str, count: int) -> list[dict]:
    cards: list[dict] = []
    seen: dict[str, int] = {}
    for line in text.splitlines():
        m = _SHIPPED_RE.match(line)
        if not m:
            continue
        date, scope, title = m.group(1), (m.group(2) or "").strip(), m.group(3).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", scope.lower()).strip("-")
        base = f"{date}-{slug}" if slug else date
        seen[base] = seen.get(base, 0) + 1
        card_id = base if seen[base] == 1 else f"{base}-{seen[base]}"  # keep ids unique
        cards.append(
            {"id": card_id, "title": title, "date": date, "scope": scope, "source": "shipped-log"}
        )
        if len(cards) >= count:
            break
    return cards


def parse_next_up(text: str, section: str, done_markers: list[str]) -> list[dict]:
    body = _section(text, section) if section else text
    marks = [re.compile(rf"\b{re.escape(m)}\b") for m in done_markers]
    cards: list[dict] = []
    for raw in body.splitlines():
        m = _TASK_RE.match(raw)
        if not m:
            continue
        if any(mk.search(raw) for mk in marks):  # flagged done on its lead line
            continue
        cards.append(
            {"id": m.group(1), "title": m.group(2).strip(), "source": "roadmap"}
        )
    return cards


def parse_current_assignment(text: str, section: str) -> list[dict]:
    """Rows of the roadmap's current-work table -> candidate current cards."""
    body = _section(text, section)
    cards: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue  # skip separators and non-rows
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() in ("lane", "disposition"):
            continue
        lane = re.sub(r"\*+", "", cells[0]).strip()
        slice_cell = cells[2]
        tm = re.search(r"\*\*(.+?)\*\*", slice_cell)
        label = (tm.group(1) if tm else slice_cell).strip()
        idm = re.match(r"([A-Z]+-?\d+[a-z]?(?:/[A-Z]+-?\d+[a-z]?)*)", label)
        card_id = (idm.group(1) if idm else label[:24]).strip()
        title = label[idm.end() :].strip(" .-") if idm else label
        card = {"id": card_id, "title": title or label, "lane": lane, "source": "roadmap"}
        if len(cells) >= 5:
            card["note"] = re.sub(r"\s+", " ", cells[4])
        cards.append(card)
    return cards


def derive_phase(state: dict) -> str:
    """Best-effort phase from recorded gate state alone (no new logging)."""
    gates = state.get("gates", {}) or {}
    if (state.get("remediation", {}) or {}).get("open_findings", 0):
        return "REMEDIATE"
    dennis = gates.get("dennis", {})
    codex = gates.get("codex", {})
    if dennis.get("status") == "red" or codex.get("status") == "red":
        return "REMEDIATE"
    if dennis.get("status") == "green" and (
        codex.get("status") == "green" or "codex" not in gates
    ):
        return "GATE"
    if dennis.get("status") == "green":
        return "REVIEW"
    if gates.get("plan_review", {}).get("status") == "green":
        return "DEVELOP"
    return "PLAN"


def scan_loop_states(repos: list[Path]) -> list[dict]:
    """One descriptor per repo that currently has a .loop-state.json."""
    found: list[dict] = []
    for repo in repos:
        path = repo / ".loop-state.json"
        if not path.exists():
            continue
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        found.append(
            {
                "repo": repo.name,
                "slice": str(state.get("slice", "")),
                "risk": state.get("risk"),
                "phase": derive_phase(state),
            }
        )
    return found


def refresh(board: dict, cfg: dict, project_root: Path) -> dict:
    """Seed derived columns without deleting authored cards/notes/questions."""

    def _resolve(p: str | None) -> Path | None:
        if not p:
            return None
        q = Path(p)
        return q if q.is_absolute() else (project_root / q)

    roadmap = _resolve(cfg.get("roadmap"))
    shipped = _resolve(cfg.get("shipped_log"))
    cols = board["columns"]

    if shipped and shipped.exists():
        pinned = [c for c in cols["recently_completed"] if c.get("pinned")]
        fresh = parse_shipped(shipped.read_text(), cfg.get("recently_completed_count", 8))
        have = {c["id"] for c in pinned}
        cols["recently_completed"] = pinned + [c for c in fresh if c["id"] not in have]

    def _parts(cid: str) -> set[str]:  # "A-4/B-5" also counts as A-4 and B-5
        return {cid, *(p.strip() for p in cid.split("/"))}

    known: set[str] = set()
    for c in cols["current"] + cols["next_up"]:
        known |= _parts(c["id"])
    if roadmap and roadmap.exists():
        text = roadmap.read_text()
        # current: merge table-derived fields into existing cards, append new ones.
        for cand in parse_current_assignment(text, cfg.get("current_section", "")):
            existing = _find(cols["current"], cand["id"])
            if existing:
                for k in ("lane", "note"):
                    existing.setdefault(k, cand.get(k))
            else:
                cols["current"].append(cand)
                known |= _parts(cand["id"])
        # next_up: append not-yet-present, not-done roadmap items, capped.
        cap = cfg.get("next_up_count", 6)
        for cand in parse_next_up(
            text, cfg.get("next_up_section", ""), cfg.get("done_markers", ["DONE"])
        ):
            if len([c for c in cols["next_up"] if c.get("source") == "roadmap"]) >= cap:
                break
            if not (_parts(cand["id"]) & known):
                cols["next_up"].append(cand)
                known |= _parts(cand["id"])

    # overlay live loop-state phase onto matching current cards (match by id or slice).
    repos = [_resolve(r) for r in cfg.get("repos", [])]
    for st in scan_loop_states([r for r in repos if r]):
        slice_name = st["slice"]

        def _matches(c: dict) -> bool:
            cid, title = c.get("id", ""), c.get("title", "")
            return bool(slice_name) and (
                slice_name == cid
                or slice_name.startswith(f"{cid} ")  # "A-2 100M ..." matches card A-2
                or slice_name in title
                or (title and title in slice_name)
            )

        card = next((c for c in cols["current"] if _matches(c)), None)
        if card is None and st["slice"]:
            card = {"id": st["slice"], "title": st["slice"], "source": "loop-state"}
            cols["current"].append(card)
        if card is not None:
            if not card.get("phase_locked"):  # never stomp a conductor-pinned phase
                card["phase"] = st["phase"]
                card["phase_source"] = "loop-state"
            card["repo"] = st["repo"]
            if st.get("risk"):
                card["risk"] = st["risk"]
    board["generated_at"] = _now()
    return board


# --------------------------------------------------------------------------
# Render: board JSON -> self-contained kanban HTML
# --------------------------------------------------------------------------

_CSS = """
:root{--bg:#f6f7f9;--col:#eceef1;--card:#fff;--ink:#1c2024;--muted:#6b7280;
--line:#d8dce1;--accent:#2563eb;--done:#16a34a;--warn:#d97706;--chip:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--col:#1d2024;--card:#23272d;
--ink:#e7eaee;--muted:#9aa3ad;--line:#333941;--accent:#5b9bff;--done:#4ade80;
--warn:#fbbf24;--chip:#2c313a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{padding:18px 24px 6px}h1{margin:0;font-size:18px}
.meta{color:var(--muted);font-size:12px;margin-top:2px}
.board{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;padding:16px 24px 40px;
align-items:start}
@media (max-width:860px){.board{grid-template-columns:1fr}}
.column{background:var(--col);border-radius:12px;padding:12px}
.column h2{margin:2px 4px 10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);display:flex;justify-content:space-between}
.count{background:var(--chip);color:var(--muted);border-radius:10px;padding:0 7px;font-size:11px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:10px 12px;margin-bottom:9px}
.card .t{font-weight:600}
.card .sub{color:var(--muted);font-size:12px;margin-top:3px}
.card .note{font-size:12px;margin-top:6px;color:var(--ink);opacity:.85}
.tags{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}
.tag{font-size:11px;background:var(--chip);color:var(--muted);border-radius:6px;padding:1px 7px}
.tag.risk{color:var(--warn)}
.phase{display:flex;flex-wrap:wrap;gap:3px;margin-top:8px}
.ph{font-size:10px;padding:1px 5px;border-radius:5px;background:var(--chip);color:var(--muted)}
.ph.on{background:var(--accent);color:#fff}
.ph.past{color:var(--done)}
.questions{margin-top:4px}
.q{background:var(--card);border:1px dashed var(--line);border-radius:9px;
padding:7px 11px;margin-bottom:7px;font-size:13px}
.q::before{content:"?";color:var(--accent);font-weight:700;margin-right:7px}
.empty{color:var(--muted);font-size:12px;font-style:italic;padding:6px 4px}
"""


def _phase_strip(current: str | None) -> str:
    if not current or current not in PHASES:
        return ""
    idx = PHASES.index(current)
    out = []
    for i, ph in enumerate(PHASES):
        cls = "on" if i == idx else ("past" if i < idx else "")
        out.append(f'<span class="ph {cls}">{html.escape(ph)}</span>')
    return '<div class="phase">' + "".join(out) + "</div>"


def _card_html(card: dict) -> str:
    t = html.escape(str(card.get("title", card.get("id", ""))))
    parts = [f'<div class="t">{t}</div>']
    sub = []
    if card.get("date"):
        sub.append(html.escape(str(card["date"])))
    if card.get("scope"):
        sub.append(html.escape(str(card["scope"])))
    if card.get("id") and card.get("id") != card.get("title"):
        sub.append(html.escape(str(card["id"])))
    if sub:
        parts.append(f'<div class="sub">{" · ".join(sub)}</div>')
    parts.append(_phase_strip(card.get("phase")))
    tags = []
    if card.get("lane"):
        tags.append(f'<span class="tag">{html.escape(str(card["lane"]))}</span>')
    if card.get("repo"):
        tags.append(f'<span class="tag">{html.escape(str(card["repo"]))}</span>')
    if card.get("risk"):
        tags.append(f'<span class="tag risk">{html.escape(str(card["risk"]))}</span>')
    if tags:
        parts.append('<div class="tags">' + "".join(tags) + "</div>")
    if card.get("note"):
        parts.append(f'<div class="note">{html.escape(str(card["note"]))}</div>')
    return '<div class="card">' + "".join(p for p in parts if p) + "</div>"


def _column_html(title: str, cards: list[dict], extra: str = "") -> str:
    body = "".join(_card_html(c) for c in cards) if cards else (
        '<div class="empty">nothing here</div>' if not extra else ""
    )
    head = (
        f'<h2>{html.escape(title)}<span class="count">{len(cards)}</span></h2>'
    )
    return f'<div class="column">{head}{body}{extra}</div>'


def render_html(board: dict) -> str:
    cols = board["columns"]
    questions = board.get("questions", [])
    q_html = ""
    if questions:
        items = "".join(
            f'<div class="q">{html.escape(str(q.get("title", q) if isinstance(q, dict) else q))}</div>'
            for q in questions
        )
        q_html = (
            '<h2 style="margin-top:14px">Side discussions<span class="count">'
            f'{len(questions)}</span></h2><div class="questions">{items}</div>'
        )
    gen = board.get("generated_at") or "not refreshed"
    board_json = html.escape(json.dumps(board))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(board.get("title", "Program Board")))}</title>
<style>{_CSS}</style></head><body>
<header><h1>{html.escape(str(board.get("title", "Program Board")))}</h1>
<div class="meta">generated {html.escape(str(gen))}</div></header>
<div class="board">
{_column_html("Recently completed", cols["recently_completed"])}
{_column_html("Current", cols["current"], extra=q_html)}
{_column_html("Next up", cols["next_up"])}
</div>
<script type="application/json" id="board-data">{board_json}</script>
</body></html>
"""


# --------------------------------------------------------------------------
# Config + CLI
# --------------------------------------------------------------------------


def _plugin_root() -> Path:
    import os

    return Path(
        os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent
    )


def load_config(project_root: Path) -> dict:
    cfg: dict = {}
    plugin_cfg = _plugin_root() / "config.json"
    if plugin_cfg.exists():
        cfg.update(json.loads(plugin_cfg.read_text()).get("board", {}))
    repo_cfg = project_root / ".loop-config.json"
    if repo_cfg.exists():
        cfg.update(json.loads(repo_cfg.read_text()).get("board", {}))
    return cfg


def _serve(path: Path, cfg: dict, project_root: Path, host: str, port: int) -> int:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            try:
                board = load_board(path)
            except BoardError as exc:
                self.send_error(404, str(exc))
                return
            if self.path.rstrip("/") in ("/board.json",):
                payload = json.dumps(board).encode()
                ctype = "application/json"
            else:
                payload = render_html(board).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = HTTPServer((host, port), Handler)
    print(f"serving board from {path} at http://{host}:{srv.server_port}/ (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    import sys

    p = argparse.ArgumentParser(prog="loop_board")
    p.add_argument("--repo", default=None, help="project root for config + default paths")
    p.add_argument("--board", default=None, help=f"board file (default: <repo>/{BOARD_FILENAME})")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--title", default="Program Board")
    sub.add_parser("refresh")
    pr = sub.add_parser("render")
    pr.add_argument("--out", default="board.html")
    pq = sub.add_parser("add-question")
    pq.add_argument("title")
    ps = sub.add_parser("set-phase")
    ps.add_argument("card_id")
    ps.add_argument("phase", choices=PHASES)
    pm = sub.add_parser("move")
    pm.add_argument("card_id")
    pm.add_argument("column", choices=_COLUMNS)
    sub.add_parser("show")
    pv = sub.add_parser("serve")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=0)

    args = p.parse_args(argv)
    project_root = Path(args.repo or os.environ.get("PWD") or ".").resolve()
    board_path = Path(args.board) if args.board else project_root / BOARD_FILENAME

    if args.cmd == "init":
        if board_path.exists():
            print(f"board already exists at {board_path}", file=sys.stderr)
            return 1
        save_board(board_path, empty_board(args.title))
        print(f"wrote {board_path}")
        return 0

    try:
        if args.cmd == "serve":
            return _serve(board_path, load_config(project_root), project_root, args.host, args.port)
        board = load_board(board_path)
        if args.cmd == "refresh":
            save_board(board_path, refresh(board, load_config(project_root), project_root))
            print(f"refreshed {board_path}")
        elif args.cmd == "render":
            Path(args.out).write_text(render_html(board))
            print(f"wrote {args.out}")
        elif args.cmd == "add-question":
            qid = f"q{len(board['questions']) + 1}"
            board["questions"].append({"id": qid, "title": args.title})
            save_board(board_path, board)
            print(f"added {qid}")
        elif args.cmd == "set-phase":
            card = _find(board["columns"]["current"], args.card_id)
            if card is None:
                print(f"no current card {args.card_id!r}", file=sys.stderr)
                return 1
            card["phase"] = args.phase
            card["phase_locked"] = True  # pin it so the hook's refresh won't override
            save_board(board_path, board)
            print(f"{args.card_id} -> {args.phase}")
        elif args.cmd == "move":
            src = next(
                (c for col in _COLUMNS for c in board["columns"][col] if c["id"] == args.card_id),
                None,
            )
            if src is None:
                print(f"no card {args.card_id!r}", file=sys.stderr)
                return 1
            for col in _COLUMNS:
                board["columns"][col] = [c for c in board["columns"][col] if c["id"] != args.card_id]
            board["columns"][args.column].append(src)
            save_board(board_path, board)
            print(f"{args.card_id} -> {args.column}")
        elif args.cmd == "show":
            print(json.dumps(board, indent=2))
    except BoardError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
