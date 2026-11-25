"""PreToolUse hook: in a headless dev-loop session, merges and base-branch pushes go through devloop-merge.

Threat model: this stops a headless model from merging or pushing to the base branch by
accident; it does not stop a hostile one.

One text match over the raw command, so no wrapper or shell form needs its own parser.
Denied: `gh ... pr ... merge` (any option order), the merge and auto-merge API calls
(`pulls/N/merge`, mergePullRequest, enablePullRequestAutoMerge, /merges, git/refs), and a
`git push` that targets the base branch (an explicit `main` refspec, `--all`, `--mirror`,
or a bare push while the current branch is the base). Feature-branch pushes and any text
that merely says "merge" stay allowed. The one exception to all of it is a command that is
exactly one `devloop-merge` call with no shell metacharacters, which is how the tick merges.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from . import gate_guard
except ImportError:  # run standalone: python hooks/headless_merge_guard.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_guard

REASON = "devloop: merge only through devloop-merge, which checks the gate first"

_OPT = r"(?:\s+(?:-R|--repo|--hostname)(?:\s+|=)\S+|\s+-\S+)*"
_GH_PR_MERGE = re.compile(rf"\bgh\b{_OPT}\s+pr{_OPT}\s+merge\b", re.IGNORECASE)
_ANYWHERE = re.compile(
    r"mergepullrequest|enablepullrequestautomerge|/merges|git/refs|pulls/\d+/merge\b"
)
_GIT_PUSH = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+[^\s-]\S*)?)*?\s+push\b([^;&|`\n)]*)")
_METACHARS = re.compile(r"[;&|`<>\n]|\$\(")
_ONLY_MERGE_CMD = re.compile(r"\s*(?:/[^\s]*/)?devloop-merge(?:\s+[^\s]+)*\s*")


def _base_branches() -> list[str]:
    raw = os.environ.get("DEVLOOP_BASE_BRANCHES", "main,master")
    return [b.strip() for b in raw.split(",") if b.strip()]


def _current_branch(cwd):
    """Current branch by symbolic-ref, so a repo with no commits yet still answers."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


_DASH_C = re.compile(r"\s-C\s+(\S+)")
_LEAD_CD = re.compile(r"^\s*cd\s+(\S+)\s*(?:&&|;)")


def _repo_dir(command: str, prefix: str, cwd):
    """Where the push runs: `git -C X`, else a leading `cd X &&`, else the hook's cwd."""
    m = _DASH_C.search(prefix) or _LEAD_CD.match(command)
    if not m:
        return cwd
    path = Path(m.group(1).strip("'\""))
    return path if path.is_absolute() or not cwd else Path(cwd) / path


def _push_targets_base(args: str, cwd) -> bool:
    base = _base_branches()
    tokens = [t.strip("'\"") for t in args.split()]
    # `HEAD:main` always names main; a bare `HEAD` is the current branch.
    if any(t in ("--all", "--mirror") for t in tokens):
        return True
    positional = [t for t in tokens if t and not t.startswith("-")]
    if len(positional) <= 1:
        # A bare push goes where the current branch tracks; unknown means allow.
        branch = _current_branch(cwd) if cwd else None
        return branch in base
    current = _current_branch(cwd) if cwd else None
    for spec in positional[1:]:
        if gate_guard.push_destination(spec, current) in base:
            return True
    return False


def blocked(command: str, cwd=None) -> bool:
    if _ONLY_MERGE_CMD.fullmatch(command) and not _METACHARS.search(command):
        return False
    text = command.lower()
    if _ANYWHERE.search(text) or _GH_PR_MERGE.search(text):
        return True
    return any(
        _push_targets_base(
            command[m.start(1) : m.end(1)],
            _repo_dir(command, command[m.start() : m.start(1)], cwd),
        )
        for m in _GIT_PUSH.finditer(text)
    )


def main() -> int:
    if os.environ.get("DEVLOOP_HEADLESS") != "1":
        return 0
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - fail open on a malformed event, as gate_guard does
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    if not blocked(event.get("tool_input", {}).get("command", ""), event.get("cwd")):
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
