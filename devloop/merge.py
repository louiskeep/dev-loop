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

from . import notes, slices, store
from . import project as projmod
from .scrub import scrub

MergeResult = collections.namedtuple("MergeResult", "outcome reason")
OK_STATES = ("SUCCESS", "SKIPPED", "NEUTRAL")
RUN_ID = re.compile(r"/runs/(\d+)")


def _result(outcome, reason):
    return MergeResult(outcome, reason)


def _slug(repo_cfg):
    return repo_cfg.get("slug") or repo_cfg["name"]


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


def _same_head(oid, head):
    return (
        isinstance(oid, str)
        and bool(head)
        and (oid == head or (len(head) >= 7 and oid.startswith(head)))
    )


def _repo_settings(cfg, repo_name):
    repo_cfg = next(r for r in cfg["repos"] if r["name"] == repo_name)
    rules = (cfg.get("merge") or {}).get("repos", {}).get(repo_name, {})
    return repo_cfg, rules, rules.get("base") or repo_cfg.get("base", "main")


def _check_states(rollup, rules):
    """Blocking checks: a required one that is not SUCCESS, or any other failed or pending."""
    seen = {}
    for entry in rollup if isinstance(rollup, list) else []:
        name = entry.get("name") or entry.get("context")
        state = str(entry.get("conclusion") or entry.get("state") or "").upper()
        if name:
            seen[name] = state
    required = rules.get("required_checks", [])
    problems = [
        f"required check {name} is not SUCCESS"
        for name in required
        if seen.get(name) != "SUCCESS"
    ]
    problems += [
        f"check {name} is {state or 'pending'}"
        for name, state in seen.items()
        if state not in OK_STATES and name not in required
    ]
    return problems


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
        env = {**os.environ, **notes._env_file(env_file)}
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


def _gate_dispatch(repo_cfg, rules, rollup, s, cfg):
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
    last = projmod.reviewer_entry(cfg["reviews"][s["risk"]]["code"][-1])[0]
    final_go = ((s["evidence"].get(s["head"]) or {}).get(f"code:{last}") or {}).get(
        "at"
    )
    created, final_at = _iso(run.get("createdAt")), _iso(final_go)
    bad = (
        run.get("workflowName") != wf
        or run.get("event") != "workflow_dispatch"
        or not _same_head(run.get("headSha"), s["head"])
        or run.get("conclusion") != "SUCCESS"
        or not run.get("jobs")
        or any(j.get("conclusion") != "SUCCESS" for j in run["jobs"])
        or created is None
        or final_at is None
        or created <= final_at
    )
    if bad:
        return "the gate workflow run is not a green dispatch after the final review"
    return None


def _preflight(project, cfg, s, view):
    """Every check before the merge call. Returns a refusal reason or None."""
    repo_cfg, rules, base = _repo_settings(cfg, s["repo"])
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
    why = _gate_dispatch(repo_cfg, rules, rollup, s, cfg)
    if why:
        return why
    why = _verify_cmd(project, cfg, repo_cfg, rules, base, s["head"])
    if why:
        return why
    if (cfg.get("merge") or {}).get("require_up_to_date") and view.get(
        "mergeStateStatus"
    ) not in ("CLEAN", "HAS_HOOKS"):
        return "the PR is not up to date with its base"
    glob = rules.get("changelog_glob")
    if glob and not any(
        fnmatch.fnmatch(f.get("path", ""), glob) for f in view.get("files") or []
    ):
        return "no changelog entry in the PR"
    return None


def _confirm(cfg, repo_name, pr, base, head):
    """merged | open | unknown, from GitHub."""
    repo_cfg, _, _ = _repo_settings(cfg, repo_name)
    view = _view(repo_cfg, pr, "state,headRefOid,baseRefName")
    if view is None:
        return "unknown"
    if (
        view.get("state") == "MERGED"
        and view.get("baseRefName") == base
        and _same_head(view.get("headRefOid"), head)
    ):
        return "merged"
    return "open" if view.get("state") == "OPEN" else "unknown"


def _finish(project, key, s):
    if s["phase"] == "GATE":
        slices.finish_merge(project, key)
    slices.board_merged(project, key, s["pr"])
    slices.set_merge_state(project, key, attempt=None, unknown=False)
    store.append_event(project, "merged", key=key, row=s["row"], slice=s["slice"])
    return _result("merged", "merged")


def _unknown(project, key):
    slices.set_merge_state(project, key, attempt=None, unknown=True)
    notes.problem(
        project,
        "merge-state-unknown",
        impact="a merge may or may not have happened; the slice waits at GATE",
        fix="read the PR on GitHub, then run the merge command again",
    )
    return _result("unknown", "GitHub did not report the merge state")


def merge(project, key) -> MergeResult:
    cfg = projmod.load_project(project)
    s = slices.load(project, key)
    attempt = s["merge_attempt"]
    if attempt or s["merge_unknown"]:
        # Reconcile first: a crash after the merge call must finish the move once.
        info = attempt or {
            "repo": s["repo"],
            "pr": s["pr"],
            "base": _repo_settings(cfg, s["repo"])[2],
            "head": s["head"],
        }
        state = _confirm(cfg, info["repo"], info["pr"], info["base"], info["head"])
        if state == "merged":
            return _finish(project, key, s)
        if state == "unknown":
            return _result("unknown", "merge state is still unknown; no retry")
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
    repo_cfg, _, base = _repo_settings(cfg, s["repo"])
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
    state = _confirm(cfg, s["repo"], s["pr"], base, s["head"])
    if state == "merged":
        return _finish(project, key, s)
    if state == "open":
        slices.set_merge_state(project, key, attempt=None)
        return _result(
            "refused", f"not merged: {scrub(err) or 'GitHub kept the PR open'}"
        )
    return _unknown(project, key)
