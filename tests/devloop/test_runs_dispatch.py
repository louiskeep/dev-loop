import shutil
import subprocess
import time
from pathlib import Path

import pytest
from runs_support import (  # noqa: F401
    brief,
    config,
    env,
    finish,
    git,
    go,
    make_repo,
    ready,
    res,
)

from devloop import notes, runs, slices, store

# --- admission ----------------------------------------------------------------


def test_admission_refuses_ninth_builder(proj, ready, brief):
    for _ in range(8):
        go(proj, brief, "builder", ready()[0])
    with pytest.raises(runs.Refused, match="builder slot"):
        go(proj, brief, "builder", ready()[0])


def test_admission_refuses_fifth_tester(proj, ready, brief):
    for _ in range(4):
        go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    with pytest.raises(runs.Refused, match="tester slot"):
        go(proj, brief, "test", ready("VERIFY")[0], level="t1")


def test_admission_refuses_second_writer_same_worktree(proj, ready, brief):
    k1, wt, _ = ready()
    k2, _, _ = ready(worktree=wt)
    go(proj, brief, "builder", k1)
    with pytest.raises(runs.Refused, match="reserved"):
        go(proj, brief, "builder", k2)


def test_admission_refuses_low_memory(proj, ready, brief, monkeypatch):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", "1000")
    with pytest.raises(runs.Refused, match="memory"):
        go(proj, brief, "builder", ready()[0])


def test_admission_refuses_while_paused_or_capped(proj, ready, brief, home):
    (home / "p" / "PAUSE").write_text("")
    with pytest.raises(runs.Refused, match="paused"):
        go(proj, brief, "builder", ready()[0])
    (home / "p" / "PAUSE").unlink()
    (home / "p" / "cap-until").write_text("2999-01-01T00:00:00+00:00")
    with pytest.raises(runs.Refused, match="cap"):
        go(proj, brief, "builder", ready()[0])


def test_writer_reserved_until_applied(proj, ready, brief):
    k1, wt, _ = ready()
    k2, _, _ = ready(worktree=wt)
    r = go(proj, brief, "builder", k1)
    finish(proj, r, result=res("DONE"))
    with pytest.raises(runs.Refused, match="reserved"):
        go(proj, brief, "builder", k2)
    runs.apply_run(proj, r)
    go(proj, brief, "builder", k2)


def test_unknown_kind_refused(proj, ready, brief):
    with pytest.raises(runs.Refused, match="unknown kind"):
        go(proj, brief, "slice-runner", ready()[0])


def test_every_kind_has_reader_and_slot(proj):
    readers = {"claude", "claude-verdict", "verdict", "selfverdict", "testexit", "exit"}
    for name, kind in runs.KINDS.items():
        assert kind.reader in readers, name
        assert kind.slot in (None, "planner", "builder", "reviewer", "tester"), name
    assert runs.KINDS["test"].slot == "tester"


def test_argv_built_from_config_only(proj, ready, brief):
    import inspect

    assert "argv" not in inspect.signature(runs.dispatch).parameters
    r = go(proj, brief, "builder", ready()[0])
    argv = runs.read_request(proj, r)["argv"]
    assert argv[:2] == ["claude", "-p"] and "sonnet" in argv
    assert "--permission-mode" in argv and "auto" in argv


def test_planner_refuses_non_opus_model(proj, ready, brief, cfg_edit):
    cfg_edit(lambda c: c["models"].update(planner="sonnet"))
    with pytest.raises(runs.Refused, match="Opus"):
        go(proj, brief, "planner", ready("PLAN")[0])


def test_planner_creates_absent_plan(proj, ready, brief):
    key, wt, _ = ready("PLAN", plan_path="new-plan.md")
    r = go(proj, brief, "planner", key)
    assert runs.read_request(proj, r)["plan_sha"] is None
    assert slices.load(proj, key)["plan_frozen"] is True
    with pytest.raises(slices.SliceError, match="frozen"):
        slices.set_fields(proj, key, plan_path="other.md")


def test_dispatch_from_unrelated_cwd(proj, ready, brief, tmp_path, monkeypatch):
    key, _, _ = ready("PLAN", risk="R2")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    assert Path(runs.read_request(proj, r)["plan_copy"]).is_file()


def test_dispatch_closes_capture_pipes(proj, ready, brief, home):
    key, _, _ = ready()
    start = time.time()
    out = subprocess.run(
        [
            str(Path(__file__).resolve().parents[2] / "bin" / "devloop-dispatch"),
            proj,
            "builder",
            key,
            "--brief",
            str(brief),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert out.returncode == 0 and out.stdout.strip().startswith("builder.")
    assert time.time() - start < 20  # the wrapper sleeps 30 s with no pipe held


def test_dispatch_cli_prints_refusal(proj, ready, brief):
    out = subprocess.run(
        [
            str(Path(__file__).resolve().parents[2] / "bin" / "devloop-dispatch"),
            proj,
            "slice-runner",
            ready()[0],
            "--brief",
            str(brief),
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 3 and "REFUSED: unknown kind" in out.stderr


# --- briefs -------------------------------------------------------------------


def test_brief_contains_rules_lessons_and_contract(proj, ready, brief, tmp_path):
    key, _, _ = ready()
    row = key.split("--")[0]
    notes.lesson(proj, "L1", area=row, wrong="bad habit", instead="good habit")
    r = go(proj, brief, "builder", key)
    text = (store.run_dir(proj, r) / "brief.md").read_text()
    for needle in (
        "Risk level: R1",
        str(tmp_path / "rules" / "a.md"),
        "recurring-defects.md",
        "Standing: keep commits short.",
        "NODEDIR-MARKER",
        "bad habit",
        "BODY-MARKER",
        "result.md",
    ):
        assert needle in text, needle


def test_brief_role_section_comes_from_the_playbook(
    proj, ready, brief, monkeypatch, tmp_path
):
    skill = tmp_path / "skill"
    shutil.copytree(store.skill_dir(), skill)
    (skill / "PLAYBOOK.md").write_text(
        "## Worker briefs\n\n### builder\n\nBUILDER-MARK\n\n### dennis\n\nDENNIS-MARK\n"
    )
    monkeypatch.setenv("DEVLOOP_SKILL_DIR", str(skill))
    r = go(proj, brief, "builder", ready()[0])
    text = (store.run_dir(proj, r) / "brief.md").read_text()
    assert "BUILDER-MARK" in text and "DENNIS-MARK" not in text


def test_review_brief_ends_with_verdict_line_instruction(proj, ready, brief):
    key, _, _ = ready("PLAN", risk="R2")
    r = go(proj, brief, "dennis", key, reviewer="dennis")
    assert "VERDICT: GO" in (store.run_dir(proj, r) / "brief.md").read_text()
