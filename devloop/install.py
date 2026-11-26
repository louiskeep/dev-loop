"""`devloop-install BIN_DIR`: copy the package and launchers so cron needs no checkout.

Each swap is one `os.replace`, so a tick that starts mid-install sees the old files or
the new ones, never a half-written set.
"""

import os
import shutil
import sys
import time
from pathlib import Path

from . import githooks, project, store


def share_root() -> Path:
    return Path.home() / ".local" / "share" / "devloop"


def _swap_link(link: Path, target: Path) -> None:
    tmp = link.with_name(link.name + ".new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, link)


def install_package(repo: Path) -> Path:
    root = share_root()
    version = root / "versions" / str(time.time_ns())
    shutil.copytree(
        repo / "devloop",
        version / "devloop",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # The skill files ride in the same version, so a checkout edit or a branch switch
    # cannot change the tick's instructions under a running cron.
    shutil.copytree(
        repo / "skills" / "dev-loop",
        version / "skills" / "dev-loop",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # The PR gate runs from the installed lib, next to the policy the clone shim runs, so
    # both always come from one version.
    (version / "hooks").mkdir()
    shutil.copyfile(repo / "hooks" / "pr_gate.py", version / "hooks" / "pr_gate.py")
    _swap_link(root / "lib", version)
    return version


def install_bins(repo: Path, bin_dir: Path) -> list:
    bin_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for src in sorted((repo / "bin").glob("devloop-*")):
        tmp = bin_dir / f".{src.name}.tmp"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, bin_dir / src.name)
        done.append(src.name)
    return done


def install_clone_hooks():
    """Write the push-policy shim into every repo of every project.

    Returns (lines, failed): one `<project>/<repo>: <status>` line each, and whether any
    repo refused the shim or any config could not be read (a repo left unguarded).
    """
    root = store.state_root()
    names = sorted(d.name for d in root.iterdir()) if root.is_dir() else []
    lines, seen, failed = [], set(), False
    for name in names:
        if (
            store.NAME.fullmatch(name) is None
            or not (root / name / "project.json").is_file()
        ):
            continue
        try:
            cfg = project.load_project(name)
        except project.ConfigError:
            lines.append(f"{name}: project.json cannot be loaded; no push policy")
            failed = True
            continue
        for repo in cfg["repos"]:
            key = Path(repo["path"]).resolve()
            if key in seen:
                continue
            seen.add(key)
            status = githooks.install_clone_hook(key)
            failed = failed or status.startswith("refused")
            lines.append(f"{name}/{repo['name']}: {status}")
    return lines, failed


def link_skill(version: Path) -> str:
    link = Path.home() / ".claude" / "skills" / "dev-loop"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        return "skill folder exists and is not a link; left alone"
    _swap_link(link, version / "skills" / "dev-loop")
    return "skill linked"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: devloop-install BIN_DIR", file=sys.stderr)
        return 2
    repo = store.package_root()
    if not (repo / "devloop" / "__init__.py").is_file() or not (repo / "bin").is_dir():
        print("REFUSED: run devloop-install from a plugin checkout", file=sys.stderr)
        return 3
    version = install_package(repo)
    names = install_bins(repo, Path(args[0]))
    print(f"installed {len(names)} commands to {args[0]}; {link_skill(version)}")
    lines, failed = install_clone_hooks()
    for line in lines:
        print(line)
    return 1 if failed else 0
