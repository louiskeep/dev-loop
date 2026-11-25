import itertools
import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from devloop import runs, store, wrap

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "plan.md").write_text("Rules consulted: x\n")
    (path / "f.txt").write_text("one\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "c1")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, fakebin, home):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(64 * 1024 * 1024))
    bins = tmp_path / "bins"
    bins.mkdir()
    wrap = bins / "devloop-wrap"
    wrap.write_text("#!/bin/sh\nsleep 30\n")
    wrap.chmod(0o755)
    monkeypatch.setenv("DEVLOOP_BIN_DIR", str(bins))
    monkeypatch.setenv("FAKE_FIX_CACHE", str(tmp_path / "fixcache"))
    yield
    for req in home.glob("*/runs/*/request.json"):
        pid = json.loads(req.read_text()).get("pid")
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def config(proj, cfg_edit, tmp_path):
    def edit(c):
        c["rules_dir"] = str(tmp_path / "rules")
        c["reviewers"]["codex"]["argv"] = {
            "codex-review": ["codex-review", "{worktree}", "{brief}", "{out}"],
            "codex-fix": ["codex-fix", "{run_id}", "{brief}", "{plan}"],
            # The shipped example's own argv, so the real shape is exercised.
            "codex-builder": json.loads(
                (
                    Path(__file__).resolve().parents[2]
                    / "skills/dev-loop/project.example.json"
                ).read_text()
            )["reviewers"]["codex"]["argv"]["codex-builder"],
        }
        c["reviewers"]["codex"]["kinds"] = [
            "codex-review",
            "codex-fix",
            "codex-builder",
        ]
        c["reviewers"]["codex"]["repair_out"] = (
            str(tmp_path / "fixcache") + "/{run_id}/{plan_name}"
        )
        c["tests"] = {
            "t1": {"argv": ["vm-run", "{worktree}"], "infra_codes": [124, 255, 75]},
            "full": {"argv": ["vm-run", "full"], "infra_codes": [124, 255, 75]},
        }
        c["brief_extra"] = "Standing: keep commits short."
        c["nodes"] = {"directory": "NODEDIR-MARKER"}

    cfg_edit(edit)


@pytest.fixture
def brief(tmp_path):
    p = tmp_path / "brief.md"
    p.write_text("BODY-MARKER do the task")
    return p


@pytest.fixture
def ready(proj, mk_slice, tmp_path):
    n = itertools.count()

    def make(phase="DEVELOP", risk="R1", worktree=None, **fields):
        if worktree is None:
            worktree = tmp_path / f"wt{next(n)}"
            head = make_repo(worktree)
        else:
            head = git(worktree, "rev-parse", "HEAD")
        fields.setdefault("plan_path", "plan.md")
        key = mk_slice(
            proj,
            phase=phase,
            risk=risk,
            rulebooks=["a.md"],
            worktree=str(worktree),
            branch="b",
            head=head,
            **fields,
        )
        return key, Path(worktree), head

    return make


def finish(proj, run_id, *, code=0, reason="exit", result=None):
    """Stand in for the wrapper: write result.md and the exit file."""
    rd = store.run_dir(proj, run_id)
    if result is not None:
        store.out_dir(rd).mkdir(exist_ok=True)
        (store.out_dir(rd) / "result.md").write_text(result)
    assert runs.create_exit(rd, code=code, reason=reason)
    req = runs.read_request(proj, run_id)
    if req["kind"] == "codex-fix":
        wrap.write_repair(rd, req)


def res(status="DONE", verdict="", head=""):
    return f"status: {status}\nhead: {head}\nverdict: {verdict}\n---\nbody\n"


def go(proj, brief, kind, key, **kw):
    return runs.dispatch(proj, kind, key, brief=brief, **kw)


def dead_run(proj, key, **extra):
    rd = store.run_dir(proj, "builder.dead")
    rd.mkdir(parents=True)
    req = {
        "id": "builder.dead",
        "kind": "builder",
        "row": key.split("--")[0],
        "slice": key.split("--")[1],
        "key": key,
        "pid": 2**22 + 5,
        "pid_start": "1",
        "boot_id": runs.boot_id(),
        "reader": "claude",
        **extra,
    }
    store.write_json(rd / "request.json", req)
    return rd


def repair(proj, ready, brief, *, plant=False):
    key, wt, _ = ready("PLAN", risk="R2")
    d = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, d, result=res("DONE", "NO-GO"))
    runs.apply_run(proj, d)
    d2 = go(proj, brief, "dennis", key, reviewer="dennis")
    finish(proj, d2, result=res("DONE", "NO-GO"))
    runs.apply_run(proj, d2)
    r = go(proj, brief, "codex-fix", key, reviewer="codex")
    req = runs.read_request(proj, r)
    out = Path(req["repair_out"])
    out.parent.mkdir(parents=True)
    out.write_text("Rules consulted: repaired\n")
    if plant:
        planted = store.run_dir(proj, r) / "repair.json"
        planted.write_text(json.dumps({"input_sha": "f" * 64, "output_sha": "f" * 64}))
    return key, wt, r


def train(proj, ready, brief):
    key, wt, h1 = ready("REVIEW")
    (wt / "f.txt").write_text("two\n")
    git(wt, "commit", "-qam", "c2")
    top, _, h2 = ready("VERIFY", worktree=wt)
    return key, h1, top, h2


def events(home, kind):
    path = home / "p" / "events.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if f'"{kind}"' in x]
