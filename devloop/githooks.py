"""Git hooks that keep a headless worker from updating a base branch by accident.

A worker that is not a Claude session (a Codex run, a configured script) never passes
through the Claude Bash hook, so a `git push` in its payload would reach the remote
unchecked. The wrapper points `core.hooksPath` at a directory it owns, for that process
tree only, through GIT_CONFIG_* environment variables. The repo's own config is left
alone. Its `pre-push` refuses a base-branch ref; every other hook runs the repo's
original hook of the same name, so the repo's own checks still apply.
"""

import shlex
import stat
import subprocess

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
    "pre-auto-gc",
    "reference-transaction",
    "push-to-checkout",
)

_CHAIN = """#!/bin/sh
orig={orig}
if [ -x "$orig" ]; then
  exec "$orig" "$@"
fi
exit 0
"""

_PRE_PUSH = """#!/bin/sh
orig={orig}
tmp=$(mktemp) || exit 1
trap 'rm -f "$tmp"' EXIT
cat > "$tmp"
while read -r _lref _lsha rref _rsha; do
  for base in $(printf '%s' "$DEVLOOP_BASE_BRANCHES" | tr ',' ' '); do
    if [ "$rref" = "refs/heads/$base" ]; then
      echo "devloop: a worker may not update the base branch $base" >&2
      exit 1
    fi
  done
done < "$tmp"
if [ -x "$orig" ]; then
  "$orig" "$@" < "$tmp"
  exit $?
fi
exit 0
"""


def _original_hooks_dir(cwd) -> str:
    """The hooks directory git would use in `cwd`, honoring the repo's own hooksPath."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute"]
            + ["--git-path", "hooks"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _write_hook(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def protect_env(env, hooks_dir, cwd) -> dict:
    """`env` plus a per-process git config that installs the guard hooks in `hooks_dir`."""
    orig = _original_hooks_dir(cwd)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in CHAINED:
        _write_hook(hooks_dir / name, _CHAIN.format(orig=shlex.quote(f"{orig}/{name}")))
    _write_hook(
        hooks_dir / "pre-push",
        _PRE_PUSH.format(orig=shlex.quote(f"{orig}/pre-push")),
    )
    try:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    return {
        **env,
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": "core.hooksPath",
        f"GIT_CONFIG_VALUE_{n}": str(hooks_dir),
    }
