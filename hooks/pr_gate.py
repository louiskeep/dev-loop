"""PreToolUse hook: `gh pr create` needs a quality-gate receipt for the exact HEAD.

Substring checks on the raw command text; the text is never split or parsed, so a quote,
a comment or a wrapper (`bash -lc`, `timeout`) cannot hide the words. Pushes are not
inspected here: git's pre-push (devloop/githooks.py) sees the real refs.

Covered: `gh pr create` or `gh pr new`, run with another command in the same call (a branch
switch before it would change HEAD after the receipt check), moved to another target with `cd`, `-C`, `--repo`
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
# Another command in the same call: a separator, a newline, a substitution. A redirect to
# a stream (`2>&1`) is cut out first. A quoted `;` in a title is a false positive.
_REDIRECT = re.compile(r"\d*>&\d*")
_COMPOUND = re.compile(r"[;&|\r\n`]|\$\(")

RETARGET_REASON = (
    "run gh pr create by itself from the worktree of the branch; "
    "put a long body in --body-file"
)
STANDALONE_REASON = (
    "run gh pr create by itself, not with && ; | or a newline: an earlier command "
    "could switch the branch after this check"
)
UNREADABLE_REASON = (
    "a project config cannot be read, so the guarded repos are unknown; "
    "fix project.json, then run devloop-install"
)
SKIP_REASON = "git hooks must run here; do not skip them"


def _receipt_reason(head: str) -> str:
    return (
        f"Quality gate not satisfied for HEAD {head[:8]}. Before opening a PR: "
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


def _configured(cwd):
    """(gate, unreadable): the push gate names of the project that lists cwd's repo (or
    a linked worktree of it), or None when none does; and whether any project config
    could not be read."""
    # The package comes from the installed lib that holds this file, not from the cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from devloop import project

    loaded = project.load_all()
    repos, unreadable = project.configured_repos(loaded)
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None, unreadable
    mine = Path(common).parent.resolve()
    if not any(r.path == mine for r in repos):
        return None, unreadable
    return project.gate_for(repos, mine), unreadable


def _receipt_ok(cwd, receipt):
    """(True, "") when the receipt holds exactly HEAD, else (False, the denial text)."""
    head = _git(cwd, "rev-parse", "HEAD") or ""
    git_dir = _git(cwd, "rev-parse", "--path-format=absolute", "--git-dir")
    text = ""
    if git_dir:
        try:
            text = (Path(git_dir) / receipt).read_text(encoding="utf-8")
        except FileNotFoundError:
            pass  # no receipt yet; any other read error propagates and fails closed
    receipt = text[:-1] if text.endswith("\n") else text
    from devloop import githooks  # the one object-id validator; see _configured

    if githooks.is_object_id(head) and receipt == head:
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
        if create and _COMPOUND.search(_REDIRECT.sub("", command)):
            return _deny(STANDALONE_REASON)
        gate, unreadable = _configured(cwd)
        # An unreadable config hides which repos are guarded: a create or a skip word is
        # denied everywhere (plan B2 rule 5).
        if unreadable:
            return _deny(UNREADABLE_REASON)
        if gate is None:
            return 0
        if create:
            ok, reason = _receipt_ok(cwd, gate["receipt"])
            if not ok:
                return _deny(reason)
        if skip:
            return _deny(SKIP_REASON)
    except Exception:  # noqa: BLE001 - fail closed on a command this hook exists to stop
        return _deny("pr gate could not check this command; run devloop-install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
