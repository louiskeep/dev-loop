import json
import os
import subprocess
from pathlib import Path

import pytest

from devloop import board

HEAD = "a" * 40
BIN = Path(__file__).resolve().parents[2] / "bin" / "devloop-state"


@pytest.fixture
def merged(proj, mk_slice, fakebin, home, tmp_path, monkeypatch):
    gh = tmp_path / "gh.json"
    monkeypatch.setenv("FAKE_GH_JSON", str(gh))
    key = mk_slice(proj, phase="DOCUMENT", head=HEAD, pr=7)

    def run(head=HEAD, **gh_fields):
        gh.write_text(json.dumps(gh_fields))
        return subprocess.run(
            [str(BIN), proj, "merged", key, "--pr", "7", "--head", head],
            capture_output=True,
            text=True,
            env={**os.environ, "DEVLOOP_HOME": str(home)},
        )

    run.key = key
    return run


def test_records_a_merged_pr_in_the_ledger(proj, merged):
    r = merged(state="MERGED", headRefOid=HEAD)
    assert r.returncode == 0, r.stderr
    recs = board.merge_records(proj)
    assert len(recs) == 1
    rec = next(iter(recs.values()))
    assert rec["key"] == merged.key and rec["pr"] == 7


def test_recording_twice_keeps_one_entry(proj, merged):
    merged(state="MERGED", headRefOid=HEAD)
    merged(state="MERGED", headRefOid=HEAD)
    assert len(board.merge_records(proj)) == 1


def test_refuses_an_open_pr(proj, merged):
    r = merged(state="OPEN", headRefOid=HEAD)
    assert r.returncode == 3 and "REFUSED" in r.stderr
    assert board.merge_records(proj) == {}


def test_refuses_a_head_mismatch(proj, merged):
    r = merged(head="b" * 40, state="MERGED", headRefOid=HEAD)
    assert r.returncode == 3
    assert board.merge_records(proj) == {}
