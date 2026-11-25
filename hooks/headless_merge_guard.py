"""PreToolUse hook: in a headless dev-loop session, merges and pushes go through devloop-merge.

Threat model: this stops a headless model from merging or pushing by accident; it does
not stop a hostile one.

The rule is one fail-closed text match over the raw command, so no wrapper, option order
or shell form needs its own parser. A command is denied when its text (any case) has:
a `gh` word with merge, alias or extension, or with `api` and a write flag or graphql; a
`git` word with `push`; or mergePullRequest, enablePullRequestAutoMerge, /merges or
git/refs. The one exception is a command that is exactly one `devloop-merge` call with
no shell metacharacters, which is how the tick merges.
"""

from __future__ import annotations

import json
import os
import re
import sys

REASON = "devloop: merge only through devloop-merge, which checks the gate first"

_GH = re.compile(r"\bgh\b")
_GIT = re.compile(r"\bgit\b")
_PUSH = re.compile(r"\bpush\b")
_GH_WORDS = re.compile(r"\b(merge|alias|extension)\b")
_API = re.compile(r"\bapi\b")
_API_WRITE = re.compile(
    r"(?:(?:-x|--method)[\s=]+(?!get\b)\S"  # a method other than GET
    r"|(?<![\w-])(?:-f|--field|--raw-field|--input)(?![\w-])"  # body flags (-F lowers to -f)
    r"|\bgraphql\b)"
)
_ANYWHERE = re.compile(r"mergepullrequest|enablepullrequestautomerge|/merges|git/refs")
_METACHARS = re.compile(r"[;&|`<>\n]|\$\(")
_ONLY_MERGE_CMD = re.compile(r"\s*(?:/[^\s]*/)?devloop-merge(?:\s+[^\s]+)*\s*")


def blocked(command: str) -> bool:
    if _ONLY_MERGE_CMD.fullmatch(command) and not _METACHARS.search(command):
        return False
    text = command.lower()
    if _ANYWHERE.search(text):
        return True
    if _GIT.search(text) and _PUSH.search(text):
        return True
    if _GH.search(text):
        if _GH_WORDS.search(text):
            return True
        if _API.search(text) and _API_WRITE.search(text):
            return True
    return False


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
