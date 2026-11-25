"""`devloop-state PROJECT check`: everything a first tick needs, checked before it runs."""

import os
import shutil
import subprocess
from pathlib import Path

from . import project as projmod
from . import slices


def _tool(name, where):
    found = shutil.which(name) or (os.path.isfile(name) and os.access(name, os.X_OK))
    return bool(
        found
    ), f"{where}: {name} on PATH" if found else f"{where}: {name} not found"


def _argv_heads(cfg):
    for rname, spec in cfg["reviewers"].items():
        for kind, argv in (spec.get("argv") or {}).items():
            yield f"reviewers.{rname}.argv.{kind}", argv[0]
    for level, spec in (cfg.get("tests") or {}).items():
        if spec.get("argv"):
            yield f"tests.{level}.argv", spec["argv"][0]
    for name, spec in (cfg.get("duties") or {}).items():
        yield f"duties.{name}.argv", spec["argv"][0]
    for i, spec in enumerate(cfg.get("snapshot_cmds") or []):
        yield f"snapshot_cmds[{i}]", spec["argv"][0]
    if cfg.get("board"):
        yield "board.cmd", cfg["board"]["cmd"][0]
    if cfg.get("notify"):
        yield "notify.cmd", cfg["notify"]["cmd"][0]


def _run(argv, timeout):
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def run_checks(project) -> list:
    """Return (ok, message) pairs. Messages hold names and counts, never output text."""
    try:
        cfg = projmod.load_project(project)
    except projmod.ConfigError as exc:
        return [(False, f"config: {exc}")]
    out = [(True, "config: project.json is valid")]
    board = cfg.get("board")
    if board:
        missing = [p for p in slices.PHASES if p not in board.get("phase_map", {})]
        out.append(
            (
                not missing,
                "board: phase map covers every phase"
                if not missing
                else f"board: no phase map entry for {', '.join(missing)}",
            )
        )
    for name in ("claude", "gh", "git", "timeout"):
        out.append(_tool(name, "tools"))
    seen = set()
    for where, head in _argv_heads(cfg):
        if head not in seen:
            seen.add(head)
            out.append(_tool(head, where))
    paths = [
        ("roadmap", cfg["roadmap"]["path"]),
        ("rules_dir", cfg["rules_dir"] + "/README.md"),
    ]
    paths += [
        (k, cfg[k]) for k in ("process_doc", "tick_doc", "playbook_doc") if cfg.get(k)
    ]
    directory = (cfg.get("nodes") or {}).get("directory")
    if directory:
        paths.append(("nodes.directory", directory))
    for what, path in paths:
        found = Path(path).exists()
        out.append((found, f"{what}: {'found' if found else 'missing'}"))
    for repo in cfg["repos"]:
        ok = Path(repo["path"], ".git").exists()
        out.append(
            (
                ok,
                f"repos.{repo['name']}: {'is a git checkout' if ok else 'not a git checkout'}",
            )
        )
    for i, spec in enumerate(cfg.get("snapshot_cmds") or []):
        out.append((_run(spec["argv"], 60), f"snapshot_cmds[{i}]: runs"))
    env_file = (cfg.get("notify") or {}).get("env_file")
    if env_file:
        p = Path(env_file)
        ok = p.is_file() and (p.stat().st_mode & 0o077) == 0
        out.append(
            (
                ok,
                "notify.env_file: exists and is private (mode 0600)"
                if ok
                else "notify.env_file: missing or readable by others",
            )
        )
    out.append((_run(["gh", "auth", "status"], 30), "gh: authenticated"))
    for host in (cfg.get("nodes") or {}).get("hosts", []):
        out.append(
            (
                _run(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=10",
                        host,
                        "true",
                    ],
                    30,
                ),
                f"nodes: ssh to {host}",
            )
        )
    return out
