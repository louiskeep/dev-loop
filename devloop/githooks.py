"""Git hooks that keep a headless worker from updating a base branch by accident.

A worker that is not a Claude session (a Codex run, a configured script) never passes
through the Claude Bash hook, so a `git push` in its payload would reach the remote
unchecked. The wrapper points `core.hooksPath` at a directory it owns, for that process
tree only, through GIT_CONFIG_* environment variables. The repo's own config is left
alone. Its `pre-push` refuses a base-branch ref; every other hook runs the repo's
original hook of the same name, so the repo's own checks still apply.
"""

import stat

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
_ORIG = """dir=$(GIT_CONFIG_COUNT=0 git rev-parse --path-format=absolute --git-path hooks 2>/dev/null) || dir=
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
    + """tmp=$(mktemp) || exit 1
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" || exit 1
while read -r _lref _lsha rref _rsha; do
  for base in $(printf '%s' "$DEVLOOP_BASE_BRANCHES" | tr ',' ' '); do
    if [ "$rref" = "refs/heads/$base" ]; then
      echo "devloop: a worker may not update the base branch $base" >&2
      exit 1
    fi
  done
done < "$tmp"
if [ -n "$orig" ] && [ -x "$orig" ]; then
  "$orig" "$@" < "$tmp"
  exit $?
fi
exit 0
"""
)


def _write_hook(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def protect_env(env, hooks_dir) -> dict:
    """`env` plus a per-process git config that installs the guard hooks in `hooks_dir`."""
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in CHAINED:
        _write_hook(hooks_dir / name, _CHAIN.format(name=name))
    _write_hook(hooks_dir / "pre-push", _PRE_PUSH.format(name="pre-push"))
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
