"""Memory facts for the dashboard, read from /proc only.

Nothing here runs a command. The `proc` root is a parameter so tests can point it at a
fixture tree. Process names come from `comm` (a command name, never arguments or paths).
"""

import re
from pathlib import Path

PAGE_BYTES = 4096
TIGHT_MARGIN_MB = 1024
TOP_N = 5
_NAME = re.compile(r"[A-Za-z0-9._+-]{1,15}")


def _kb(text, field):
    for line in text.splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise ValueError(field)


def meminfo(proc="/proc"):
    """(MemTotal, MemAvailable) in MB, or None when the file is not readable."""
    try:
        text = (Path(proc) / "meminfo").read_text()
        return _kb(text, "MemTotal") // 1024, _kb(text, "MemAvailable") // 1024
    except (OSError, ValueError, IndexError):
        return None


def _stat(proc, pid):
    """(comm-less fields after the command name) of /proc/PID/stat, or None."""
    try:
        text = (Path(proc) / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The command name may hold spaces and parens; fields restart after the last ")".
    return text.rsplit(")", 1)[-1].split()


def _rss_mb(proc, pid):
    try:
        pages = int((Path(proc) / str(pid) / "statm").read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    return pages * PAGE_BYTES // (1024 * 1024)


def _pids(proc):
    try:
        return [p.name for p in Path(proc).iterdir() if p.name.isdigit()]
    except OSError:
        return []


def _group_of(proc, pid):
    fields = _stat(proc, pid)
    # After ")": state, ppid, pgrp, ...
    return int(fields[2]) if fields and len(fields) > 2 else None


def group_rss_mb(proc, pgid) -> int:
    """Resident memory summed over every process in one process group."""
    return sum(_rss_mb(proc, p) for p in _pids(proc) if _group_of(proc, p) == int(pgid))


def top_processes(proc, skip_groups=(), n=TOP_N) -> list:
    """The biggest processes outside the workers, as [(command name, MB)]."""
    skip = {int(g) for g in skip_groups}
    rows = []
    for pid in _pids(proc):
        if _group_of(proc, pid) in skip:
            continue
        try:
            name = (Path(proc) / pid / "comm").read_text().strip()
        except OSError:
            continue
        rows.append((name if _NAME.fullmatch(name) else "?", _rss_mb(proc, pid)))
    rows.sort(key=lambda r: -r[1])
    return rows[:n]


def state_of(headroom_mb, floor_mb) -> str:
    """ok, tight (within 1 GB of the floor) or low (below it)."""
    if headroom_mb < floor_mb:
        return "low"
    return "tight" if headroom_mb < floor_mb + TIGHT_MARGIN_MB else "ok"


def read_memory(proc, workers, reserved_mb, floor_mb):
    """The memory section of the view, or None when meminfo is unreadable.

    `workers` is [(role label, slice key, payload pgid or None)]. Headroom is what
    admission sees: available memory less what recent starts still reserve.
    """
    info = meminfo(proc)
    if info is None:
        return None
    total, avail = info
    groups = [g for _, _, g in workers if g]
    headroom = avail - reserved_mb
    return {
        "total_mb": total,
        "available_mb": avail,
        "reserved_mb": reserved_mb,
        "floor_mb": floor_mb,
        "headroom_mb": headroom,
        "state": state_of(headroom, floor_mb),
        "workers": [
            {
                "role": role,
                "slice": key,
                "rss_mb": group_rss_mb(proc, g) if g else 0,
            }
            for role, key, g in workers
        ],
        "top": [{"name": n, "rss_mb": mb} for n, mb in top_processes(proc, groups)],
        # Remote hosts add their own rows here once workers run on other machines.
        "hosts": [],
    }
