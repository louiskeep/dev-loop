"""Stop hook: warn (never block) when a done/merge claim lacks gate evidence.

Stop hooks in this build can block, but this plugin deliberately warns via
additionalContext instead: a text heuristic would false-positive and stall turns.
gate_guard is the wall; this is the visible backstop.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from . import loop_state as ls
except ImportError:  # run standalone: python hooks/done_claim_check.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import loop_state as ls

_DONE = re.compile(
    r"\b(done|complete|completed|merge[- ]?ready|ready to (ship|merge)|shipped)\b",
    re.IGNORECASE,
)


def claims_done(text: str) -> bool:
    return bool(_DONE.search(text or ""))


def _emit(message: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": message,
                }
            }
        )
    )
    return 0


def main_from_event(event: dict) -> int:
    if event.get("stop_hook_active") is True:
        return 0
    if not claims_done(event.get("last_assistant_message", "")):
        return 0
    cwd = Path(event.get("cwd", "."))
    try:
        root = ls.repo_root(cwd)
        state = ls.load_state(root)
        if state is None:
            return _emit(
                "dev-loop: a completion was claimed but there is no .loop-state.json for "
                "this slice. If this is real work, initialize the slice and run the gates."
            )
        landing = ls.rev_parse(root, "HEAD")
        ok, reasons = ls.is_mergeable(state, landing, root, ls.load_config(root))
        if not ok:
            return _emit(
                "dev-loop: completion claimed but the slice is not gate-clean: "
                + "; ".join(reasons)
            )
    except Exception:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return 0  # a warning hook must never disrupt the turn
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return 0
    return main_from_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
