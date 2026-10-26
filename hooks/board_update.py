"""Keep the program board current, with no model tokens.

Wired as a PostToolUse(Bash) and Stop hook. The dev-loop has no phase event,
so this reacts to the signals that do exist: the conductor's own
`loop_state.py` calls and git moves are the phase boundaries, and Stop marks
the end of each step. On those, it re-seeds the board from `.loop-state.json`
(phase), the roadmap, and the shipped log, then re-renders the HTML.

Best-effort and silent: any error is swallowed and the turn is never
disrupted. It is a complete no-op in a repo that has no board file, so the
hook is inert until a board is created with `loop_board.py init`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from . import loop_board as lb
    from . import loop_state as ls
except ImportError:  # run standalone: python hooks/board_update.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import loop_board as lb
    import loop_state as ls

# Commands that plausibly moved the loop or HEAD; other Bash calls are skipped
# so the board is not re-rendered on every unrelated command.
_TRIGGERS = ("loop_state.py", "git commit", "git merge", "git push", "git checkout", "git reset")


def _board_path(project_root: Path, cfg: dict) -> Path:
    bp = cfg.get("board_path")
    if bp:
        q = Path(bp)
        return q if q.is_absolute() else project_root / q
    return project_root / lb.BOARD_FILENAME


def _update(cwd: Path) -> None:
    try:
        project_root = ls.repo_root(cwd)
    except Exception:  # noqa: BLE001 — not a git repo: use cwd as-is
        project_root = cwd
    cfg = lb.load_config(project_root)
    path = _board_path(project_root, cfg)
    if not path.exists():  # inert until a board exists
        return
    board = lb.load_board(path)
    # always include the current repo in the loop-state scan, config or not.
    repos = list(cfg.get("repos", []))
    if str(project_root) not in {str(Path(r)) for r in repos}:
        repos = repos + [str(project_root)]
    cfg = {**cfg, "repos": repos}
    lb.refresh(board, cfg, project_root)
    lb.save_board(path, board)
    out = cfg.get("html_out")
    html_path = (Path(out) if out and Path(out).is_absolute() else project_root / (out or "board.html"))
    html_path.write_text(lb.render_html(board))


def main_from_event(event: dict) -> int:
    if event.get("tool_name") == "Bash":
        cmd = (event.get("tool_input") or {}).get("command", "")
        if not any(t in cmd for t in _TRIGGERS):
            return 0
    cwd = Path(event.get("cwd", "."))
    try:
        _update(cwd)
    except Exception:  # noqa: BLE001 — a side-effect hook must never disrupt the turn
        return 0
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    return main_from_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
