import datetime
import json
import sys

import pytest

from devloop import slices, snapshot, status, store

NOW = datetime.datetime.now(datetime.timezone.utc)


def at(**delta):
    return (NOW - datetime.timedelta(**delta)).isoformat()


def journal(proj, *events):
    path = store.state_dir(proj) / "events.jsonl"
    with open(path, "a") as fh:
        for kind, when in events:
            fh.write(json.dumps({"kind": kind, "at": when}) + "\n")


def test_status_renders_counts_from_fixture_journal(proj):
    journal(
        proj,
        ("merged", at(seconds=1)),
        ("merged", at(days=3)),
        ("merged", at(days=30)),
        ("row-closed", at(days=2)),
        ("row-closed", at(days=40)),
    )
    text = status.render_status(proj)
    assert "- This week: 2" in text and "- Total: 3" in text
    assert "- Rows closed: 2" in text
    assert "- Today: 1" in text


def test_status_scrubs_planted_secret(proj, mk_slice, tmp_path, cfg_edit):
    copy = tmp_path / "vault" / "status.md"
    cfg_edit(lambda c: c.update(status_copy=str(copy)))
    k = mk_slice(proj, phase="DEVELOP", risk="R1")
    slices.set_fields(proj, k, next_action="use ghp_abcdefghijklmnop1234567 now")
    status.write_status(proj)
    for path in (store.state_dir(proj) / "STATUS.md", copy):
        assert "ghp_" not in path.read_text() and "abcdefghijkl" not in path.read_text()
    md, _ = snapshot.build_snapshot(proj)
    assert "ghp_abcdefghijklmnop" not in md


def test_scrub_removes_sha_and_path():
    s = status.scrub(
        "fixed 3f2a9c1de4b7 in /home/x/y/z.py and ~/a/b, key sk-abcdefgh12345678"
    )
    assert "3f2a9c1de4b7" not in s and "/home/x/y/z.py" not in s and "~/a/b" not in s
    assert "sk-abcdefgh" not in s and s.count("[ref]") == 3
    assert len(status.scrub("x " * 500)) <= 160
    assert "pw" not in status.scrub("see https://user:pw@host/x")


def test_snapshot_capped(proj, cfg_edit):
    cfg_edit(
        lambda c: c.update(
            snapshot_cmds=[
                {
                    "name": f"c{i}",
                    "argv": [sys.executable, "-c", "print('x' * 20000)"],
                    "max_chars": 30000,
                }
                for i in range(4)
            ]
        )
    )
    md, _ = snapshot.build_snapshot(proj)
    assert len(md) <= snapshot.CAP
    assert "## c0" in md and "## c3" not in md  # last-listed trimmed first


def test_snapshot_command_output_cut_to_max_chars(proj, cfg_edit):
    cfg_edit(
        lambda c: c.update(
            snapshot_cmds=[
                {
                    "name": "board",
                    "argv": [sys.executable, "-c", "print('y' * 500)"],
                    "max_chars": 50,
                }
            ]
        )
    )
    md, _ = snapshot.build_snapshot(proj)
    assert "y" * 50 in md and "y" * 51 not in md


def test_hash_ignores_ages_and_digits(proj, mk_slice, cfg_edit):
    def cmd(n):
        cfg_edit(
            lambda c: c.update(
                snapshot_cmds=[{"name": "n", "argv": ["echo", f"open prs {n}"]}]
            )
        )

    k = mk_slice(proj, phase="DEVELOP", risk="R1")
    cmd(12)
    _, h1 = snapshot.build_snapshot(proj)
    cmd(13)
    s = slices.load(proj, k)
    s["last_moved_at"] = at(hours=5)
    slices._save(proj, s)
    _, h2 = snapshot.build_snapshot(proj)
    assert h1 == h2
    s["phase"] = "SELF_CHECK"
    slices._save(proj, s)
    assert snapshot.build_snapshot(proj)[1] != h1


def test_snapshot_lists_stale_checklist_command_and_slots(proj, mk_slice):
    k = mk_slice(proj, phase="VERIFY", risk="R1", head="ab12cd34ef")
    s = slices.load(proj, k)
    s["last_moved_at"] = at(hours=3)
    slices._save(proj, s)
    md, _ = snapshot.build_snapshot(proj)
    assert f"- stale: {k} in VERIFY" in md
    assert "[ ] t1 GO on head" in md
    assert f"devloop-dispatch {proj} test {k} --level t1" in md
    assert "builder 8" in md and "memory available" in md


def test_snapshot_shows_finished_unapplied_runs(proj, mk_slice, mk_run):
    k = mk_slice(proj, phase="REVIEW", risk="R1", head="ab12cd34ef")
    d = mk_run(
        proj,
        "r9",
        kind="codex-review",
        key=k,
        head="ab12cd34ef",
        reviewer="codex",
        purpose="code",
        applied=False,
    )
    assert d.exists()
    md, _ = snapshot.build_snapshot(proj)
    assert f"devloop-state {proj} applied r9" in md and "GO" in md


def test_snapshot_next_command_is_the_move_when_every_item_is_met(proj, mk_slice):
    k = mk_slice(proj, phase="FRAME", risk="R1", rulebooks=["a.md"])
    md, _ = snapshot.build_snapshot(proj)
    assert f"slice move {k} PLAN" in md


@pytest.mark.parametrize("n", [3])
def test_summarize_uses_fixed_words_and_plain_ids(proj, n):
    since = at(minutes=5)
    store.append_event(proj, "merged", row="CLI-1", slice="s1")
    store.append_event(proj, "slice-moved", row="CLI-1", slice="s1", to="DOCUMENT")
    store.append_event(proj, "merged", row="ghp_secret value", slice="s2")
    text = status.summarize(proj, since)
    assert "merged CLI-1 s1" in text and "moved CLI-1 s1 to DOCUMENT" in text
    assert "secret" not in text and "?" in text
