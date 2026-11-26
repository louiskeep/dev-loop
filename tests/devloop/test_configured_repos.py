"""One reader of the project configs, and a sentry that keeps it the only one."""

import json
from pathlib import Path

from devloop import project

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = [*ROOT.glob("devloop/**/*.py"), *ROOT.glob("hooks/*.py"), *ROOT.glob("bin/*")]
OWNERS = {"devloop/project.py", "devloop/store.py"}


def _cfg(name, path):
    return {
        "name": name,
        "intent": "ship",
        "roadmap": {"path": "R.md", "section": "5", "row_id": "ROW"},
        "repos": [{"name": "r", "path": str(path), "slug": "o/r"}],
        "rules_dir": "/tmp/x",
        "capacity": {"builder": 1, "tester": 1, "reviewer": 1, "planner": 1},
        "models": {"tick": "m", "planner": "m", "builder": "m", "reviewer": "m"},
        "reviews": {t: {"plan": [], "code": []} for t in ("R0", "R1", "R2")},
        "reviewers": {},
    }


def test_configured_repos_lists_repos_and_names_unreadable_projects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "project.json").write_text(
        json.dumps(_cfg("good", tmp_path / "repo"))
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "project.json").write_text("{nope")
    (tmp_path / "empty").mkdir()
    (tmp_path / "Bad Name").mkdir()
    (tmp_path / "Bad Name" / "project.json").write_text("{nope")
    repos, unreadable = project.configured_repos()
    assert repos == [("good", "r", (tmp_path / "repo").resolve())]
    assert unreadable == ["bad"]


def test_configured_repos_with_no_state_dir_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path / "missing"))
    assert project.configured_repos() == ([], [])


def test_the_installer_reports_an_unreadable_config_and_still_does_the_rest(
    tmp_path, monkeypatch
):
    from devloop import install

    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path / "state"))
    state = tmp_path / "state"
    (state / "good").mkdir(parents=True)
    (state / "good" / "project.json").write_text(
        json.dumps(_cfg("good", tmp_path / "nowhere"))
    )
    (state / "bad").mkdir()
    (state / "bad" / "project.json").write_text("{nope")
    lines, failed = install.install_clone_hooks()
    assert failed is True
    assert "bad: project.json cannot be loaded; no push policy" in lines
    assert "good/r: skipped: not a git repository" in lines


def test_only_project_and_store_walk_the_state_root():
    """A second walk of the state root is a second rule for which projects exist."""
    offenders = []
    for path in SOURCES:
        rel = str(path.relative_to(ROOT))
        if rel in OWNERS or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "state_root()" in text and ".iterdir()" in text:
            offenders.append(rel)
    assert offenders == []
