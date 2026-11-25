"""Paths, atomic file writes under flock, and the event journal.

Every state write goes through here so that two processes (the tick, a worker
wrapper, an operator shell) never lose each other's update.
"""

import contextlib
import datetime
import fcntl
import json
import os
import re
import threading
from pathlib import Path


class StoreError(Exception):
    """A state file is unreadable or a name is not allowed."""


class HeadlessRefused(Exception):
    """An owner-only action was attempted from a headless session."""


# Names that become path components. fullmatch only: `$` would let "x\n" through.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

_tls = threading.local()


def check_name(value, what: str = "name") -> str:
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        raise StoreError(f"bad {what}")
    return value


def state_dir(project: str) -> Path:
    check_name(project, "project name")
    base = os.environ.get("DEVLOOP_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state" / "devloop"
    d = root / project
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return d


def run_dir(project: str, run_id: str) -> Path:
    if not isinstance(run_id, str) or TOKEN.fullmatch(run_id) is None:
        raise StoreError("bad run id")
    return state_dir(project) / "runs" / run_id


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def is_headless() -> bool:
    return os.environ.get("DEVLOOP_HEADLESS") == "1"


def require_interactive(action: str) -> None:
    if is_headless():
        raise HeadlessRefused(f"{action} is refused in a headless session")


@contextlib.contextmanager
def locked(path):
    """Exclusive flock on `<path>.lock`; re-entrant within one thread.

    Re-entry matters because write_json takes the same lock a caller already
    holds for its read-modify-write, and two flocks on one file would deadlock.
    """
    path = Path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    held = _tls.__dict__.setdefault("held", {})
    key = str(lock_path)
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
    finally:
        os.close(fd)


def write_text(path, text: str) -> None:
    path = Path(path)
    with locked(path):
        tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def write_json(path, obj: dict) -> None:
    write_text(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def read_json(path, default=None) -> dict:
    """Read a JSON object. A missing file gives `default` ({} when None)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {} if default is None else default
    bad = False
    try:
        data = json.loads(text)
    except ValueError:
        bad = True
    # Raised outside the except block so the error carries no __context__.
    if bad or not isinstance(data, dict):
        raise StoreError(f"unreadable JSON object: {path.name}")
    return data


def read_header(path) -> dict:
    """Parse `key: value` lines up to a `---` line; {} when the file is missing."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out = {}
    for line in text.splitlines():
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def append_event(project: str, kind: str, **fields) -> None:
    if TOKEN.fullmatch(kind) is None:
        raise StoreError("bad event kind")
    line = json.dumps({**fields, "at": now_iso(), "kind": kind}, sort_keys=True)
    path = state_dir(project) / "events.jsonl"
    with locked(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def package_root() -> Path:
    """Directory that holds the `devloop` package (the repo checkout or the installed lib)."""
    return Path(__file__).resolve().parent.parent


def skill_dir() -> Path:
    """Where TICK.md, PLAYBOOK.md and the brief template live."""
    env = os.environ.get("DEVLOOP_SKILL_DIR")
    if env:
        return Path(env)
    beside = package_root() / "skills" / "dev-loop"
    if beside.is_dir():
        return beside
    return Path.home() / ".claude" / "skills" / "dev-loop"


def bin_dir() -> Path:
    """Where the `devloop-*` launchers are, for the wrapper and the tick to start each other."""
    env = os.environ.get("DEVLOOP_BIN_DIR")
    if env:
        return Path(env)
    beside = package_root() / "bin"
    return beside if (beside / "devloop-wrap").is_file() else Path.home() / "bin"
