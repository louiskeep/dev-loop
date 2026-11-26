"""Merge on the exact gated head, and nothing else.

`gh pr merge` is checked against the PR as GitHub reports it, not against the local
checkout, and the result is confirmed from GitHub before any state or board change.
"""

import collections
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import board, evidence, notes, slices, store
from . import project as projmod
from .scrub import scrub

MergeResult = collections.namedtuple("MergeResult", "outcome reason")
OK_STATES = ("SUCCESS", "SKIPPED", "NEUTRAL")
RUN_ID = re.compile(r"/runs/(\d+)")


def _result(outcome, reason):
    return MergeResult(outcome, reason)


def _slug(repo_cfg):
    return repo_cfg["slug"]


def _gh(repo_cfg, *args, timeout=120):
    """Run gh in the repo checkout. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["gh", *args],
            cwd=repo_cfg["path"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 255, "", "gh did not run"
    return r.returncode, r.stdout, r.stderr


def _view(repo_cfg, pr, fields):
    rc, out, _ = _gh(
        repo_cfg, "pr", "view", str(pr), "-R", _slug(repo_cfg), "--json", fields
    )
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _success(conclusion):
    """gh run view exports lowercase `success`; the status rollup uses `SUCCESS`."""
    return isinstance(conclusion, str) and conclusion.lower() == "success"


def _skipped(conclusion):
    """Real gate runs skip some jobs (a fast path, a proof job); a skip is not a failure."""
    return isinstance(conclusion, str) and conclusion.lower() == "skipped"


def _same_head(oid, head):
    return (
        isinstance(oid, str)
        and bool(head)
        and (oid == head or (len(head) >= 7 and oid.startswith(head)))
    )


def repo_settings(cfg, repo_name):
    repo_cfg = next(r for r in cfg["repos"] if r["name"] == repo_name)
    rules = (cfg.get("merge") or {}).get("repos", {}).get(repo_name, {})
    return repo_cfg, rules, rules.get("base") or repo_cfg.get("base", "main")


def _source_of(entry):
    """Names that identify where a check came from: workflow, status context or app."""
    app = entry.get("app") if isinstance(entry.get("app"), dict) else {}
    return {
        v
        for v in (entry.get("workflowName"), entry.get("context"), app.get("slug"))
        if v
    }


def _check_states(rollup, rules):
    """Blocking checks. A required check passes only when it is present and every one
    of its entries (from the pinned source, when one is set) is SUCCESS. Every other
    entry, whatever its name, that is failed or pending refuses."""
    by_name = {}
    for entry in rollup if isinstance(rollup, list) else []:
        name = entry.get("name") or entry.get("context")
        if name:
            by_name.setdefault(name, []).append(entry)

    def state(entry):
        return str(entry.get("conclusion") or entry.get("state") or "").upper()

    required = rules.get("required_checks", [])
    pins = rules.get("required_check_sources", {})
    problems = []
    satisfying = set()  # ids of the entries that stand for a required check
    for name in required:
        entries = by_name.get(name, [])
        if name in pins:
            entries = [e for e in entries if pins[name] in _source_of(e)]
        satisfying.update(id(e) for e in entries)
        if not entries or any(state(e) != "SUCCESS" for e in entries):
            where = f" from {pins[name]}" if name in pins else ""
            problems.append(f"required check {name}{where} is not SUCCESS")
    # A pin only chooses which entry satisfies the requirement. Every other entry,
    # including one with the same name from another workflow, must not be red or pending.
    for name, entries in by_name.items():
        for e in entries:
            if id(e) not in satisfying and state(e) not in OK_STATES:
                problems.append(f"check {name} is {state(e) or 'pending'}")
                break
    return problems


def _git_ok(repo_cfg, *args, timeout=300):
    return (
        subprocess.run(
            ["git", "-C", repo_cfg["path"], *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        ).returncode
        == 0
    )


def _up_to_date(repo_cfg, base, head):
    """The head must contain the current base tip. Returns a refusal reason or None."""
    if not _git_ok(repo_cfg, "fetch", "origin", base):
        return "could not fetch the base branch to check the PR is up to date"
    if not _git_ok(repo_cfg, "merge-base", "--is-ancestor", f"origin/{base}", head):
        return "the PR head does not contain the latest base branch"
    return None


def _verify_cmd(project, cfg, repo_cfg, rules, base, head):
    """Run the repo's own required-checks verifier from a fresh checkout of the base."""
    cmd = rules.get("verify_cmd")
    if not cmd:
        return None
    fetch = subprocess.run(
        ["git", "-C", repo_cfg["path"], "fetch", "origin", base],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
        check=False,
    )
    if fetch.returncode != 0:
        return "could not fetch the base branch for the verifier"
    tmp = Path(tempfile.mkdtemp(prefix="verify-", dir=store.state_dir(project)))
    tree = tmp / "tree"
    made = subprocess.run(
        [
            "git",
            "-C",
            repo_cfg["path"],
            "worktree",
            "add",
            "--detach",
            str(tree),
            f"origin/{base}",
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if made.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return "could not check out the base branch for the verifier"
    env = None
    env_file = (cfg.get("notify") or {}).get("env_file")
    if env_file:
        env = {**os.environ, **notes.read_env_file(env_file)}
    try:
        r = subprocess.run(
            [a.replace("{head}", head) for a in cmd],
            cwd=tree,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=600,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "the required-checks verifier did not run"
    finally:
        subprocess.run(
            ["git", "-C", repo_cfg["path"], "worktree", "remove", "--force", str(tree)],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        shutil.rmtree(tmp, ignore_errors=True)
    return None if r.returncode == 0 else "the required-checks verifier failed"


def _iso(text):
    try:
        at = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=datetime.timezone.utc)


def _workflow_matches(repo_cfg, wf, run_id, run):
    """The configured workflow is a display name or a file name.

    `gh run view` has no path field, so a file name is resolved through the Actions API.
    """
    if wf == run.get("workflowName"):
        return True
    if not wf.endswith((".yml", ".yaml")):
        return False
    rc, out, _ = _gh(
        repo_cfg,
        "api",
        f"repos/{_slug(repo_cfg)}/actions/runs/{run_id}",
        "--jq",
        ".path",
    )
    return rc == 0 and Path(out.strip()).name == wf


def _gate_dispatch(project, repo_cfg, rules, rollup, s, cfg):
    """The gate status must point at a green workflow_dispatch run for this head,
    created after the final cross-model GO."""
    wf, status = rules.get("dispatch_workflow"), rules.get("gate_status")
    if not wf and not status:
        return None
    if not (wf and status):
        return "dispatch_workflow and gate_status are required together"
    url = next(
        (
            e.get("targetUrl") or e.get("detailsUrl") or ""
            for e in rollup
            if (e.get("name") or e.get("context")) == status
        ),
        "",
    )
    m = RUN_ID.search(url)
    if not m:
        return f"status {status} does not point at a workflow run"
    rc, out, _ = _gh(
        repo_cfg,
        "run",
        "view",
        m.group(1),
        "-R",
        _slug(repo_cfg),
        "--json",
        "event,headSha,workflowName,createdAt,conclusion,jobs",
    )
    try:
        run = json.loads(out) if rc == 0 else {}
    except ValueError:
        run = {}
    roster = evidence.code_roster(project, cfg, s)
    if not roster:  # config load refuses this; never an IndexError mid-merge
        return "the gate workflow needs a code reviewer for this risk tier"
    last = roster[-1][0]
    final_go = ((s["evidence"].get(s["head"]) or {}).get(f"code:{last}") or {}).get(
        "at"
    )
    created, final_at = _iso(run.get("createdAt")), _iso(final_go)
    bad = (
        not _workflow_matches(repo_cfg, wf, m.group(1), run)
        or run.get("event") != "workflow_dispatch"
        or not _same_head(run.get("headSha"), s["head"])
        or not _success(run.get("conclusion"))
        or not run.get("jobs")
        or not any(_success(j.get("conclusion")) for j in run["jobs"])
        or any(
            not (_success(j.get("conclusion")) or _skipped(j.get("conclusion")))
            for j in run["jobs"]
        )
        or created is None
        or final_at is None
        or created <= final_at
    )
    if bad:
        return "the gate workflow run is not a green dispatch after the final review"
    return None


def _preflight(project, cfg, s, view):
    """Every check before the merge call. Returns a refusal reason or None."""
    repo_cfg, rules, base = repo_settings(cfg, s["repo"])
    if view is None:
        return "GitHub did not report the PR"
    if view.get("baseRefName") != base:
        return f"the PR does not target {base}"
    if not _same_head(view.get("headRefOid"), s["head"]):
        return "the PR head is not the reviewed head"
    rollup = view.get("statusCheckRollup") or []
    problems = _check_states(rollup, rules)
    if problems:
        return "; ".join(problems)
    why = _gate_dispatch(project, repo_cfg, rules, rollup, s, cfg)
    if why:
        return why
    why = _verify_cmd(project, cfg, repo_cfg, rules, base, s["head"])
    if why:
        return why
    if (cfg.get("merge") or {}).get("require_up_to_date"):
        if view.get("mergeStateStatus") not in ("CLEAN", "HAS_HOOKS"):
            return "the PR is not up to date with its base"
        # GitHub reports BEHIND only under strict branch protection, so check it here.
        why = _up_to_date(repo_cfg, base, s["head"])
        if why:
            return why
    glob = rules.get("changelog_glob")
    if glob and not any(
        fnmatch.fnmatch(f.get("path", ""), glob) for f in view.get("files") or []
    ):
        return "no changelog entry in the PR"
    return None


def confirm_pr(cfg, repo_name, pr, base, head):
    """merged | open | closed | mismatch | unknown, from GitHub.

    closed is a CLOSED PR. mismatch is a MERGED PR that went to another base or at
    another head, which is not the merge this slice asked for.
    """
    repo_cfg, _, _ = repo_settings(cfg, repo_name)
    view = _view(repo_cfg, pr, "state,headRefOid,baseRefName")
    if view is None:
        return "unknown"
    state = view.get("state")
    if state == "MERGED":
        if view.get("baseRefName") == base and _same_head(view.get("headRefOid"), head):
            return "merged"
        return "mismatch"
    return {"OPEN": "open", "CLOSED": "closed"}.get(state, "unknown")


_DEAD_REASONS = {
    "closed": "the PR is closed, not merged; reopen it or make a new PR",
    "mismatch": "the PR merged into another base or at another head",
}


def _dead(project, key, state):
    """A final answer from GitHub that is not our merge: clear the flags and refuse."""
    slices.set_merge_state(project, key, attempt=None, unknown=False)
    notes.problem(
        project,
        "merge-pr-dead",
        impact="the slice's PR cannot be merged as it stands",
        fix="read the PR on GitHub, then fix the slice's PR or head",
    )
    store.append_event(project, "merge-pr-dead", key=key, state=state)
    return _result("refused", _DEAD_REASONS[state])


def _finish(project, cfg, key, info):
    """Advance and report the merge GitHub confirmed, which is `info`'s PR and head."""
    s = slices.load(project, key)
    ident = {
        "repo": info["repo"],
        "slug": _slug(repo_settings(cfg, info["repo"])[0]),
        "pr": info["pr"],
        "head": info["head"],
    }
    if s["phase"] == "GATE":
        try:
            slices.finish_merge(project, key, ident=ident)
        except slices.TransitionError as exc:
            return _result("refused", str(exc))
    slices.deliver_board(project, ident)
    store.append_event(project, "merged", key=key, row=s["row"], slice=s["slice"])
    return _result("merged", "merged")


def _unknown(project, key):
    # The attempt stays so the next call reconciles the same PR and head.
    slices.set_merge_state(project, key, unknown=True)
    notes.problem(
        project,
        "merge-state-unknown",
        impact="a merge may or may not have happened; the slice waits at GATE",
        fix="read the PR on GitHub, then run the merge command again",
    )
    return _result("unknown", "GitHub did not report the merge state")


def merge(project, key) -> MergeResult:
    if os.environ.get("DEVLOOP_RUN"):  # set for every worker the loop starts
        return _result("refused", "only the orchestrator merges")
    cfg = projmod.load_project(project)
    s = slices.load(project, key)
    for ident in slices.pending_board(project, key):
        # A crash after the move but before the board call: deliver it once.
        ident["repo"] = s["repo"]
        slices.deliver_board(project, ident)
    attempt = s["merge_attempt"]
    if attempt or s["merge_unknown"]:
        # Reconcile first: a crash after the merge call must finish the move once.
        info = attempt or {
            "repo": s["repo"],
            "pr": s["pr"],
            "base": repo_settings(cfg, s["repo"])[2],
            "head": s["head"],
        }
        state = confirm_pr(cfg, info["repo"], info["pr"], info["base"], info["head"])
        if state == "merged":
            return _finish(project, cfg, key, info)
        if state == "unknown":
            return _result("unknown", "merge state is still unknown; no retry")
        if state in _DEAD_REASONS:
            return _dead(project, key, state)
        slices.set_merge_state(project, key, attempt=None, unknown=False)
        if s["merge_unknown"]:
            return _result("refused", "state read OPEN; the flag is clear, call again")
        s = slices.load(project, key)
    if s["phase"] != "GATE":
        return _result("refused", f"slice is in {s['phase']}, not GATE")
    if not (cfg.get("merge") or {}).get("allowed"):
        return _result("refused", "merging is not allowed for this project")
    if s["pr"] is None or not s["head"]:
        return _result("refused", "slice has no PR or head")
    problems = slices.validate_gate(project, key)
    if problems:
        return _result("refused", "; ".join(problems))
    repo_cfg, _, base = repo_settings(cfg, s["repo"])
    view = _view(
        repo_cfg,
        s["pr"],
        "baseRefName,headRefOid,statusCheckRollup,mergeStateStatus,files",
    )
    why = _preflight(project, cfg, s, view)
    if why:
        return _result("refused", why)
    # Recorded before the call so a crash right after it can be reconciled.
    slices.set_merge_state(
        project,
        key,
        attempt={"repo": s["repo"], "pr": s["pr"], "base": base, "head": s["head"]},
    )
    notes.notify(project, f"Merging {s['row']} {s['slice']} (PR {s['pr']}).")
    _, _, err = _gh(
        repo_cfg,
        "pr",
        "merge",
        str(s["pr"]),
        "-R",
        _slug(repo_cfg),
        "--squash",
        "--match-head-commit",
        s["head"],
    )
    state = confirm_pr(cfg, s["repo"], s["pr"], base, s["head"])
    if state == "merged":
        return _finish(
            project, cfg, key, {"repo": s["repo"], "pr": s["pr"], "head": s["head"]}
        )
    if state in _DEAD_REASONS:
        return _dead(project, key, state)
    if state == "open":
        slices.set_merge_state(project, key, attempt=None)
        return _result(
            "refused", f"not merged: {scrub(err) or 'GitHub kept the PR open'}"
        )
    return _unknown(project, key)


def record_direct(project, key, pr, head) -> MergeResult:
    """Ledger entry for a PR merged by hand, once GitHub confirms it merged at `head`.

    Uses the same writer as `merge`, so the dashboard counts it. The slice is not moved.
    """
    cfg = projmod.load_project(project)
    s = slices.load(project, key)
    if s["pr"] != pr or not _same_head(s["head"], head):
        return _result("refused", "that PR and head are not this slice's PR and head")
    repo_cfg, _, base = repo_settings(cfg, s["repo"])
    state = confirm_pr(cfg, s["repo"], pr, base, head)
    if state != "merged":
        return _result(
            "refused", f"GitHub does not show PR {pr} merged at that head: {state}"
        )
    view = _view(repo_cfg, pr, "headRefOid,headRefName") or {}
    full = view.get("headRefOid")
    if not _same_head(full, head) or (
        s["branch"] and view.get("headRefName") != s["branch"]
    ):
        return _result("refused", "GitHub's PR head or branch is not this slice's")
    # The full SHA from GitHub keeps one ledger key however the head was typed.
    board.record_merge(project, {"slug": _slug(repo_cfg), "pr": pr, "head": full}, s)
    store.append_event(project, "merged", key=key, row=s["row"], slice=s["slice"])
    return _result("merged", "recorded")
