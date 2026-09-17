"""PreToolUse hook: gate protected-branch git ops via a tiny allowlist; fail closed.

classify() is a strict argv parser over shlex tokens. It ALLOWS only a small
canonical set and DENIES anything else in (or possibly in) the push/merge/pull
family, so unrecognized, aliased, or obfuscated forms cannot slip through.

This is best-effort in-session enforcement, not a hard boundary: deliberate
obfuscation (interpreter wrappers, transient config aliases, exotic push.default)
is out of scope by design. Server-side branch protection is the real wall.
"""

from __future__ import annotations

import datetime
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from . import loop_state as ls
except ImportError:  # run standalone: python hooks/gate_guard.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import loop_state as ls

_PUSH_OPTS = {"--force", "-f", "--force-with-lease"}
_METACHAR = re.compile(r"[;&|<>$(){}`\n]")
_RETARGET_OPTS = {"-C", "--git-dir", "--work-tree"}
_OVERRIDE_RE = re.compile(r"^DEVLOOP_OVERRIDE=(.*)$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FAMILY = {"push", "merge", "pull"}
# Read-only builtins that cannot push to a protected remote branch. Used only for
# retargeted (git -C/--git-dir/--work-tree) commands, whose aliases/config live in
# the target repo and cannot be resolved from the current cwd.
_RETARGET_SAFE = {
    "status",
    "log",
    "diff",
    "show",
    "rev-parse",
    "branch",
    "tag",
    "describe",
    "config",
    "remote",
    "for-each-ref",
    "ls-files",
    "ls-remote",
    "ls-tree",
    "symbolic-ref",
    "name-rev",
    "shortlog",
    "cat-file",
    "blame",
    "fetch",
}


def _strip_heads(ref: str) -> str:
    return ref.removeprefix("refs/heads/")


def _mentions_family(tokens: list[str]) -> bool:
    return bool(_FAMILY & set(tokens)) or ("pr" in tokens and "merge" in tokens)


def _skip_global(tokens: list[str]) -> tuple[bool, int]:
    """Skip git global options; return (repo_retargeted, index_of_subcommand)."""
    i, retarget = 1, False
    while i < len(tokens):
        t = tokens[i]
        if t in _RETARGET_OPTS:
            retarget = True
            i += 2
            continue
        if t.startswith(("--git-dir=", "--work-tree=", "-C")):
            retarget = True
            i += 1
            continue
        if t == "-c":
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break
    return retarget, i


def classify(
    tokens: list[str], protected: list[str], current_branch: str | None
) -> tuple[str, str | None]:
    if not tokens:
        return ("allow", None)
    head = tokens[0]
    if head not in ("git", "gh"):
        return (
            (
                "deny",
                "unrecognized wrapper/prefix around a push/merge/pull; run plain git",
            )
            if _mentions_family(tokens)
            else ("allow", None)
        )
    if head == "gh":
        if (
            "pr" in tokens and "merge" in tokens
        ):  # catches global opts before 'pr merge'
            return (
                "deny",
                (
                    "gh pr merge is not gated in-session (validates the local checkout, "
                    "not the PR head); use a gated push or server-side protection."
                ),
            )
        return ("allow", None)
    retarget, idx = _skip_global(tokens)
    if idx >= len(tokens):
        return ("allow", None)
    sub, args = tokens[idx], tokens[idx + 1 :]
    if retarget and (sub in _FAMILY or sub == "pr"):
        return (
            "deny",
            (
                "git -C/--git-dir/--work-tree with a push/merge/pull retargets the repo; "
                "run it inside that repo without indirection."
            ),
        )
    if sub in ("pull", "merge") and current_branch is None:
        return (
            "deny",
            (
                "cannot determine the current branch; resolve the repo state before "
                "a merge/pull."
            ),
        )
    if sub == "pull":
        return (
            (
                "deny",
                (
                    "git pull merges into the current branch; while on a protected branch use "
                    "an explicit gated 'git merge --ff-only'."
                ),
            )
            if current_branch in protected
            else ("allow", None)
        )
    if sub == "merge":
        if current_branch not in protected:
            return ("allow", None)
        opts = [a for a in args if a.startswith("-")]
        pos = [a for a in args if not a.startswith("-")]
        if opts != ["--ff-only"] or len(pos) != 1:
            return (
                "deny",
                "only 'git merge --ff-only <one-ref>' into a protected branch is supported",
            )
        return ("check", pos[0])
    if sub == "push":
        opts = [a for a in args if a.startswith("-")]
        pos = [a for a in args if not a.startswith("-")]
        for o in opts:
            if o.split("=", 1)[0] not in _PUSH_OPTS:
                return ("deny", f"unsupported push option {o}")
        if len(pos) <= 1:
            return ("check_bare_push", pos[0] if pos else None)  # bare or remote-only
        if len(pos) > 2:
            return ("deny", "multiple refspecs are not supported here")
        refspec = pos[1]
        if "*" in refspec:
            return ("deny", "wildcard refspec is not supported here")
        if refspec.count(":") > 1:
            return ("deny", "malformed refspec (multiple colons)")
        colon = ":" in refspec
        src, dst = refspec.split(":", 1) if colon else (refspec, refspec)
        if not src or not dst:
            return ("deny", "delete/empty refspec is not supported here")
        dst = _strip_heads(dst)
        if dst in ("HEAD", "@"):
            if colon:
                return ("deny", "cannot resolve remote destination 'HEAD'/'@'")
            if current_branch in (None, "", "HEAD"):
                return (
                    "deny",
                    "detached/unknown HEAD push cannot be resolved; push an explicit branch",
                )
            dst = current_branch
        if dst.startswith(
            "refs/"
        ):  # non-branch namespace (tags/remotes/...) after stripping heads
            return ("deny", f"unsupported push destination {dst}")
        return ("check", src) if dst in protected else ("allow", None)
    # any other subcommand could be a user alias that expands to a push/merge
    return ("maybe_alias", sub)


def _deny(reason: str) -> int:
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
    return 2


def _current_branch(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return None


def _git_config(root: Path, key: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "config", "--get", key],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return ""


def _bare_push_target(
    root: Path, remote: str | None, branch: str, protected: list[str]
) -> tuple[str, str | None]:
    """Decide a bare/remote-only push: allow (feature), deny (ambiguous), or check HEAD."""
    remote = (
        remote
        or _git_config(root, f"branch.{branch}.pushRemote")
        or _git_config(root, "remote.pushDefault")
        or _git_config(root, f"branch.{branch}.remote")
        or "origin"
    )
    if _git_config(root, f"remote.{remote}.push"):
        return (
            "deny",
            (
                "a configured remote.push refspec makes this push ambiguous; "
                "push an explicit single refspec."
            ),
        )
    push_default = _git_config(root, "push.default") or "simple"
    if push_default not in ("simple", "current"):
        return (
            "deny",
            (
                f"push.default={push_default} may push a differently-named or protected "
                "branch; push an explicit single refspec."
            ),
        )
    return ("check", "HEAD") if branch in protected else ("allow", None)


def _escape(
    root: Path, config: dict, reason: str | None, command: str, landing: str
) -> bool:
    if not (config.get("escape_hatch") and reason):
        return False
    line = json.dumps(
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "command": command,
            "reason": reason,
            "landing_commit": landing,
        }
    )
    with (root / ".loop-audit.log").open("a") as fh:
        fh.write(line + "\n")
    return True


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = event.get("tool_input", {}).get("command", "")
    cwd = Path(event.get("cwd", "."))
    try:
        # Repo-independent obfuscation rejection first, so 'cd /x && git push origin main'
        # is denied even when cwd is not (yet) a repo. Obfuscation is never escapable.
        try:
            tokens = shlex.split(command)
        except ValueError:
            return (
                _deny("dev-loop: unparseable command (unbalanced quotes)")
                if re.search(r"\b(push|merge|pull)\b", command)
                else 0
            )
        override = None
        if tokens and (m := _OVERRIDE_RE.match(tokens[0])):
            override, tokens = m.group(1), tokens[1:]
        # strip leading env-assignment prefixes (e.g. GIT_SSH=x git ...) so the real
        # git head and its global options are seen by the checks below.
        while tokens and _ENV_ASSIGN.match(tokens[0]):
            tokens = tokens[1:]
        if _mentions_family(tokens) and any(_METACHAR.search(t) for t in tokens):
            return _deny(
                "dev-loop: shell metacharacter/expansion in a push/merge command; "
                "run the gated step alone"
            )
        # Retargeting points at another repo whose aliases/config cannot be resolved from
        # cwd, so decide retargeted commands entirely here: allow only read-only safe
        # builtins, deny everything else.
        if tokens and tokens[0] == "git":
            retarget, idx = _skip_global(tokens)
            if retarget:
                if idx >= len(tokens):
                    return 0  # bare 'git -C x' with no subcommand
                sub = tokens[idx]
                if sub in _RETARGET_SAFE:
                    return 0
                return _deny(
                    f"dev-loop: 'git ... {sub}' with -C/--git-dir/--work-tree targets "
                    "another repo where aliases/config can't be resolved; run it inside "
                    "that repo."
                )
        try:
            root = ls.repo_root(cwd)
        except ls.GitError:
            return 0  # not a git repo: nothing to protect
        config = ls.load_config(root)
        protected = config.get("protected_branches", ["main", "master"])
        branch = _current_branch(root)

        action, payload = classify(tokens, protected, branch)
        if action == "allow":
            return 0
        if action == "deny":
            return (
                0
                if _escape(root, config, override, command, "n/a")
                else _deny(f"dev-loop: {payload}")
            )
        if action == "maybe_alias":
            if _git_config(root, f"alias.{payload}"):
                return (
                    0
                    if _escape(root, config, override, command, "n/a")
                    else _deny(
                        f"dev-loop: 'git {payload}' is a configured alias; run the explicit command it expands to"
                    )
                )
            return 0  # normal builtin (status, rebase, reset, ...); does not push a protected remote branch
        if action == "check_bare_push":
            if branch is None:
                return (
                    0
                    if _escape(root, config, override, command, "n/a")
                    else _deny(
                        "dev-loop: cannot determine the current branch; push an explicit refspec"
                    )
                )
            act, ref = _bare_push_target(root, payload, branch, protected)
            if act == "allow":
                return 0
            if act == "deny":
                return (
                    0
                    if _escape(root, config, override, command, "n/a")
                    else _deny(f"dev-loop: {ref}")
                )
            landing = ls.rev_parse(root, "HEAD")
        else:  # "check"
            landing = ls.rev_parse(root, payload + "^{commit}")

        state = ls.load_state(root)
        if state is None:
            reason = (
                "dev-loop: no .loop-state.json for this slice; run 'loop_state.py init' "
                "and pass the gates before touching a protected branch."
            )
        else:
            ok, reasons = ls.is_mergeable(state, landing, root, config)
            if ok:
                return 0
            reason = "dev-loop blocks this operation: " + "; ".join(reasons)
        return 0 if _escape(root, config, override, command, landing) else _deny(reason)
    except (ls.StateError, ls.GitError) as exc:
        return _deny(f"dev-loop fail-closed (guard error): {exc}")
    except Exception as exc:  # noqa: BLE001 (fail-closed/defensive: any error -> safe default)
        return _deny(f"dev-loop fail-closed (unexpected guard error): {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
