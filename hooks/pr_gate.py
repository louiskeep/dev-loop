"""PreToolUse hook: `gh pr create` needs a quality-gate receipt for the exact HEAD.

Substring checks on the raw command text; the text is never split or parsed, so a quote,
a comment or a wrapper (`bash -lc`, `timeout`) cannot hide the words. Pushes are not
inspected here: git's pre-push (devloop/githooks.py) sees the real refs.

Covered: `gh pr create` or `gh pr new`, moved to another target with `cd`, `-C`, `--repo`
and the like, and `--no-verify` or `core.hooksPath` from a configured repo. False positive
accepted: `git grep 'gh pr create'` without a receipt is denied. Not covered (V2-LOG):
`gh api` POST, `eval`, words built at run time, a skip-hooks flag run from another cwd.

Fail closed, one load path. Only the standard library is imported at the top. The words
are matched first, then the installed `devloop` package is imported inside one `try`; any
failure there denies when the command is a PR create or names a skip-hooks word, and allows
everything else, so an unrelated command never breaks the session.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_CREATE = re.compile(r"\bpr\s+(create|new)\b")
_RETARGET = re.compile(
    r"(?<![\w./-])(?:cd|pushd|popd)(?![\w./-])"
    r"|\s-[CRH]|--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE|GH_REPO|--repo|--head"
)
_SKIP_HOOKS = re.compile(r"--no-verify|core\.hookspath", re.IGNORECASE)
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

RETARGET_REASON = (
    "run gh pr create by itself from the worktree of the branch; "
    "put a long body in --body-file"
)
SKIP_REASON = "git hooks must run here; do not skip them"


def _receipt_reason(head: str) -> str:
    return (
        f"Decoy quality gate not satisfied for HEAD {head[:8]}. Before opening a PR: "
        "(1) dispatch the 'dennis' agent to review this change (he runs /code-review, "
        "/security-review and /qa and returns a verdict) and resolve every BLOCKER and "
        "HIGH; (2) dispatch the 'barry' agent to sync the docs to the change; "
        "(3) record the pass: devloop-gate-pass. Then re-run gh pr create. "
        "Committing more after the gate re-arms it, by design."
    )


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
    print(reason, file=sys.stderr)
    return 2


def _git(cwd, *args):
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _configured(cwd) -> bool:
    """True when cwd is in a repo (or a linked worktree of one) that a project config lists."""
    # The package comes from the installed lib that holds this file, not from the cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from devloop import project, store

    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return False
    root = store.state_root()
    names = sorted(d.name for d in root.iterdir()) if root.is_dir() else []
    mine = Path(common).parent.resolve()
    for name in names:
        if (
            store.NAME.fullmatch(name) is None
            or not (root / name / "project.json").is_file()
        ):
            continue
        try:
            cfg = project.load_project(name)
        except project.ConfigError:
            continue
        if any(Path(r["path"]).resolve() == mine for r in cfg["repos"]):
            return True
    return False


def _receipt_ok(cwd):
    """(True, "") when the receipt holds exactly HEAD, else (False, the denial text)."""
    head = _git(cwd, "rev-parse", "HEAD") or ""
    git_dir = _git(cwd, "rev-parse", "--path-format=absolute", "--git-dir")
    text = ""
    if git_dir:
        try:
            text = (Path(git_dir) / "decoy-quality-gate").read_text(encoding="utf-8")
        except FileNotFoundError:
            pass  # no receipt yet; any other read error propagates and fails closed
    receipt = text[:-1] if text.endswith("\n") else text
    if head and _OID.fullmatch(head) and receipt == head:
        return True, ""
    return False, _receipt_reason(head)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
        if event.get("tool_name") != "Bash" or not isinstance(command, str):
            return 0
    except Exception:  # noqa: BLE001 - not a Bash event we can read: never break the session
        return 0
    create = _CREATE.search(command) is not None
    skip = _SKIP_HOOKS.search(command) is not None
    if not (create or skip):
        return 0
    cwd = event.get("cwd") or os.getcwd()
    try:
        if create and _RETARGET.search(command):
            return _deny(RETARGET_REASON)
        if not _configured(cwd):
            return 0
        if create:
            ok, reason = _receipt_ok(cwd)
            if not ok:
                return _deny(reason)
        if skip:
            return _deny(SKIP_REASON)
    except Exception:  # noqa: BLE001 - fail closed on a command this hook exists to stop
        return _deny("pr gate could not check this command; run devloop-install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
