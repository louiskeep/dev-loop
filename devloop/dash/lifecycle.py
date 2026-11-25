"""Start, detach and stop one dashboard per project.

Exactly one server runs per project: `dash.lock` is held for the process lifetime, so a
second start finds it held. The flock dies with the process, so a killed server leaves
no lock; `dash.json` is then read as dead by pid, start time and boot id.
"""

import argparse
import fcntl
import hashlib
import os
import signal
import socket
import subprocess
import sys
import time

from .. import project as projmod
from .. import runs, store
from . import server

# Loopback unless the project (dash.host) or the flag says otherwise, so a shared
# plugin never opens a port to the network by default.
DEFAULT_HOST = "127.0.0.1"
PORT_BASE = 8791
PORT_SPAN = 20
DETACH_WAIT_S = 5.0
STOP_WAIT_S = 5.0
EXIT_USAGE, EXIT_REFUSED = 2, 3


def default_port(project) -> int:
    """A stable port per project, so two projects rarely contend for the same one."""
    n = int(hashlib.sha256(project.encode()).hexdigest()[:8], 16)
    return PORT_BASE + n % PORT_SPAN


def pick_host(flag, cfg) -> str:
    """The bind address: --host, then dash.host, then loopback."""
    return flag or (cfg.get("dash") or {}).get("host") or DEFAULT_HOST


def lan_ip(host) -> str:
    """The address other machines should use. The UDP connect sends no packet."""
    if host != "0.0.0.0":
        return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def _url(host, port) -> str:
    return f"http://{lan_ip(host)}:{port}/"


def _files(project):
    d = store.state_path(project)
    return {
        "dir": d,
        "pid": d / "dash.json",
        "lock": d / "dash.lock",
        "port": d / "dash-port.json",
        "log": d / "dash.log",
    }


def _running_record(project):
    """dash.json when its process is alive (pid, start time and boot id agree)."""
    try:
        rec = store.read_json(_files(project)["pid"])
    except store.StoreError:
        return None
    return rec if rec and runs.wrapper_alive(rec) else None


def _saved_port(project):
    try:
        port = store.read_json(_files(project)["port"]).get("port")
    except store.StoreError:
        return None
    return port if type(port) is int and 1 <= port <= 65535 else None


def dash_status(project):
    """(state, url, port) for `devloop-state check`. Reads only; creates nothing."""
    rec = _running_record(project)
    if rec:
        return "running", _url(rec.get("host", DEFAULT_HOST), rec["port"]), rec["port"]
    return "not running", None, _saved_port(project) or default_port(project)


def _candidates(project, cfg, explicit):
    if explicit is not None:
        return [explicit]
    first = (cfg.get("dash") or {}).get("port") or _saved_port(project)
    ring = [
        PORT_BASE + (default_port(project) - PORT_BASE + i) % PORT_SPAN
        for i in range(PORT_SPAN)
    ]
    return ([first] if first else []) + [p for p in ring if p != first]


def _bind(project, host, candidates):
    for port in candidates:
        try:
            return server.make_server(project, host, port, log=sys.stderr)
        except OSError:
            continue
    return None


def _write_record(project, srv, host):
    files = _files(project)
    port = srv.server_address[1]
    store.write_json(
        files["pid"],
        {
            "pid": os.getpid(),
            "pid_start": runs.proc_start(os.getpid()),
            "boot_id": runs.boot_id(),
            "port": port,
            "host": host,
        },
    )
    store.write_json(files["port"], {"port": port})
    return port


def _take_lock(project):
    """The per-project flock, or None when another server holds it."""
    fd = os.open(_files(project)["lock"], os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _stop_handler(signum, frame):
    raise KeyboardInterrupt


def _serve(project, cfg, host, explicit):
    fd = _take_lock(project)
    if fd is None:
        return _already(project)
    srv = None
    try:
        candidates = _candidates(project, cfg, explicit)
        srv = _bind(project, host, candidates)
        if srv is None:
            print(f"port {candidates[0]} in use", file=sys.stderr)
            return EXIT_REFUSED
        port = _write_record(project, srv, host)
        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGINT, _stop_handler)
        print(f"Dashboard: {_url(host, port)}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if srv is not None:
            srv.server_close()
        rec = _running_record(project)
        if rec and rec.get("pid") == os.getpid():
            _files(project)["pid"].unlink(missing_ok=True)
        os.close(fd)


def _already(project):
    rec = _running_record(project)
    if rec:
        print(
            f"Dashboard: {_url(rec.get('host', DEFAULT_HOST), rec['port'])} (already running)"
        )
    else:
        print("Dashboard is already starting", file=sys.stderr)
    return EXIT_REFUSED


def _detach(project, args):
    if _running_record(project):
        _already(project)
        return 0
    argv = [sys.executable, str(store.bin_dir() / "devloop-dash"), project]
    for name in ("port", "host"):
        value = getattr(args, name)
        if value is not None:
            argv += [f"--{name}", str(value)]
    env = dict(os.environ, DEVLOOP_LIB=str(store.package_root()))
    with open(_files(project)["log"], "ab") as log:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            env=env,
        )
    deadline = time.monotonic() + DETACH_WAIT_S
    while time.monotonic() < deadline:
        rec = _running_record(project)
        if rec:
            print(f"Dashboard: {_url(rec.get('host', DEFAULT_HOST), rec['port'])}")
            return 0
        time.sleep(0.1)
    print("dashboard did not start; see dash.log in the state dir", file=sys.stderr)
    return EXIT_REFUSED


def _stop(project):
    """Signal only a process that dash.json proves is ours (pid, start and boot id)."""
    rec = _running_record(project)
    if rec is None:
        print("Dashboard not running")
        return 0
    os.kill(rec["pid"], signal.SIGTERM)
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline:
        if not runs.wrapper_alive(rec):
            print("Dashboard stopped")
            return 0
        time.sleep(0.1)
    print("dashboard did not stop in time", file=sys.stderr)
    return EXIT_REFUSED


def _parser():
    p = argparse.ArgumentParser(prog="devloop-dash")
    p.add_argument("project")
    p.add_argument("--port", type=int)
    p.add_argument("--host")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--stop", action="store_true")
    return p


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    if args.port is not None and not 0 <= args.port <= 65535:
        print("bad port", file=sys.stderr)
        return EXIT_USAGE
    # Checked before any store call: store.state_dir would create a missing project.
    try:
        has_config = (store.state_path(args.project) / "project.json").is_file()
    except store.StoreError:
        has_config = False
    if not has_config:
        print("no such project; run the project setup first", file=sys.stderr)
        return EXIT_USAGE
    try:
        cfg = projmod.load_project(args.project)
    except projmod.ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return EXIT_USAGE
    host = pick_host(args.host, cfg)
    if args.stop:
        return _stop(args.project)
    if args.detach:
        return _detach(args.project, args)
    return _serve(args.project, cfg, host, args.port)
