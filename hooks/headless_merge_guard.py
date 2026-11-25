"""PreToolUse hook: in a headless dev-loop session, merges go through devloop-merge.

The merge command runs the gate check first. A direct merge from the tick or a worker
would skip it. `devloop-merge` calls gh itself, which is not a tool call and is not seen
here. Parsing reuses gate_guard's tokenizer rules and `classify`, which already skips
gh global options such as -R.

Covered shapes (accidental or habitual forms, not deliberate evasion): `gh pr merge` in
any option order, `gh api` calls to the pull merge endpoint or the merge and auto-merge
GraphQL mutations, `gh alias` and `gh extension`, `git push` to a base branch, and any
of these behind env, command, exec, sudo or timeout, `VAR=x` prefixes or a `&&`, `;`
or `|` chain. Shell indirection (`bash -c`, `$(...)`, backticks) around such a command
is denied outright, as gate_guard does.
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
_WRAPPERS = {"env", "command", "exec", "sudo", "timeout", "nice", "nohup", "time"}
_OPERATORS = {"&&", "||", ";", "|", "&", "|&", "(", ")", "\n"}
# Options that take a separate value, per wrapper.
_VALUE_OPTS = {
    "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-U"),
    "timeout": ("-s", "-k"),
    "env": ("-u", "-C", "-S"),
    "nice": ("-n",),
}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_MERGE_API = re.compile(r"pulls/\d+/merge")
_MERGE_WORDS = ("mergePullRequest", "enablePullRequestAutoMerge")
_INDIRECTION = re.compile(r"\$\(|`")
_MENTION = re.compile(r"\b(merge|push|api|alias|extension|graphql)\b")
GH_BLOCKED_SUBCOMMANDS = {"alias", "extension", "ext"}
REASON = "devloop: merge only through devloop-merge, which checks the gate first"


def _base_branches() -> list[str]:
    raw = os.environ.get("DEVLOOP_BASE_BRANCHES", "main,master")
    return [b.strip() for b in raw.split(",") if b.strip()]


def _segments(command: str) -> list[list[str]] | None:
    """Tokens split at shell operators, or None when the command does not parse."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    out, cur = [], []
    for t in tokens:
        if t in _OPERATORS or set(t) <= set("&|;()"):
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return [s for s in out if s]


def _strip_prefixes(tokens: list[str]) -> list[str]:
    """Drop VAR=x, env/command/exec/sudo/timeout and their options; basename the program."""
    toks = list(tokens)
    while toks:
        if _ENV_ASSIGN.match(toks[0]):
            toks.pop(0)
        elif os.path.basename(toks[0]) in _WRAPPERS:
            wrapper = os.path.basename(toks.pop(0))
            while toks and (toks[0].startswith("-") or _ENV_ASSIGN.match(toks[0])):
                opt = toks.pop(0)
                if opt in _VALUE_OPTS.get(wrapper, ()) and toks:
                    toks.pop(0)
            if (
                wrapper == "timeout"
                and toks
                and re.fullmatch(r"[\d.]+[smhd]?", toks[0])
            ):
                toks.pop(0)
        else:
            break
    if toks:
        toks[0] = os.path.basename(toks[0])
    return toks


def _gh_subcommand(tokens: list[str]):
    """First positional after gh's global options (-R takes a value)."""
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-R", "--repo", "--hostname"):
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            return tokens[i], i
    return None, i


def _gh_denied(tokens: list[str]) -> bool:
    action, _ = gate_guard.classify(tokens, [], None)
    if action == "deny":  # `gh ... pr ... merge ...` in any option order
        return True
    sub, idx = _gh_subcommand(tokens)
    if sub in GH_BLOCKED_SUBCOMMANDS:
        return True
    if sub == "api":
        rest = tokens[idx + 1 :]
        return any(
            _MERGE_API.search(t) or any(w in t for w in _MERGE_WORDS) for t in rest
        )
    return False


def _git_denied(tokens: list[str]) -> bool:
    protected = _base_branches()
    root = Path.cwd()
    branch = gate_guard._current_branch(root)
    action, payload = gate_guard.classify(tokens, protected, branch)
    if action == "check":
        return True  # a push (or ff merge) that lands on a base branch
    if action == "check_bare_push":
        return branch in protected
    return False


def blocked(command: str) -> bool:
    segments = _segments(command)
    if segments is None:
        return bool(_MENTION.search(command)) and bool(
            re.search(r"\b(gh|git)\b", command)
        )
    for seg in segments:
        toks = _strip_prefixes(seg)
        if not toks:
            continue
        if toks[0] == "gh" and _gh_denied(toks):
            return True
        if toks[0] == "git" and _git_denied(toks):
            return True
        if toks[0] in _SHELLS and "-c" in toks:
            inner = " ".join(toks[toks.index("-c") + 1 :])
            if re.search(r"\b(gh|git)\b", inner) and _MENTION.search(inner):
                return True
    if _INDIRECTION.search(command) and re.search(r"\b(gh|git)\b", command):
        return bool(_MENTION.search(command))
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
