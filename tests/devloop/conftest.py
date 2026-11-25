import copy
import json
import os
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
    """A directory first on PATH for fake `gh`, board and notify scripts."""
    d = tmp_path / "fakebin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d
