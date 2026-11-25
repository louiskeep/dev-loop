import copy
import itertools
import json
import os
import shutil
from pathlib import Path

import pytest


def _minimal_project(name="p"):
    return {
        "name": name,
        "intent": "ship the thing",
        "roadmap": {"path": "ROADMAP.md", "section": "5", "row_id": "ROW"},
        "repos": [{"name": "main", "path": "/tmp/devloop-test-repo"}],
        "rules_dir": "/tmp/devloop-test-rules",
        "capacity": {"builder": 8, "tester": 4, "reviewer": 2, "planner": 3},
        "models": {
            "tick": "opus",
            "planner": "opus",
            "builder": "sonnet",
            "reviewer": "opus",
        },
        "reviews": {
            "R0": {"plan": [], "code": []},
            "R1": {"plan": ["codex"], "code": ["codex"]},
            "R2": {
                "plan": [{"name": "dennis", "cap": 2}, "codex"],
                "code": ["dennis", "codex"],
            },
        },
        "reviewers": {
            "dennis": {"kind": "dennis", "kinds": ["dennis"]},
            "codex": {"kind": "codex-review", "kinds": ["codex-review", "codex-fix"]},
        },
    }


@pytest.fixture
def minimal_project():
    return lambda name="p": copy.deepcopy(_minimal_project(name))


@pytest.fixture
def write_project():
    def write(home, name, cfg):
        d = Path(home) / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(json.dumps(cfg))

    return write


@pytest.fixture
def fakebin(tmp_path, monkeypatch):
    """The shared fake tools (gh, ...) first on PATH, in a per-test copy."""
    d = tmp_path / "fakebin"
    shutil.copytree(Path(__file__).parent / "fakebin", d)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("DEVLOOP_HOME", str(h))
    monkeypatch.delenv("DEVLOOP_HEADLESS", raising=False)
    return h


@pytest.fixture
def proj(home, tmp_path, minimal_project, write_project):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = minimal_project()
    cfg["repos"][0]["path"] = str(repo)
    write_project(home, "p", cfg)
    return "p"


@pytest.fixture
def cfg_edit(home):
    """Edit the loaded project.json in place: cfg_edit(lambda c: c.update(...))."""

    def edit(mutate):
        path = home / "p" / "project.json"
        cfg = json.loads(path.read_text())
        mutate(cfg)
        path.write_text(json.dumps(cfg))

    return edit


@pytest.fixture
def mk_slice(proj):
    """Build a slice directly in any phase, bypassing the transition rules."""
    from devloop import slices

    counter = itertools.count(1)

    def make(project, phase="FRAME", **fields):
        s = slices.new_slice(project, f"ROW{next(counter)}", "S1", repo="main")
        s["phase"] = phase
        s.update(fields)
        s["was_r3"] = s["was_r3"] or s["risk"] == "R3"
        slices._save(project, s)
        return s["key"]

    return make


@pytest.fixture
def raw():
    from devloop import slices

    return lambda project, key: slices.slice_path(project, key).read_bytes()


@pytest.fixture
def mk_run(home):
    """Write a minimal applied run dir (request.json, result.md, exit, applied)."""
    from devloop import store

    def make(
        project,
        run_id,
        *,
        kind,
        key,
        head=None,
        plan_sha=None,
        reviewer=None,
        purpose=None,
        level=None,
        predecessor_run=None,
        verdict="GO",
        status="DONE",
        covers=None,
        applied=True,
        result_head=None,
    ):
        d = store.run_dir(project, run_id)
        d.mkdir(parents=True)
        row, slice_id = key.split("--", 1)
        store.write_json(
            d / "request.json",
            {
                "id": run_id,
                "kind": kind,
                "row": row,
                "slice": slice_id,
                "head": head,
                "plan_sha": plan_sha,
                "reviewer": reviewer,
                "purpose": purpose,
                "level": level,
                "predecessor_run": predecessor_run,
                "covers": dict(covers or {}),
            },
        )
        (d / "result.md").write_text(
            f"status: {status}\nhead: {result_head or head or ''}\n"
            f"verdict: {verdict}\n---\nbody\n"
        )
        store.write_json(d / "exit", {"code": 0, "reason": "exit", "ended_at": "t"})
        if applied:
            (d / "applied").write_text("")
        return d

    return make


@pytest.fixture
def give(mk_run):
    """Make a run dir and record its evidence, as apply_run will. Returns the run id."""
    from devloop import slices

    kinds = {"dennis": "dennis", "codex": "codex-review"}

    def go(
        project,
        key,
        kind,
        run,
        *,
        verdict="GO",
        pred=None,
        head=None,
        sha=None,
        run_kind=None,
    ):
        s = slices.load(project, key)
        if kind in ("t1", "t2", "full"):
            h = head or s["head"]
            mk_run(
                project, run, kind="test", key=key, head=h, level=kind, verdict=verdict
            )
            slices.record_evidence(project, key, kind, verdict, run, head=h)
        elif kind.startswith("code:"):
            name = kind.split(":", 1)[1]
            h = head or s["head"]
            mk_run(
                project,
                run,
                kind=run_kind or kinds[name],
                key=key,
                head=h,
                reviewer=name,
                purpose="code",
                predecessor_run=pred,
                verdict=verdict,
            )
            slices.record_evidence(project, key, kind, verdict, run, head=h)
        else:
            name = kind.split(":", 1)[1]
            sh = sha or slices.plan_sha(s)
            mk_run(
                project,
                run,
                kind=run_kind or kinds[name],
                key=key,
                plan_sha=sh,
                reviewer=name,
                purpose="plan",
                predecessor_run=pred,
                verdict=verdict,
            )
            slices.record_evidence(project, key, kind, verdict, run, plan_sha=sh)
        return run

    return go
