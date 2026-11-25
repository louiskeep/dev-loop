"""One state dir for the dashboard tests, built through the public APIs.

Two rows of slices in different phases, a stuck slice, an executing builder, an exited
builder not yet applied, an unconfirmed test, reviewers with agent-like names, three
merges, one closed row, one waiting question, one problem and one node record.
"""

import datetime
import os

import pytest

from devloop import notes, runs, slices, store

NOW = datetime.datetime.now(datetime.timezone.utc)
AGENT_NAMES = ("zed-agent", "qux-agent")


def ago(**delta):
    return (NOW - datetime.timedelta(**delta)).isoformat()


def _agents(c):
    c["reviewers"] = {
        "zed-agent": {"kind": "dennis", "kinds": ["dennis"]},
        "qux-agent": {"kind": "codex-review", "kinds": ["codex-review", "codex-fix"]},
    }
    c["reviews"] = {
        "R0": {"plan": [], "code": []},
        "R1": {"plan": ["qux-agent"], "code": ["qux-agent"]},
        "R2": {
            "plan": [{"name": "zed-agent", "cap": 2}, "qux-agent"],
            "code": ["zed-agent", "qux-agent"],
        },
    }
    c["repos"][0]["slug"] = "own/main"


def put_slice(proj, row, name, phase, **fields):
    s = slices.new_slice(proj, row, name, repo="main", risk=fields.pop("risk", "R1"))
    s["phase"] = phase
    s.update(fields)
    slices._save(proj, s)
    return s["key"]


def put_run(proj, run_id, *, kind, key, started=None, exit_=None, alive=False, **extra):
    """A run dir written the way dispatch and the wrapper leave it."""
    rd = store.run_dir(proj, run_id)
    rd.mkdir(parents=True)
    row, sl = key.split("--", 1)
    store.write_json(
        rd / "request.json",
        {
            "id": run_id,
            "kind": kind,
            "row": row,
            "slice": sl,
            "key": key,
            "started_at": started or store.now_iso(),
            **extra,
        },
    )
    store.out_dir(rd).mkdir()
    store.register_request(proj, run_id)
    if alive:
        runs.update_request(
            proj,
            run_id,
            pid=os.getpid(),
            pid_start=runs.proc_start(os.getpid()),
            boot_id=runs.boot_id(),
            payload_pgid=os.getpgid(0),
            payload_start=runs.proc_start(os.getpgid(0)),
        )
    if exit_ is not None:
        store.write_json(rd / "exit", exit_)
    return rd


def build(proj, cfg_edit, home):
    cfg_edit(_agents)
    k = {}
    k["frame"] = put_slice(proj, "ROW1", "FRESH", "FRAME", risk=None)
    k["plan"] = put_slice(
        proj, "ROW1", "PLANNED", "PLAN", risk="R2", last_moved_at=ago(hours=5)
    )
    k["review"] = put_slice(
        proj,
        "ROW1",
        "REVIEWED",
        "REVIEW",
        pr=5,
        head="a1b2",
        round=2,
        last_moved_at=ago(minutes=20),
    )
    k["gate"] = put_slice(
        proj,
        "ROW2",
        "GATED",
        "GATE",
        risk="R2",
        pr=6,
        head="c3d4",
        last_moved_at=ago(minutes=10),
    )
    k["held"] = put_slice(
        proj,
        "ROW2",
        "HELD1",
        "HELD",
        risk="R3",
        parked_from="PLAN",
        waiting_on="question q-held",
        last_moved_at=ago(hours=1),
    )
    k["parked"] = put_slice(
        proj,
        "ROW2",
        "PARKED1",
        "PARKED",
        parked_from="DEVELOP",
        park_reason="failed-twice",
        last_moved_at=ago(hours=2),
    )
    k["develop"] = put_slice(
        proj, "ROW3", "UNAPPLIED", "DEVELOP", last_moved_at=ago(minutes=30)
    )
    put_run(proj, "builder.live", kind="builder", key=k["review"], alive=True)
    put_run(
        proj,
        "builder.done",
        kind="builder",
        key=k["develop"],
        exit_={"code": 0, "reason": "exit", "ended_at": ago(minutes=5)},
    )
    put_run(
        proj,
        "test.lost",
        kind="test",
        key=k["gate"],
        host="node-a",
        level="t1",
        exit_={"code": 255, "reason": "exit", "ended_at": ago(minutes=4)},
    )
    store.write_json(
        store.state_dir(proj) / "merges.json",
        {
            "merges": {
                "own/main#5@aaa1": {
                    "key": "ROW0--M1",
                    "row": "ROW0",
                    "slice": "M1",
                    "pr": 5,
                    "at": ago(minutes=40),
                },
                "own/main#4@bbb2": {
                    "key": "ROW0--M0",
                    "row": "ROW0",
                    "slice": "M0",
                    "pr": 4,
                    "at": ago(days=2),
                },
                "evil/repo#9@ccc3": {
                    "key": "ROW0--X",
                    "row": "ROW0",
                    "slice": "X",
                    "pr": 9,
                    "at": ago(hours=3),
                },
            }
        },
    )
    store.write_json(
        store.state_dir(proj) / "closures.json",
        {
            "closures": {
                "ROW0": {"attempted": True, "closed": True, "at": ago(hours=1)},
                "ROW9": {"attempted": True, "closed": False},
            }
        },
    )
    notes.ask(
        proj,
        "q-owner",
        question="Pick a thing",
        options=["a", "b"],
        recommended="a",
        wait=True,
    )
    notes.problem(proj, "disk-low", impact="space is short", fix="clear it")
    store.write_json(
        store.state_dir(proj) / "node-status.json",
        {
            "node-a": {"ok": True, "at": ago(minutes=2)},
            "node-b": {"ok": False, "at": ago(minutes=2)},
        },
    )
    store.append_event(proj, "merged", row="ROW0", slice="M1")
    store.append_event(
        proj, "run-dispatched", run_kind="builder", key=k["review"], run="r"
    )
    store.append_event(proj, "slice-moved", row="ROW1", slice="PLANNED", to="PLAN")
    (store.state_dir(proj) / "STATUS.md").write_text("# status\n")
    return k


@pytest.fixture
def dash(proj, cfg_edit, home, monkeypatch):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(64 * 1024 * 1024))
    return build(proj, cfg_edit, home)
