import multiprocessing
import os

import pytest

from devloop import project, store


def test_write_json_is_atomic(tmp_path):
    p = tmp_path / "a.json"
    store.write_json(p, {"x": 1})
    assert store.read_json(p) == {"x": 1}
    assert not list(tmp_path.glob("*.tmp*"))


def test_bad_project_config_is_refused(
    tmp_path, monkeypatch, minimal_project, write_project
):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    cfg = minimal_project()
    cfg["reviews"]["R2"]["code"] = ["dennnis"]
    write_project(tmp_path, "p", cfg)
    with pytest.raises(project.ConfigError, match=r"reviews\.R2\.code"):
        project.load_project("p")


def test_good_project_config_loads(
    tmp_path, monkeypatch, minimal_project, write_project
):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    write_project(tmp_path, "p", minimal_project())
    assert project.load_project("p")["capacity"]["builder"] == 8


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda c: c["capacity"].update(builder=True), "capacity.builder"),
        (lambda c: c["capacity"].update(tester="4"), "capacity.tester"),
        (lambda c: c["reviews"].pop("R1"), "reviews.R1"),
        (
            lambda c: c["reviews"]["R2"]["plan"].append({"name": "codex", "cap": 0}),
            "reviews.R2.plan",
        ),
        (lambda c: c.update(name="other"), "name"),
        (lambda c: c["models"].pop("tick"), "models.tick"),
        (lambda c: c.update(board={"cmd": "board"}), "board.cmd"),
    ],
)
def test_config_refusals_name_the_key_path(
    tmp_path, monkeypatch, minimal_project, write_project, mutate, path
):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    cfg = minimal_project()
    mutate(cfg)
    write_project(tmp_path, "p", cfg)
    with pytest.raises(project.ConfigError, match=path.replace(".", r"\.")):
        project.load_project("p")


def test_corrupt_config_error_has_no_context(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "project.json").write_text("{not json")
    with pytest.raises(project.ConfigError) as exc:
        project.load_project("p")
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_corrupt_state_file_error_has_no_context(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("[1")
    with pytest.raises(store.StoreError) as exc:
        store.read_json(p)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.mark.parametrize("name", ["p\n", "../x", "a b", "", ".hidden", "a/b"])
def test_project_name_is_anchored(tmp_path, monkeypatch, name):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    with pytest.raises(store.StoreError):
        store.state_dir(name)


def test_state_dir_is_private_and_honours_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path / "h"))
    d = store.state_dir("p")
    assert d == tmp_path / "h" / "p"
    assert d.stat().st_mode & 0o777 == 0o700


def test_append_event_adds_timestamp_and_rejects_bad_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOOP_HOME", str(tmp_path))
    store.append_event("p", "slice-moved", key="k", to="PLAN")
    store.append_event("p", "again")
    lines = (tmp_path / "p" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2 and '"at"' in lines[0] and '"key": "k"' in lines[0]
    with pytest.raises(store.StoreError):
        store.append_event("p", "bad kind\n")


def test_locked_is_reentrant_in_one_thread(tmp_path):
    p = tmp_path / "a.json"
    with store.locked(p):
        store.write_json(p, {"x": 2})
    assert store.read_json(p) == {"x": 2}


def _bump(path, n):
    for _ in range(n):
        with store.locked(path):
            cur = store.read_json(path, {"n": 0})["n"]
            store.write_json(path, {"n": cur + 1})


def test_locked_serialises_read_modify_write(tmp_path):
    p = tmp_path / "c.json"
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_bump, args=(p, 25)) for _ in range(4)]
    [x.start() for x in procs]
    [x.join() for x in procs]
    assert store.read_json(p)["n"] == 100


def test_read_header_stops_at_divider(tmp_path):
    p = tmp_path / "result.md"
    p.write_text("status: DONE\nhead: a1\n---\nverdict: NO\n")
    assert store.read_header(p) == {"status": "DONE", "head": "a1"}
    assert store.read_header(tmp_path / "missing.md") == {}


def test_headless_guard(monkeypatch):
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        store.require_interactive("answer")
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    store.require_interactive("answer")
    assert os.environ.get("DEVLOOP_HEADLESS") is None
