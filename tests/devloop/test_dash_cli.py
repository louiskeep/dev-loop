"""Start, stop and report: one dashboard per project, cleaned up on every exit path."""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devloop import check, install, runs, store
from devloop.dash import lifecycle

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "bin" / "devloop-dash"


def env_for(home):
    return dict(
        os.environ, DEVLOOP_HOME=str(home), DEVLOOP_LIB=str(ROOT), PYTHONPATH=str(ROOT)
    )


def run_cli(home, *args, timeout=20):
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        env=env_for(home),
        timeout=timeout,
        check=False,
    )


def start(home, *args):
    return subprocess.Popen(
        [sys.executable, str(LAUNCHER), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_for(home),
    )


def wait_for(path, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def procs():
    started = []
    yield started
    for p in started:
        if p.poll() is None:
            p.kill()
        p.wait()


def test_default_port_stable_and_reported(proj, home):
    a = lifecycle.default_port("p")
    assert a == lifecycle.default_port("p") and 8791 <= a <= 8810
    ports = {
        lifecycle.default_port(n) for n in ("alpha", "beta", "gamma", "delta", "eps")
    }
    assert len(ports) > 1
    state, url, port = lifecycle.dash_status(proj)
    assert (state, url, port) == ("not running", None, a)


def test_lan_ip_keeps_a_named_host_and_resolves_the_wildcard():
    assert lifecycle.lan_ip("127.0.0.1") == "127.0.0.1"
    got = lifecycle.lan_ip("0.0.0.0")
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", got) and got != "0.0.0.0"


def test_sigterm_removes_pid_file(proj, home, procs):
    p = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(p)
    pid_file = home / "p" / "dash.json"
    assert wait_for(pid_file)
    rec = json.loads(pid_file.read_text())
    assert rec["pid"] == p.pid and runs.wrapper_alive(rec)
    assert (
        json.loads((home / "p" / "dash-port.json").read_text())["port"] == rec["port"]
    )
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=10) == 0
    assert not pid_file.exists()
    assert lifecycle.dash_status(proj)[0] == "not running"


def test_sigint_also_cleans_up(proj, home, procs):
    p = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(p)
    assert wait_for(home / "p" / "dash.json")
    p.send_signal(signal.SIGINT)
    assert p.wait(timeout=10) == 0 and not (home / "p" / "dash.json").exists()


def test_stale_pid_file_reads_dead(proj, home):
    store.write_json(
        home / "p" / "dash.json",
        {
            "pid": 2**22 + 7,
            "pid_start": "1",
            "boot_id": runs.boot_id(),
            "port": 9000,
            "host": "0.0.0.0",
        },
    )
    assert lifecycle.dash_status(proj)[0] == "not running"
    store.write_json(
        home / "p" / "dash.json",
        {
            "pid": os.getpid(),
            "pid_start": "1",
            "boot_id": runs.boot_id(),
            "port": 9000,
            "host": "0.0.0.0",
        },
    )
    assert lifecycle.dash_status(proj)[0] == "not running"  # pid alive, start differs


def test_a_killed_server_is_replaced(proj, home, procs):
    p = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(p)
    assert wait_for(home / "p" / "dash.json")
    p.kill()
    p.wait()
    assert (home / "p" / "dash.json").exists()  # SIGKILL leaves the file behind
    q = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(q)
    end = time.time() + 10
    while time.time() < end:
        rec = json.loads((home / "p" / "dash.json").read_text())
        if rec["pid"] == q.pid:
            break
        time.sleep(0.05)
    assert rec["pid"] == q.pid
    q.send_signal(signal.SIGTERM)
    q.wait(timeout=10)


def test_second_start_same_project_other_port_refused(proj, home, procs):
    p = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(p)
    assert wait_for(home / "p" / "dash.json")
    before = (home / "p" / "dash.json").read_text()
    second = run_cli(home, "p", "--port", "0", "--host", "127.0.0.1")
    assert second.returncode == 3 and "already running" in second.stdout
    third = run_cli(home, "p", "--detach")
    assert third.returncode == 0 and "Dashboard: http://" in third.stdout
    assert (home / "p" / "dash.json").read_text() == before  # untouched
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)


def test_two_projects_get_distinct_ports(home, minimal_project, write_project, procs):
    for name in ("alpha", "beta"):
        write_project(home, name, minimal_project(name))
    a = start(home, "alpha", "--host", "127.0.0.1")
    b = start(home, "beta", "--host", "127.0.0.1")
    procs += [a, b]
    assert wait_for(home / "alpha" / "dash.json") and wait_for(
        home / "beta" / "dash.json"
    )
    pa = json.loads((home / "alpha" / "dash.json").read_text())["port"]
    pb = json.loads((home / "beta" / "dash.json").read_text())["port"]
    assert pa != pb and 8791 <= pa <= 8810 and 8791 <= pb <= 8810
    for p in (a, b):
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)


def test_busy_default_port_moves_on_and_an_explicit_busy_port_is_refused(
    proj, home, procs
):
    import socket

    want = lifecycle.default_port("p")
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        blocker.bind(("127.0.0.1", want))
        blocker.listen()
    except OSError:
        pytest.skip("default port already in use on this machine")
    try:
        p = start(home, "p", "--host", "127.0.0.1")
        procs.append(p)
        assert wait_for(home / "p" / "dash.json")
        got = json.loads((home / "p" / "dash.json").read_text())["port"]
        assert got != want and 8791 <= got <= 8810
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        out = run_cli(home, "p", "--host", "127.0.0.1", "--port", str(want))
        assert out.returncode == 3 and f"port {want} in use" in out.stderr
        assert not (home / "p" / "dash.json").exists()
    finally:
        blocker.close()


def test_detach_prints_network_url(proj, home):
    out = run_cli(home, "p", "--detach", "--host", "127.0.0.1", "--port", "0")
    try:
        assert out.returncode == 0
        m = re.fullmatch(r"Dashboard: http://127\.0\.0\.1:(\d+)/\n", out.stdout)
        assert m, out.stdout
        rec = json.loads((home / "p" / "dash.json").read_text())
        assert rec["port"] == int(m.group(1)) and runs.wrapper_alive(rec)
        assert (home / "p" / "dash.log").exists()
    finally:
        run_cli(home, "p", "--stop")


def test_stop_kills_only_own_pid(proj, home):
    bystander = subprocess.Popen(["sleep", "30"])
    try:
        store.write_json(
            home / "p" / "dash.json",
            {
                "pid": bystander.pid,
                "pid_start": "wrong",
                "boot_id": runs.boot_id(),
                "port": 9,
                "host": "x",
            },
        )
        out = run_cli(home, "p", "--stop")
        assert out.returncode == 0 and "not running" in out.stdout
        assert bystander.poll() is None  # the pid file did not prove it was ours
    finally:
        bystander.kill()
        bystander.wait()
    started = run_cli(home, "p", "--detach", "--host", "127.0.0.1", "--port", "0")
    assert started.returncode == 0
    stopped = run_cli(home, "p", "--stop")
    assert stopped.returncode == 0 and "stopped" in stopped.stdout
    assert not (home / "p" / "dash.json").exists()


def test_missing_project_exits_two_without_creating_state(home):
    out = run_cli(home, "ghost")
    assert out.returncode == 2 and "no such project" in out.stderr
    assert not (home / "ghost").exists()
    assert run_cli(home, "bad name").returncode == 2
    assert run_cli(home).returncode == 2


def test_bad_port_flag_is_a_usage_error(proj, home):
    assert run_cli(home, "p", "--port", "70000").returncode == 2
    assert run_cli(home, "p", "--port", "x").returncode == 2


def test_detach_fails_cleanly_when_the_child_dies(proj, home):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        out = run_cli(home, "p", "--detach", "--host", "127.0.0.1", "--port", str(port))
        assert out.returncode == 3 and "dash.log" in out.stderr
    finally:
        s.close()


def test_check_reports_dash_running_and_not_running(proj, home, procs):
    def line():
        return next(m for ok, m in check.run_checks(proj) if m.startswith("dash:"))

    assert re.fullmatch(r"dash: not running \(port \d+\)", line())
    p = start(home, "p", "--port", "0", "--host", "127.0.0.1")
    procs.append(p)
    assert wait_for(home / "p" / "dash.json")
    assert re.fullmatch(r"dash: running at http://127\.0\.0\.1:\d+/", line())
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)


def test_install_ships_dash_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    bins = tmp_path / "bin"
    version = install.install_package(ROOT)
    install.install_bins(ROOT, bins)
    lib = tmp_path / "h" / ".local" / "share" / "devloop" / "lib"
    assert (lib / "devloop" / "dash" / "web" / "dash.js").is_file()
    assert (lib / "devloop" / "dash" / "web" / "dash.css").is_file()
    assert version.is_dir() and os.access(bins / "devloop-dash", os.X_OK)


def test_default_host_is_loopback_and_dash_host_overrides(proj, cfg_edit, home):
    cfg = {}
    assert lifecycle.DEFAULT_HOST == "127.0.0.1"
    assert lifecycle.pick_host(None, cfg) == "127.0.0.1"
    assert lifecycle.pick_host(None, {"dash": {"host": "0.0.0.0"}}) == "0.0.0.0"
    assert lifecycle.pick_host("10.0.0.5", {"dash": {"host": "0.0.0.0"}}) == "10.0.0.5"
    # End to end: with no flag the server binds loopback, and the saved host says so.
    out = run_cli(home, "p", "--detach", "--port", "0")
    try:
        assert out.returncode == 0 and "http://127.0.0.1:" in out.stdout
        assert json.loads((home / "p" / "dash.json").read_text())["host"] == "127.0.0.1"
    finally:
        run_cli(home, "p", "--stop")
    cfg_edit(lambda c: c.update(dash={"host": "0.0.0.0"}))
    out = run_cli(home, "p", "--detach", "--port", "0")
    try:
        assert out.returncode == 0
        assert json.loads((home / "p" / "dash.json").read_text())["host"] == "0.0.0.0"
    finally:
        run_cli(home, "p", "--stop")
