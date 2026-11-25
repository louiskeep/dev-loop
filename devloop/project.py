"""Load and validate `project.json`.

A typo here would otherwise surface mid-tick, so every refusal names the key path.
"""

import json
import re

from . import store

TIERS = ("R0", "R1", "R2")
SLOTS = ("builder", "tester", "reviewer", "planner")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ROLES = ("tick", "planner", "builder", "reviewer")


class ConfigError(Exception):
    pass


def project_path(project: str):
    return store.state_dir(project) / "project.json"


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


def base_branches(cfg) -> str:
    """Comma list the headless merge guard treats as base branches: main and master plus every
    configured base, so a project on `develop` is guarded the same way."""
    names = {"main", "master"}
    names.update(r["base"] for r in cfg.get("repos", []) if r.get("base"))
    for rules in ((cfg.get("merge") or {}).get("repos") or {}).values():
        if rules.get("base"):
            names.add(rules["base"])
    return ",".join(sorted(names))
