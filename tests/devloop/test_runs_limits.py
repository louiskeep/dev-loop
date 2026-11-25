"""Worker memory: no MCP servers, memory-aware admission, a per-tick dispatch limit."""

import json

import pytest
from runs_support import (  # noqa: F401
    brief,
    config,
    env,
    git,
    go,
    make_repo,
    ready,
)

from devloop import project as projmod
from devloop import runs

NO_MCP = '{"mcpServers":{}}'


def _argv(proj, run_id):
    return runs.read_request(proj, run_id)["argv"]


def _has_empty_mcp(argv):
    i = argv.index("--mcp-config")
    return "--strict-mcp-config" in argv and argv[i + 1] == NO_MCP


def test_claude_workers_start_with_no_mcp_servers(proj, ready, brief):
    b = go(proj, brief, "builder", ready()[0])
    d = go(proj, brief, "dennis", ready("REVIEW", risk="R2")[0], reviewer="dennis")
    assert _has_empty_mcp(_argv(proj, b)) and _has_empty_mcp(_argv(proj, d))


def test_mcp_allow_list_replaces_the_empty_config(proj, ready, brief, cfg_edit):
    cfg_edit(lambda c: c.update(mcp={"builder": "/etc/mcp-builder.json"}))
    b = go(proj, brief, "builder", ready()[0])
    argv = _argv(proj, b)
    assert argv[argv.index("--mcp-config") + 1] == "/etc/mcp-builder.json"
    assert NO_MCP not in argv
    d = go(proj, brief, "dennis", ready("REVIEW", risk="R2")[0], reviewer="dennis")
    assert _has_empty_mcp(_argv(proj, d))  # only the named kind is replaced


def test_codex_kinds_get_no_claude_flags(proj, ready, brief):
    r = go(proj, brief, "codex-review", ready("REVIEW")[0], reviewer="codex")
    assert "--mcp-config" not in _argv(proj, r)


@pytest.mark.parametrize(
    "bad",
    [
        {"mcp": {"nope": "x.json"}},
        {"mcp": {"builder": ""}},
        {"memory_mb": {"builder": -1}},
        {"memory_mb": {"nope": 5}},
        {"memory_mb": {"builder": True}},
        {"max_dispatch_per_tick": 0},
        {"max_dispatch_per_tick": "4"},
    ],
)
def test_limit_config_is_validated(proj, cfg_edit, bad):
    cfg_edit(lambda c: c.update(bad))
    with pytest.raises(projmod.ConfigError):
        projmod.load_project(proj)


def test_memory_defaults_by_kind():
    cfg = {}
    got = {k: projmod.memory_mb(cfg, k) for k in projmod.MEMORY_DEFAULTS}
    assert {got[k] for k in ("builder", "planner", "docs", "dennis", "tick")} == {600}
    assert {got[k] for k in ("codex-review", "codex-fix", "codex-builder")} == {300}
    assert {got["test"], got["duty"]} == {50}
    assert projmod.memory_mb({"memory_mb": {"builder": 7}}, "builder") == 7


def _avail(monkeypatch, mb):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(int(mb * 1024)))


def test_burst_of_dispatches_stops_at_the_reservation(proj, ready, brief, monkeypatch):
    # Floor 2560 MB. Free memory never moves, as for workers that have not grown yet.
    _avail(monkeypatch, 2560 + 600 + 600 + 599)
    go(proj, brief, "builder", ready()[0])  # 3759-0-600 ok
    go(proj, brief, "builder", ready()[0])  # 3759-600-600 ok
    with pytest.raises(runs.Refused, match="memory"):  # 3759-1200-600 < floor
        go(proj, brief, "builder", ready()[0])


def test_reservation_is_per_kind_and_configurable(
    proj, ready, brief, monkeypatch, cfg_edit
):
    _avail(monkeypatch, 2560 + 650)
    go(proj, brief, "builder", ready()[0])
    # A 50 MB test still fits after one 600 MB builder; a second builder does not.
    go(proj, brief, "test", ready("VERIFY")[0], level="t1")
    with pytest.raises(runs.Refused, match="memory"):
        go(proj, brief, "builder", ready()[0])
    cfg_edit(lambda c: c.update(memory_mb={"builder": 10}))
    go(proj, brief, "builder", ready()[0])


def test_reservations_expire_after_five_minutes(proj, ready, brief, monkeypatch, home):
    _avail(monkeypatch, 2560 + 700)
    first = go(proj, brief, "builder", ready()[0])
    with pytest.raises(runs.Refused, match="memory"):
        go(proj, brief, "builder", ready()[0])
    # Age the run past the window by rewriting the frozen request's start time.
    path = home / "p" / "runs" / first / "request.json"
    path.chmod(0o644)
    req = json.loads(path.read_text())
    req["started_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(req))
    go(proj, brief, "builder", ready()[0])


def test_tick_dispatch_limit(proj, ready, brief, monkeypatch, cfg_edit):
    cfg_edit(lambda c: c.update(max_dispatch_per_tick=2))
    monkeypatch.setenv("DEVLOOP_TICK_ID", "tick-1")
    go(proj, brief, "builder", ready()[0])
    go(proj, brief, "builder", ready()[0])
    with pytest.raises(runs.Refused, match="tick-dispatch-limit"):
        go(proj, brief, "builder", ready()[0])
    monkeypatch.setenv("DEVLOOP_TICK_ID", "tick-2")  # a new tick starts fresh
    go(proj, brief, "builder", ready()[0])


def test_tick_limit_defaults_to_four_and_needs_a_tick(proj, ready, brief, monkeypatch):
    monkeypatch.setenv("DEVLOOP_TICK_ID", "tick-1")
    for _ in range(4):
        go(proj, brief, "builder", ready()[0])
    with pytest.raises(runs.Refused, match="tick-dispatch-limit"):
        go(proj, brief, "builder", ready()[0])
    monkeypatch.delenv("DEVLOOP_TICK_ID")
    go(proj, brief, "builder", ready()[0])  # an operator shell is not limited
