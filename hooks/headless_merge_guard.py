"""PreToolUse hook: only the orchestrator merges or pushes to a base branch.

Workers (DEVLOOP_RUN set) and subagents (stdin `agent_id`) may push feature branches only.
The headless tick merges through devloop-merge and may push to base; interactive top-level
sessions are not restricted.

Threat model: this stops a headless model from merging or pushing to the base branch by
accident; it does not stop a hostile one.

The executable of each shell command segment decides, so a search term or a commit message
can name a command. Segments split on ; && || | & and newlines outside quotes. VAR=val, env,
sudo, timeout N and xargs are skipped, and sh -c, eval and find -exec scripts are read too.
A worker's command that cannot be parsed is denied.
Denied:`gh ... pr ... merge` (any option order), the merge and auto-merge API calls
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
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from . import gate_guard
except ImportError:  # run standalone: python hooks/headless_merge_guard.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_guard

REASON = "devloop: merge only through devloop-merge, which checks the gate first"
WORKER_REASON = "devloop: only the orchestrator pushes to main or merges; push your feature branch instead"

_OPT = r"(?:\s+(?:-R|--repo|--hostname)(?:\s+|=)\S+|\s+-\S+)*"
_GH_PR_MERGE = re.compile(rf"\bgh\b{_OPT}\s+pr{_OPT}\s+merge\b", re.IGNORECASE)
_ANYWHERE = re.compile(
    r"mergepullrequest|enablepullrequestautomerge|/merges|git/refs|pulls/\d+/merge\b"
)
_GIT_PUSH = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+[^\s-]\S*)?)*?\s+push\b([^;&|`\n)]*)")
_METACHARS = re.compile(r"[;&|`<>\n]|\$\(")
_ONLY_MERGE_CMD = re.compile(r"\s*(?:/[^\s]*/)?devloop-merge(?:\s+[^\s]+)*\s*")


def _base_branches() -> list[str]:
    raw = os.environ.get("DEVLOOP_BASE_BRANCHES", "")
    extra = [b.strip() for b in raw.split(",") if b.strip()]
    return sorted({"main", "master", *extra})


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


_SEPARATORS = (";", "&&", "||", "|", "&", "\n", "(", ")", "`")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_VAR = re.compile(r"[A-Za-z_]\w*=.*")
_PREFIXES = {
    "env",
    "sudo",
    "nohup",
    "time",
    "command",
    "exec",
    "nice",
    "timeout",
    "xargs",
}
# Options of a prefix command that take a separate value.
_OPT_VALUE = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-D"},
    "env": {"-u", "-C", "-S"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a", "-i"},
}
_NETWORK_TOOLS = {"gh", "curl", "wget", "http", "https", "hub"}


class _Unparseable(Exception):
    pass


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def _strip_heredocs(command: str) -> str:
    """Drop here-document bodies: they are input text, never commands."""
    lines, out, end = command.split("\n"), [], None
    for line in lines:
        if end is not None:
            if line.strip() == end:
                end = None
                out.append(line)
            continue
        out.append(line)
        m = _HEREDOC.search(line)
        if m:
            end = m.group(2)
    return "\n".join(out)


def _segments(command: str) -> list[str]:
    """Split on shell command separators and `$(` that are outside quotes."""
    command = _strip_heredocs(command)
    out, cur, quote, i = [], [], None, 0
    while i < len(command):
        c = command[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                cur.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < len(command):
            cur.append(command[i : i + 2])
            i += 1
        elif command.startswith("$(", i):
            out.append("".join(cur))
            cur = []
            i += 1
        else:
            sep = next((s for s in _SEPARATORS if command.startswith(s, i)), None)
            if sep:
                out.append("".join(cur))
                cur = []
                i += len(sep) - 1
            else:
                cur.append(c)
        i += 1
    if quote:
        raise _Unparseable("unbalanced quote")
    out.append("".join(cur))
    return [s for s in (x.strip() for x in out) if s]


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError as exc:
        raise _Unparseable(str(exc)) from exc


_RESERVED = {
    "if", "then", "else", "elif", "do", "while", "until", "for", "case", "in",
    "fi", "done", "esac", "{", "}", "!",
}  # fmt: skip


def _command_start(tokens: list[str]) -> int:
    """Index of the real executable: past reserved words, VAR=val, env, sudo, timeout N,
    xargs and the like. A prefix that runs off the end, or env -S, is unparseable."""
    i, after_prefix = 0, False
    while i < len(tokens):
        tok = tokens[i]
        name = os.path.basename(tok)
        if tok in _RESERVED:
            i += 1
            after_prefix = False
        elif _VAR.fullmatch(tok):
            i += 1
        elif name in _PREFIXES:
            i += 1
            after_prefix = True
            values = _OPT_VALUE.get(name, set())
            while i < len(tokens) and (
                tokens[i].startswith("-") or _VAR.fullmatch(tokens[i])
            ):
                if name == "env" and (
                    tokens[i].startswith("-S")
                    or tokens[i] == "--split-string"
                    or tokens[i].startswith("--split-string=")
                ):
                    raise _Unparseable("env -S runs a command string")
                i += 2 if tokens[i] in values else 1
            if name == "timeout":
                i += 1  # the duration
        else:
            return i
    if after_prefix:
        raise _Unparseable("a command prefix with no command")
    return i


def _commands(command: str):
    """Yield (executable basename, argument tokens) for every command in the text,
    including a script handed to a shell with -c, to eval, or to find -exec."""
    for seg in _segments(command):
        tokens = _tokens(seg)
        i = _command_start(tokens)
        if i >= len(tokens):
            continue
        exe, args = os.path.basename(tokens[i]).lower(), tokens[i + 1 :]
        yield exe, args
        if exe in _SHELLS and any(re.fullmatch(r"-[a-z]*c[a-z]*", a) for a in args):
            script = [a for a in args if not a.startswith("-")]
            if script:
                yield from _commands(script[0])
        elif exe == "eval":
            yield from _commands(" ".join(args))
        elif exe == "find":
            for j, a in enumerate(args):
                if a in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = []
                    for b in args[j + 1 :]:
                        if b in (";", "+"):
                            break
                        inner.append(b)
                    yield from _commands(shlex.join(inner))


def _git_push_args(args: list[str]):
    """(directory option, push arguments) when this git call is a push, else None."""
    i, where = 0, None
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            where = args[i + 1]
            i += 2
        elif a in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(
            args
        ):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i < len(args) and args[i] == "push":
        return where, args[i + 1 :]
    return None


def _blocked_commands(command, cwd, *, worker, check_base_pushes) -> bool:
    here = cwd
    for exe, args in _commands(command):
        text = " ".join([exe, *args]).lower()
        if exe == "cd" and args:
            path = Path(args[0])
            here = path if path.is_absolute() or not here else Path(here) / path
        elif exe == "devloop-merge" and worker:
            return True
        elif exe in _NETWORK_TOOLS and (
            _ANYWHERE.search(text) or _GH_PR_MERGE.search(text)
        ):
            return True
        elif exe == "git" and check_base_pushes:
            push = _git_push_args(args)
            if push is None:
                continue
            where, rest = push
            root = here
            if where:
                path = Path(where)
                root = path if path.is_absolute() or not here else Path(here) / path
            if _push_targets_base(" ".join(rest), root):
                return True
    return False


def blocked(command: str, cwd=None, *, worker=False, check_base_pushes=True) -> bool:
    """True when the command must be denied.

    A worker (loop worker or subagent) may not merge at all, devloop-merge included. The
    headless tick may merge only through a lone devloop-merge call and, with check_base_pushes
    False, may push to a base branch (docs commits in DOCUMENT). The executable of each shell
    command segment decides, so a search term or a commit message may name a command. Text
    that cannot be parsed is denied for a worker and gets the plain text match otherwise.
    """
    if (
        not worker
        and _ONLY_MERGE_CMD.fullmatch(command)
        and not _METACHARS.search(command)
    ):
        return False
    try:
        return _blocked_commands(
            command, cwd, worker=worker, check_base_pushes=check_base_pushes
        )
    except _Unparseable:
        if worker:
            return True
        return _blocked_text(command, cwd, check_base_pushes)


def _blocked_text(command: str, cwd, check_base_pushes) -> bool:
    text = command.lower()
    if _ANYWHERE.search(text) or _GH_PR_MERGE.search(text):
        return True
    if not check_base_pushes:
        return False
    return any(
        _push_targets_base(
            command[m.start(1) : m.end(1)],
            _repo_dir(command, command[m.start() : m.start(1)], cwd),
        )
        for m in _GIT_PUSH.finditer(text)
    )


def _deny(reason) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    print(reason, file=sys.stderr)
    return 2


def main() -> int:
    """Roles: a loop worker (DEVLOOP_RUN) or a subagent (agent_id) is never the orchestrator.
    The headless tick is the orchestrator under the lone-devloop-merge rule; an interactive
    top-level session is not restricted here."""
    in_run = bool(os.environ.get("DEVLOOP_RUN"))
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
    except Exception:  # noqa: BLE001 - only a worker fails closed on a bad event
        return _deny(WORKER_REASON) if in_run else 0
    worker = in_run or bool(event.get("agent_id"))
    if not worker and os.environ.get("DEVLOOP_HEADLESS") != "1":
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    try:
        command = event.get("tool_input", {}).get("command", "")
        deny = blocked(
            command, event.get("cwd"), worker=worker, check_base_pushes=worker
        )
    except Exception:  # noqa: BLE001
        return _deny(WORKER_REASON) if worker else 0
    if not deny:
        return 0
    return _deny(WORKER_REASON if worker else REASON)


if __name__ == "__main__":
    sys.exit(main())
