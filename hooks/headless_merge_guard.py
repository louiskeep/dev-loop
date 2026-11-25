"""PreToolUse hook: in a headless dev-loop session, `gh pr merge` goes through devloop-merge.

The merge command runs the gate check first. A direct merge from the tick or a worker
would skip it. `devloop-merge` calls gh itself, which is not a tool call and is not seen
here. Parsing reuses gate_guard.classify, which already skips gh global options such
as -R.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

try:
    from . import gate_guard
except ImportError:  # run standalone: python hooks/headless_merge_guard.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_guard

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
REASON = "devloop: merge only through devloop-merge, which checks the gate first"


def blocked(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return bool(re.search(r"\bmerge\b", command)) and "gh" in command
    while tokens and _ENV_ASSIGN.match(tokens[0]):
        tokens = tokens[1:]
    if "gh" not in tokens or "merge" not in tokens:
        return False
    # A wrapper or a chained command is denied by classify or by the metacharacter rule.
    if any(gate_guard._METACHAR.search(t) for t in tokens):
        return True
    action, _ = gate_guard.classify(tokens, [], None)
    return action == "deny"


def main() -> int:
    if os.environ.get("DEVLOOP_HEADLESS") != "1":
        return 0
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - fail open on a malformed event, as gate_guard does
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    if not blocked(event.get("tool_input", {}).get("command", "")):
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON,
                }
            }
        )
    )
    print(REASON, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
