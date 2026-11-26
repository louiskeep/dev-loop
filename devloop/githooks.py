"""The ref policy for `git push`, and the git hooks that carry it.

git's `pre-push` receives every ref update after git has resolved refspecs, `--all`,
`--mirror` and `--prune`, so the policy reads facts instead of guessing from Bash text.
`check_refs` is the one place that decides. Two routes call it: the `pre-push` that
`protect_env` gives each loop worker (this process tree only, through GIT_CONFIG_*
variables, the repo's own config left alone), and the clone shim that `devloop-install`
writes into every configured repo for interactive sessions and their subagents.

The policy covers pushes to the project's GitHub remotes only. A node mirror (vm-run,
decoy-vm-t3) takes any push, and the URL is compared, never printed (it can carry a token).

Standard library only: the clone shim runs this file alone with `python3 -I`.
"""

import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

# Every hook git may call except pre-push, which carries the guard itself.
CHAINED = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "post-rewrite",
    "post-index-change",
    "pre-auto-gc",
    "reference-transaction",
    "push-to-checkout",
    "pre-receive",
    "update",
    "proc-receive",
    "post-receive",
    "post-update",
    "fsmonitor-watchman",
    "sendemail-validate",
)

# The repository that runs the hook is not the one that started the worker, so the
# original hooks directory is resolved here, with this guard's config override removed.
_ORIG = """dir=$(GIT_CONFIG_COUNT={count} git rev-parse --path-format=absolute --git-path hooks 2>/dev/null) || dir=
orig="$dir/{name}"
[ -n "$dir" ] || orig=
"""

_CHAIN = (
    "#!/bin/sh\n"
    + _ORIG
    + """if [ -n "$orig" ] && [ -x "$orig" ]; then
  exec "$orig" "$@"
fi
exit 0
"""
)

_PRE_PUSH = (
    "#!/bin/sh\n"
    + _ORIG
    + """policy={policy}
tmp=$(mktemp) || exit 1
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" || exit 1
if [ ! -f "$policy" ]; then
  echo "push refused: push policy not installed; run devloop-install" >&2
  exit 1
fi
/usr/bin/env python3 -I "$policy" pre-push "$@" < "$tmp" || exit 1
if [ -n "$orig" ] && [ -x "$orig" ]; then
  "$orig" "$@" < "$tmp"
  exit $?
fi
exit 0
"""
)

MARKER = "# devloop-ref-policy v1"
# `devloop-install` points this at the installed lib, so a reinstall updates the policy
# without rewriting the shim. A missing target refuses the push (fail closed).
SHIM = f"""#!/bin/sh
{MARKER}
f="$HOME/.local/share/devloop/lib/devloop/githooks.py"
if [ ! -f "$f" ]; then
  echo "push refused: push policy not installed; run devloop-install" >&2
  exit 1
fi
exec /usr/bin/env python3 -I "$f" pre-push "$@"
"""

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_ZERO = re.compile(r"0+")
_SAFE_REF = re.compile(r"[\x21-\x7e]+")
_GITHUB = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://(?:[^/@]*@)?|(?:[^/@:\s]+@)?)github\.com(?:[:/]|\Z)",
    re.IGNORECASE,
)
_GIT_TIMEOUT_S = 20


def _git(args, cwd=None):
    """(exit code, stdout) of one git call; a call that cannot run is a failure."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return r.returncode, r.stdout


def is_protected_url(url, cwd=None) -> bool:
    """True when `url` is one of this repo's `origin` URLs or any github.com spelling."""
    if _GITHUB.match(url):
        return True
    for opts in (("--all",), ("--push", "--all")):
        code, out = _git(["remote", "get-url", *opts, "origin"], cwd)
        if code == 0 and url in out.splitlines():
            return True
    return False


def _bases(env) -> set:
    extra = (b.strip() for b in (env.get("DEVLOOP_BASE_BRANCHES") or "").split(","))
    return {"main", "master", *(b for b in extra if b)}


def _in_reflog(remote_oid, local_ref, cwd) -> bool:
    """git's --force-if-includes rule: the old remote tip is reachable from the reflog of
    the pushed ref, or from this worktree's HEAD reflog when that is not a branch."""
    ref = local_ref if re.fullmatch(r"refs/heads/.+", local_ref) else "HEAD"
    code, out = _git(["rev-list", "--walk-reflogs", "-n", "100", ref], cwd)
    tips = out.split() if code == 0 else []
    if not tips:
        return False
    code, out = _git(["rev-list", "-n", "1", remote_oid, "--not", *tips], cwd)
    return code == 0 and out.strip() == ""


def _judge(line, worker, bases, cwd):
    """(reason or None, remote ref) for one `lref loid rref roid` line."""
    f = line.split(" ")
    if len(f) != 4 or not (_OID.fullmatch(f[1]) and _OID.fullmatch(f[3])):
        return "unreadable push line", f[2] if len(f) == 4 else None
    lref, loid, rref, roid = f
    if _ZERO.fullmatch(loid):
        return "deletes a remote ref", rref
    if re.fullmatch(r"refs/tags/.+", rref) and _ZERO.fullmatch(roid) and not worker:
        return None, rref  # a new release tag from an interactive session
    if not re.fullmatch(r"refs/heads/.+", rref):
        return "not a branch", rref
    name = rref[len("refs/heads/") :]
    if name in bases and worker:
        return f"a worker may not update the base branch {name}", rref
    if _ZERO.fullmatch(roid):
        return None, rref
    if _git(["cat-file", "-e", f"{roid}^{{commit}}"], cwd)[0] != 0:
        return "remote has commits this clone has not fetched; fetch first", rref
    if _git(["merge-base", "--is-ancestor", roid, loid], cwd)[0] == 0:
        return None, rref
    if name in bases:
        return "rewrites a base branch", rref
    if not _in_reflog(roid, lref, cwd):
        return "rewrites commits this branch never had", rref
    return None, rref


def check_refs(lines, env=None, cwd=None) -> list:
    """One `push refused: <ref>: <reason>` per refused line; empty when all pass.

    `lines` are pre-push stdin lines with no newline stripped, so a stray one is refused.
    """
    env = os.environ if env is None else env
    worker = bool(env.get("DEVLOOP_RUN"))
    bases = _bases(env)
    out = []
    for line in lines:
        reason, ref = _judge(line, worker, bases, cwd)
        if reason:
            shown = ref if ref and _SAFE_REF.fullmatch(ref) else "(unreadable)"
            out.append(f"push refused: {shown}: {reason}")
    return out


def pre_push_main(argv, text, env=None, cwd=None) -> int:
    """`pre-push <remote> <url>` with the update lines in `text`. 0 allows, 1 refuses."""
    env = os.environ if env is None else env
    # An unknown URL is treated as the project's remote: fail closed.
    if len(argv) > 1 and not is_protected_url(argv[1], cwd):
        return 0
    if env.get("DECOY_PUSH_OVERRIDE") == "1" and not env.get("DEVLOOP_RUN"):
        print("push policy override", file=sys.stderr)
        return 0
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    refused = check_refs(lines, env, cwd)
    for message in refused:
        print(message, file=sys.stderr)
    return 1 if refused else 0


def install_clone_hook(repo) -> str:
    """Write the clone shim to the common git dir, so every linked worktree inherits it.

    Returns installed, updated, unchanged, `skipped: <why>` (no repository there) or
    `refused: <why>`. A pre-push that does not
    carry this marker, or a local core.hooksPath, is the owner's and is never touched.
    """
    code, common = _git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"], str(repo)
    )
    if code != 0 or not common.strip():
        return "skipped: not a git repository"
    if _git(["config", "--local", "--get", "core.hooksPath"], str(repo))[0] == 0:
        return "refused: core.hooksPath is set in this repository"
    hooks = Path(common.strip()) / "hooks"
    hook = hooks / "pre-push"
    status = "installed"
    if hook.is_symlink() or hook.exists():
        try:
            current = hook.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return "refused: pre-push cannot be read"
        if MARKER not in current.split("\n"):
            return "refused: a pre-push hook that is not ours exists"
        if current == SHIM and os.access(hook, os.X_OK):
            return "unchanged"
        status = "updated"
    tmp = hooks / ".pre-push.devloop-new"
    try:
        hooks.mkdir(parents=True, exist_ok=True)
        tmp.write_text(SHIM, encoding="utf-8")
        tmp.chmod(0o755)
        os.replace(tmp, hook)
    except OSError:
        tmp.unlink(missing_ok=True)
        return "refused: the hook cannot be written"
    return status


def _write_hook(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def protect_env(env, hooks_dir) -> dict:
    """`env` plus a per-process git config that installs the guard hooks in `hooks_dir`."""
    hooks_dir.mkdir(parents=True, exist_ok=True)
    try:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    for name in CHAINED:
        _write_hook(hooks_dir / name, _CHAIN.format(name=name, count=n))
    policy = shlex.quote(str(Path(__file__).resolve()))
    pre_push = _PRE_PUSH.format(name="pre-push", count=n, policy=policy)
    _write_hook(hooks_dir / "pre-push", pre_push)
    return {
        **env,
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": "core.hooksPath",
        f"GIT_CONFIG_VALUE_{n}": str(hooks_dir),
    }


def main(argv) -> int:
    """`githooks.py pre-push <remote> <url>`, update lines on stdin."""
    if len(argv) < 2 or argv[0] != "pre-push":
        print("usage: githooks.py pre-push REMOTE URL", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
        return pre_push_main(argv[1:], text)
    except Exception as exc:  # noqa: BLE001 - any failure must refuse, with a fixed text
        name = type(exc).__name__
    print(f"push refused: the push policy failed ({name})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
