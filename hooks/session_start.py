"""SessionStart hook: emit the conductor preamble as additionalContext.

Gated on the Task 0 finding: if SessionStart additionalContext does not reach the
model in this build, this becomes a no-op and the skill's description trigger is
the sole load path.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PREAMBLE = Path(
    os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent
) / "skills" / "conducting-the-loop" / "PREAMBLE.md"


def main() -> int:
    try:
        text = _PREAMBLE.read_text().strip()
    except OSError:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
