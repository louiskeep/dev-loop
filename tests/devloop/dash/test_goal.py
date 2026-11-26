"""The top bar's goal: rows left, counted from the roadmap group tables at a git ref."""

import subprocess

from devloop.dash import roadmap

TABLE = """# Run

## 1.0

| # | Item | Repo | State | After |
|---|---|---|---|---|
| 58 | scratch | engine | PARTLY MERGED. more | - |
| 59 | publish | engine | CLOSED (engine #1) | - |
| 61 | deliver | cli | NOT STARTED | - |

## Later

| # | Item | Repo | State | After |
|---|---|---|---|---|
| 90 | later | cli | NOT STARTED | - |
"""


def test_count_section_counts_only_the_named_section():
    assert roadmap.count_section(TABLE, "1.0") == (2, 3)
    assert roadmap.count_section(TABLE, "Later") == (1, 1)
    assert roadmap.count_section(TABLE, "1.1") == (0, 0)


def test_count_section_finds_state_by_header():
    text = "## 1.0\n\n| # | State | Item |\n|---|---|---|\n| 1 | CLOSED | a |\n| 2 | open | b |\n"
    assert roadmap.count_section(text, "1.0") == (1, 2)


def _repo(tmp_path, text):
    r = tmp_path / "repo"
    (r / "docs/roadmap/groups").mkdir(parents=True)
    (r / "docs/roadmap/groups/run.md").write_text(text)
    for cmd in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(r), *cmd], check=True)
    return r


def test_goal_reads_the_ref_not_the_working_tree(tmp_path):
    r = _repo(tmp_path, TABLE)
    (r / "docs/roadmap/groups/run.md").write_text(
        "## 1.0\n"
    )  # uncommitted edit is ignored
    cfg = {
        "roadmap": {"path": str(r / "docs/ROADMAP.md")},
        "goal": {"title": "V1 complete", "section": "1.0", "ref": "HEAD"},
    }
    assert roadmap.goal(cfg) == {"title": "V1 complete", "left": 2, "total": 3}


def test_no_goal_configured_is_none():
    assert roadmap.goal({"roadmap": {"path": "/x/docs/ROADMAP.md"}}) is None


def test_bad_ref_raises_oserror(tmp_path):
    r = _repo(tmp_path, TABLE)
    cfg = {
        "roadmap": {"path": str(r / "docs/ROADMAP.md")},
        "goal": {"title": "V1", "section": "1.0", "ref": "no-such-ref"},
    }
    try:
        roadmap.goal(cfg)
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")
