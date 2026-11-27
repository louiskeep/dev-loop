"""Add and remove the project's cron line without touching anyone else's lines."""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import store


class CronError(Exception):
    pass


def _bin():
    return os.environ.get("DEVLOOP_CRONTAB", "crontab")


def _tag(project):
    return f"# devloop:{project}"


def read() -> str:
    """The current crontab. An empty one is only `no crontab for ...`; other errors raise."""
    try:
        r = subprocess.run(
            [_bin(), "-l"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise CronError("crontab could not be read") from None
    if r.returncode != 0:
        if "no crontab for" in r.stderr:
            return ""
        raise CronError("crontab could not be read")
    return r.stdout


def _write(text) -> None:
    try:
        r = subprocess.run(
            [_bin(), "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise CronError("crontab could not be written") from None
    if r.returncode != 0:
        raise CronError("crontab could not be written")


def _without(text, project) -> list:
    return [
        line for line in text.splitlines() if not line.rstrip().endswith(_tag(project))
    ]


def _replace(project, intended_lines) -> None:
    """Write, read back, and restore the saved copy when the result differs."""
    backup = store.state_dir(project) / "crontab.bak"
    before = read()
    store.write_text(backup, before)
    intended = "\n".join(intended_lines) + ("\n" if intended_lines else "")
    _write(intended)
    if read() != intended:
        _write(before)
        raise CronError("crontab did not match what was written; the old one is back")


def add(project, bin_dir, interval_min=30) -> None:
    state = store.state_dir(project)
    command = (
        f"PATH={shlex.quote(os.environ.get('PATH', ''))} "
        f"HOME={shlex.quote(str(Path.home()))} DEVLOOP_HEADLESS=1 "
        f"{shlex.quote(str(bin_dir))}/devloop-tick {shlex.quote(project)} "
        f">> {shlex.quote(str(state / 'cron.log'))} 2>&1"
    ).replace("%", "\\%")
    line = f"*/{int(interval_min)} * * * * {command} {_tag(project)}"
    _replace(project, _without(read(), project) + [line])


def remove(project) -> None:
    _replace(project, _without(read(), project))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devloop-cron")
    ap.add_argument("action", choices=("add", "remove"))
    ap.add_argument("project")
    ap.add_argument("--bin-dir", default=str(store.bin_dir()))
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args(argv)
    try:
        if args.action == "add":
            add(args.project, args.bin_dir, args.interval)
        else:
            remove(args.project)
    except CronError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    return 0
