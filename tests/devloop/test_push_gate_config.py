"""The push gate's override variable and receipt file names come from the project config."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from devloop import githooks, project

ROOT = Path(__file__).resolve().parent.parent.parent
OID = "a" * 40


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


def _write(tmp_path, monkeypatch, repo, **block):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path / "state"))
    cfg = _cfg("p", repo)
    if block:
        cfg["push_gate"] = block
    (tmp_path / "state" / "p").mkdir(parents=True)
    (tmp_path / "state" / "p" / "project.json").write_text(json.dumps(cfg))
    return cfg


def test_push_gate_defaults_when_the_block_is_absent():
    assert project.push_gate({}) == {
        "override_env": "DEVLOOP_PUSH_OVERRIDE",
        "receipt": "devloop-quality-gate",
    }


def test_push_gate_reads_the_block_and_fills_missing_keys():
    got = project.push_gate({"push_gate": {"receipt": "acme-gate"}})
    assert got == {"override_env": "DEVLOOP_PUSH_OVERRIDE", "receipt": "acme-gate"}


@pytest.mark.parametrize(
    "block",
    [
        {"receipt": "../x"},
        {"receipt": "a/b"},
        {"receipt": ".hidden"},
        {"receipt": "ok\n"},
        {"receipt": ""},
        {"receipt": 5},
        {"override_env": "lower"},
        {"override_env": "A B"},
        {"override_env": "OK\n"},
        {"override_env": "X;rm"},
        {"override_env": ""},
        {"unknown": "x"},
    ],
)
def test_a_bad_push_gate_block_is_a_config_error(tmp_path, monkeypatch, block):
    _write(tmp_path, monkeypatch, tmp_path / "r", **block)
    with pytest.raises(project.ConfigError):
        project.load_project("p")


def test_push_gate_names_for_a_listed_repo_is_that_projects_block(
    tmp_path, monkeypatch
):
    repo = tmp_path / "r"
    repo.mkdir()
    _write(tmp_path, monkeypatch, repo, receipt="acme-gate", override_env="ACME_PUSH")
    assert project.push_gate_names(repo.resolve()) == {
        "override_env": "ACME_PUSH",
        "receipt": "acme-gate",
    }
    # An unlisted repo gets the defaults, so no caller fills them itself.
    assert project.push_gate_names(tmp_path / "other") == project.push_gate({})


def test_the_override_name_rule_is_the_shims(tmp_path, monkeypatch):
    # One owner of the rule: project validates with githooks' own pattern.
    assert not hasattr(project, "_ENV_NAME")
    _write(tmp_path, monkeypatch, tmp_path / "r", override_env="lower")
    with pytest.raises(project.ConfigError):
        project.load_project("p")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def wt(tmp_path):
    d = tmp_path / "wt"
    d.mkdir()
    _git(d, "init", "-q")
    return d


def test_the_shim_carries_the_configured_override_name(wt):
    assert githooks.install_clone_hook(wt, "ACME_PUSH") == "installed"
    text = (wt / ".git" / "hooks" / "pre-push").read_text()
    assert "DEVLOOP_PUSH_OVERRIDE_VAR=ACME_PUSH exec" in text
    assert githooks.MARKER in text.split("\n")
    assert githooks.install_clone_hook(wt, "ACME_PUSH") == "unchanged"
    assert githooks.install_clone_hook(wt, "OTHER_PUSH") == "updated"


def test_an_unsafe_override_name_is_refused_and_writes_nothing(wt):
    assert githooks.install_clone_hook(wt, "A;b").startswith("refused")
    assert not (wt / ".git" / "hooks" / "pre-push").exists()


def test_an_unknown_marker_version_is_still_refused(wt):
    hook = wt / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    foreign = "#!/bin/sh\n# devloop-ref-policy v2\nexit 0\n"
    hook.write_text(foreign)
    assert githooks.install_clone_hook(wt, "ACME_PUSH").startswith("refused")
    assert hook.read_text() == foreign


def _line(ref="refs/heads/x"):
    return f"refs/heads/x {OID} {ref} {OID}\n"


def test_pre_push_honors_only_the_override_name_the_shim_passes(monkeypatch):
    delete = f"refs/heads/x {'0' * 40} refs/heads/x {OID}\n"
    base = {"DEVLOOP_PUSH_OVERRIDE_VAR": "ACME_PUSH"}
    assert githooks.pre_push_main(["origin", "git@github.com:o/r"], delete, base) == 1
    assert (
        githooks.pre_push_main(
            ["origin", "git@github.com:o/r"], delete, {**base, "ACME_PUSH": "1"}
        )
        == 0
    )
    # the old private name and the neutral default are not an override here
    for stray in ("DECOY_PUSH_OVERRIDE", "DEVLOOP_PUSH_OVERRIDE"):
        env = {**base, stray: "1"}
        assert (
            githooks.pre_push_main(["origin", "git@github.com:o/r"], delete, env) == 1
        )
    # a worker never overrides
    worker = {**base, "ACME_PUSH": "1", "DEVLOOP_RUN": "r"}
    assert githooks.pre_push_main(["origin", "git@github.com:o/r"], delete, worker) == 1
    # no name from the shim: no override at all
    assert (
        githooks.pre_push_main(
            ["origin", "git@github.com:o/r"], delete, {"DEVLOOP_PUSH_OVERRIDE": "1"}
        )
        == 1
    )
    assert os.environ.get("ACME_PUSH") is None
