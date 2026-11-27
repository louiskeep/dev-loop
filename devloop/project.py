"""Load and validate `project.json`.

A typo here would otherwise surface mid-tick, so every refusal names the key path.
"""

import json
import re
from pathlib import Path
from typing import NamedTuple

from . import githooks, store

TIERS = ("R0", "R1", "R2")
SLOTS = ("builder", "tester", "reviewer", "planner")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ROLES = ("tick", "planner", "builder", "reviewer")
# What people read for each run kind, never an agent or persona name. Kind names stay the
# internal contract; the owner can rename a label in project.json under `roles`.
ROLE_DEFAULTS = {
    "planner": "Architect",
    "builder": "Developer",
    "codex-builder": "Developer",
    "dennis": "Senior reviewer",
    "codex-review": "Outside reviewer",
    "codex-fix": "Outside reviewer",
    "test": "QA",
    "docs": "Tech writer",
    "duty": "Duty",
    "tick": "Tech lead",
    "owner": "Product owner (you)",
}
POOL_DEFAULTS = {
    "builder": "Developers",
    "tester": "QA",
    "reviewer": "Reviewers",
    "planner": "Architects",
}


# Workers start with no MCP servers: each one would otherwise launch all of the user's
# servers, and a burst of workers ran the devbox out of memory. A project can point a
# kind at its own MCP config file instead (`mcp` in project.json).
NO_MCP = '{"mcpServers":{}}'
CLAUDE_MB = 600
CODEX_MB = 300
LIGHT_MB = 50
MEMORY_DEFAULTS = {
    "planner": CLAUDE_MB,
    "builder": CLAUDE_MB,
    "docs": CLAUDE_MB,
    "dennis": CLAUDE_MB,
    "tick": CLAUDE_MB,
    "codex-builder": CODEX_MB,
    "codex-review": CODEX_MB,
    "codex-fix": CODEX_MB,
    "test": LIGHT_MB,
    "duty": LIGHT_MB,
}
DEFAULT_MAX_DISPATCH_PER_TICK = 4


def mcp_args(cfg, kind) -> list:
    """The claude flags that replace the user's MCP servers for this kind."""
    path = (cfg.get("mcp") or {}).get(kind)
    return ["--strict-mcp-config", "--mcp-config", path or NO_MCP]


def memory_mb(cfg, kind) -> int:
    custom = (cfg.get("memory_mb") or {}).get(kind)
    return MEMORY_DEFAULTS.get(kind, CLAUDE_MB) if custom is None else custom


def max_dispatch_per_tick(cfg) -> int:
    return cfg.get("max_dispatch_per_tick", DEFAULT_MAX_DISPATCH_PER_TICK)


def role_label(cfg, kind) -> str:
    custom = (cfg.get("roles") or {}).get(kind)
    return custom or ROLE_DEFAULTS.get(kind, "Worker")


def pool_label(cfg, slot) -> str:
    return POOL_DEFAULTS.get(slot, "Other")


# The names of the push gate's override variable and receipt file are the project's own
# choice (the `push_gate` block); these are the neutral defaults. The override name is
# the one fact githooks.py also needs, so its default lives there (stdlib-only module).
PUSH_GATE_RECEIPT = "devloop-quality-gate"
# The receipt is a file in the git dir, next to `index`, `HEAD` and `config`, and
# `devloop-gate-pass` replaces it. Every accepted name ends in `-quality-gate`, which no
# git file does, so a project's choice (`acme-quality-gate` is one) cannot hit git's own.
RECEIPT_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-quality-gate")


class ConfigError(Exception):
    pass


def project_path(project: str):
    return store.state_path(project) / "project.json"


def _int(v) -> bool:
    return type(v) is int


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _fail(path: str, why: str):
    raise ConfigError(f"{path}: {why}")


def _dict(cfg, path):
    cur = cfg
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _need(cfg: dict, path: str, kind: type):
    v = _dict(cfg, path)
    if v is None:
        _fail(path, "required")
    if not isinstance(v, kind) or isinstance(v, bool):
        _fail(path, f"must be {kind.__name__}")
    return v


def _opt(cfg: dict, path: str, kind: type):
    if _dict(cfg, path) is None:
        return None
    return _need(cfg, path, kind)


def reviewer_entry(entry):
    """Normalise a roster entry to (name, cap or None); raises ValueError if malformed."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict) and set(entry) <= {"name", "cap"}:
        name, cap = entry.get("name"), entry.get("cap")
        if isinstance(name, str) and (cap is None or (_int(cap) and cap >= 1)):
            return name, cap
    raise ValueError("bad reviewer entry")


def _validate(cfg: dict, project: str) -> dict:
    if _need(cfg, "name", str) != project:
        _fail("name", "must equal the project name")
    if not _need(cfg, "intent", str).strip():
        _fail("intent", "must not be empty")
    for k in ("path", "section", "row_id"):
        _need(cfg, f"roadmap.{k}", str)
    repos = _need(cfg, "repos", list)
    if not repos:
        _fail("repos", "needs at least one repo")
    seen = set()
    for i, repo in enumerate(repos):
        if not isinstance(repo, dict):
            _fail(f"repos[{i}]", "must be an object")
        for k in ("name", "path"):
            if not isinstance(repo.get(k), str) or not repo[k]:
                _fail(f"repos[{i}].{k}", "must be a non-empty string")
        if not isinstance(repo.get("slug"), str) or not SLUG.fullmatch(repo["slug"]):
            _fail(f"repos[{i}].slug", "must be OWNER/REPO (the GitHub repository)")
        if repo["name"] in seen:
            _fail(f"repos[{i}].name", "duplicate repo name")
        seen.add(repo["name"])
        if "base" in repo and not isinstance(repo["base"], str):
            _fail(f"repos[{i}].base", "must be str")
    _need(cfg, "rules_dir", str)
    for slot in SLOTS:
        v = _dict(cfg, f"capacity.{slot}")
        if not _int(v) or v < 0:
            _fail(f"capacity.{slot}", "must be an integer >= 0")
    for role in ROLES:
        if not _need(cfg, f"models.{role}", str):
            _fail(f"models.{role}", "must not be empty")

    reviewers = _need(cfg, "reviewers", dict)
    for name, spec in reviewers.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
            _fail(f"reviewers.{name}.kind", "must be str")
        if "kinds" in spec and not _str_list(spec["kinds"]):
            _fail(f"reviewers.{name}.kinds", "must be a list of str")
        # One command per allowed (reviewer, kind) pair.
        argv = spec.get("argv", {})
        allowed = spec.get("kinds") or [spec["kind"]]
        if not isinstance(argv, dict) or not all(
            k in allowed and _str_list(v) for k, v in argv.items()
        ):
            _fail(f"reviewers.{name}.argv", "must map an allowed kind to a list of str")
        if "repair_out" in spec and not isinstance(spec["repair_out"], str):
            _fail(f"reviewers.{name}.repair_out", "must be str")
    for tier in TIERS:
        for which in ("plan", "code"):
            path = f"reviews.{tier}.{which}"
            roster = _need(cfg, path, list)
            names = []
            for i, entry in enumerate(roster):
                try:
                    name, _cap = reviewer_entry(entry)
                except ValueError:
                    _fail(f"{path}[{i}]", "must be a reviewer name or {name, cap}")
                if name not in reviewers:
                    _fail(f"{path}[{i}]", f"unknown reviewer '{name}'")
                names.append(name)
            if len(set(names)) != len(names):
                _fail(path, "reviewer listed twice")

    empty_tiers = [t for t in TIERS if not cfg["reviews"][t]["code"]]
    for rname, spec in ((cfg.get("merge") or {}).get("repos") or {}).items():
        if isinstance(spec, dict) and spec.get("dispatch_workflow") and empty_tiers:
            _fail(
                f"merge.repos.{rname}.dispatch_workflow",
                "needs a code reviewer in every tier, but reviews."
                f"{empty_tiers[0]}.code is empty",
            )
    tests = _opt(cfg, "tests", dict)
    for level, spec in (tests or {}).items():
        if not isinstance(spec, dict):
            _fail(f"tests.{level}", "must be an object")
        if "argv" in spec and not _str_list(spec["argv"]):
            _fail(f"tests.{level}.argv", "must be a list of str")
        if "deadline_s" in spec and not _int(spec["deadline_s"]):
            _fail(f"tests.{level}.deadline_s", "must be int")
        codes = spec.get("infra_codes", [])
        if not isinstance(codes, list) or not all(_int(c) for c in codes):
            _fail(f"tests.{level}.infra_codes", "must be a list of int")
        for k in ("host", "label"):
            if k in spec and not isinstance(spec[k], str):
                _fail(f"tests.{level}.{k}", "must be str")
    if (
        _dict(cfg, "shipped_doc") is not None
        and not _need(cfg, "shipped_doc", str).strip()
    ):
        _fail("shipped_doc", "must not be empty")
    if _opt(cfg, "board", dict) is not None:
        if not _str_list(cfg["board"].get("cmd")):
            _fail("board.cmd", "must be a list of str")
        pm = cfg["board"].get("phase_map", {})
        if not isinstance(pm, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in pm.items()
        ):
            _fail("board.phase_map", "must map phase names to str")
    if _opt(cfg, "notify", dict) is not None:
        if not _str_list(cfg["notify"].get("cmd")):
            _fail("notify.cmd", "must be a list of str")
        if "env_file" in cfg["notify"] and not isinstance(
            cfg["notify"]["env_file"], str
        ):
            _fail("notify.env_file", "must be str")
    if _opt(cfg, "merge", dict) is not None:
        merge = cfg["merge"]
        if not isinstance(merge.get("allowed"), bool):
            _fail("merge.allowed", "must be bool")
        if "require_up_to_date" in merge and not isinstance(
            merge["require_up_to_date"], bool
        ):
            _fail("merge.require_up_to_date", "must be bool")
        for rname, spec in (merge.get("repos") or {}).items():
            where = f"merge.repos.{rname}"
            if rname not in seen or not isinstance(spec, dict):
                _fail(where, "must name a configured repo and be an object")
            for k in ("base", "dispatch_workflow", "gate_status", "changelog_glob"):
                if k in spec and not isinstance(spec[k], str):
                    _fail(f"{where}.{k}", "must be str")
            pins = spec.get("required_check_sources", {})
            if not isinstance(pins, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in pins.items()
            ):
                _fail(f"{where}.required_check_sources", "must map check name to str")
            for k in ("required_checks", "verify_cmd"):
                if k in spec and not _str_list(spec[k]):
                    _fail(f"{where}.{k}", "must be a list of str")
    duties = _opt(cfg, "duties", dict)
    for dname, spec in (duties or {}).items():
        if store.NAME.fullmatch(dname) is None or not isinstance(spec, dict):
            _fail(f"duties.{dname}", "must be an object with a plain name")
        if not _str_list(spec.get("argv")):
            _fail(f"duties.{dname}.argv", "must be a list of str")
        if "deadline_s" in spec and not _int(spec["deadline_s"]):
            _fail(f"duties.{dname}.deadline_s", "must be int")
    if _opt(cfg, "nodes", dict) is not None:
        if "directory" in cfg["nodes"] and not isinstance(
            cfg["nodes"]["directory"], str
        ):
            _fail("nodes.directory", "must be str")
        if "hosts" in cfg["nodes"] and not _str_list(cfg["nodes"]["hosts"]):
            _fail("nodes.hosts", "must be a list of str")
    if _opt(cfg, "questions", dict) is not None and not isinstance(
        cfg["questions"].get("path"), str
    ):
        _fail("questions.path", "must be str")
    snaps = _opt(cfg, "snapshot_cmds", list)
    for i, spec in enumerate(snaps or []):
        if not isinstance(spec, dict) or not _str_list(spec.get("argv")):
            _fail(f"snapshot_cmds[{i}].argv", "must be a list of str")
    for k in ("process_doc", "tick_doc", "playbook_doc", "status_copy", "brief_extra"):
        _opt(cfg, k, str)
    roles = _opt(cfg, "roles", dict)
    for key, label in (roles or {}).items():
        if key not in ROLE_DEFAULTS:
            _fail(f"roles.{key}", "unknown role")
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= 40
            or label != " ".join(label.split())
        ):
            _fail(f"roles.{key}", "must be one line of 1 to 40 characters")
    for key, path in (_opt(cfg, "mcp", dict) or {}).items():
        if key not in MEMORY_DEFAULTS:
            _fail(f"mcp.{key}", "unknown kind")
        if not isinstance(path, str) or not path:
            _fail(f"mcp.{key}", "must be a path to an MCP config file")
    for key, mb in (_opt(cfg, "memory_mb", dict) or {}).items():
        if key not in MEMORY_DEFAULTS:
            _fail(f"memory_mb.{key}", "unknown kind")
        if not _int(mb) or mb < 0:
            _fail(f"memory_mb.{key}", "must be an integer >= 0")
    if cfg.get("mode", "headless") not in ("headless", "live"):
        _fail("mode", 'must be "headless" or "live"')
    cap = cfg.get("max_dispatch_per_tick", 1)
    if not _int(cap) or cap < 1:
        _fail("max_dispatch_per_tick", "must be an integer >= 1")
    dash = _opt(cfg, "dash", dict)
    if dash is not None:
        port = dash.get("port")
        if port is not None and (not _int(port) or not 1024 <= port <= 65535):
            _fail("dash.port", "must be an integer from 1024 to 65535")
        if "host" in dash and not isinstance(dash["host"], str):
            _fail("dash.host", "must be str")
    gate = _opt(cfg, "push_gate", dict)
    for key, value in (gate or {}).items():
        pattern = {"override_env": githooks.ENV_NAME, "receipt": RECEIPT_NAME}.get(key)
        if pattern is None:
            _fail(f"push_gate.{key}", "unknown key")
        if not isinstance(value, str) or not pattern.fullmatch(value):
            _fail(f"push_gate.{key}", "must be a plain name")
    free = cfg.get("min_free_gb")
    if free is not None and (
        isinstance(free, bool) or not isinstance(free, (int, float))
    ):
        _fail("min_free_gb", "must be a number")
    return cfg


def load_project(project: str) -> dict:
    path = project_path(project)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    if text is None:
        raise ConfigError("project.json: not found; run the project setup first")
    bad = False
    try:
        cfg = json.loads(text)
    except ValueError:
        bad = True
    if bad or not isinstance(cfg, dict):
        raise ConfigError("project.json: not a JSON object")
    return _validate(cfg, project)


def project_names() -> list:
    """Sorted names of the project directories under the state root."""
    root = store.state_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir() if d.is_dir() and store.NAME.fullmatch(d.name)
    )


def load_all():
    """([(name, cfg)] for each project with a project.json, names of unreadable ones)."""
    loaded, unreadable = [], []
    for name in project_names():
        if not project_path(name).is_file():
            continue
        try:
            loaded.append((name, load_project(name)))
        except ConfigError:
            unreadable.append(name)
    return loaded, unreadable


class Repo(NamedTuple):
    """One configured repo: its project, name, resolved path and push gate names."""

    project: str
    name: str
    path: Path
    gate: dict


def configured_repos(loaded=None):
    """The one reader of which repos the project configs list, and of their gate names.

    Returns (repos, unreadable): a `Repo` for each repo of each project that has a
    project.json (its `gate` has the defaults filled), and the names of projects whose
    config cannot be loaded. The caller picks the rule for an unreadable one: the
    installer reports it, the PR gate denies. `loaded` is a `load_all()` result, for a
    caller that reads once.
    """
    loaded, unreadable = loaded or load_all()
    repos = [
        Repo(name, repo["name"], Path(repo["path"]).resolve(), push_gate(cfg))
        for name, cfg in loaded
        for repo in cfg["repos"]
    ]
    return repos, unreadable


def push_gate(cfg) -> dict:
    """The push gate names of one project: `override_env` and `receipt`, defaults filled."""
    block = cfg.get("push_gate") or {}
    return {
        "override_env": block.get("override_env", githooks.DEFAULT_OVERRIDE_ENV),
        "receipt": block.get("receipt", PUSH_GATE_RECEIPT),
    }


def gate_for(repos, repo_path) -> dict:
    """The gate names of the first listed repo at `repo_path`, or the defaults when none
    is. No caller fills defaults itself."""
    for repo in repos:
        if repo.path == repo_path:
            return repo.gate
    return push_gate({})


def base_branches(cfg) -> str:
    """Comma list the headless merge guard treats as base branches: main and master plus every
    configured base, so a project on `develop` is guarded the same way."""
    names = {"main", "master"}
    names.update(r["base"] for r in cfg.get("repos", []) if r.get("base"))
    for rules in ((cfg.get("merge") or {}).get("repos") or {}).values():
        if rules.get("base"):
            names.add(rules["base"])
    return ",".join(sorted(names))


def is_live(cfg) -> bool:
    """True when a live loop session drives the project instead of the cron tick."""
    return cfg.get("mode") == "live"
