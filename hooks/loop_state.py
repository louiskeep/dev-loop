"""State store and merge-gate logic for the dev-loop plugin.

State is validated on load and on save. The merge decision compares each required
gate's attested commit to the commit an operation would land on a protected branch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATE_FILENAME = ".loop-state.json"
REPO_CONFIG_FILENAME = ".loop-config.json"
SCHEMA_VERSION = 1
_RISKS = {"R0", "R1", "R2", "R3"}
_ARTIFACT_GATES = {"dennis", "codex"}
_TOP_FIELDS = {
    "schema_version",
    "slice",
    "risk",
    "risk_rationale",
    "slice_base",
    "gates",
    "remediation",
}
_GATE_NAMES = {"plan_review", "dennis", "codex"}


class GitError(Exception):
    pass


class StateError(Exception):
    pass


def repo_root(start: Path) -> Path:
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitError(f"not a git repo at {start}: {exc}") from exc
    return Path(out.stdout.strip())


def rev_parse(root: Path, ref: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitError(f"cannot resolve ref {ref}: {exc}") from exc
    return out.stdout.strip()


def _plugin_root() -> Path:
    import os

    return Path(
        os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent
    )


def load_config(root: Path) -> dict:
    config: dict = {}
    plugin_cfg = _plugin_root() / "config.json"
    if plugin_cfg.exists():
        config.update(json.loads(plugin_cfg.read_text()))
    repo_cfg = Path(root) / REPO_CONFIG_FILENAME
    if repo_cfg.exists():
        config.update(json.loads(repo_cfg.read_text()))
    return config


def _is_int(v) -> bool:
    return type(v) is int  # rejects bool (type(True) is bool, not int)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_state(state: dict) -> None:
    if not isinstance(state, dict):
        raise StateError("state is not an object")
    if not (
        _is_int(state.get("schema_version"))
        and state["schema_version"] == SCHEMA_VERSION
    ):
        raise StateError(f"unsupported schema_version: {state.get('schema_version')!r}")
    unknown = set(state) - _TOP_FIELDS
    if unknown:
        raise StateError(f"unknown top-level field(s): {sorted(unknown)}")
    for key in _TOP_FIELDS:
        if key not in state:
            raise StateError(f"missing required field: {key}")
    for key in ("slice", "risk_rationale", "slice_base"):
        if not _nonempty_str(state[key]):
            raise StateError(f"field {key} must be a non-empty string")
    if not isinstance(state["risk"], str) or state["risk"] not in _RISKS:
        raise StateError(f"invalid risk: {state['risk']!r}")
    if not isinstance(state["gates"], dict):
        raise StateError("gates must be an object")
    for name, g in state["gates"].items():
        if name not in _GATE_NAMES:
            raise StateError(f"unknown gate: {name}")
        if not isinstance(g, dict):
            raise StateError(f"gate {name} is not an object")
        allowed = {"status", "reviewer", "ts"} | (
            {"at_commit"} if name in _ARTIFACT_GATES else set()
        )
        extra = set(g) - allowed
        if extra:
            raise StateError(f"gate {name} has unknown field(s): {sorted(extra)}")
        missing = allowed - set(g)
        if missing:
            raise StateError(f"gate {name} missing field(s): {sorted(missing)}")
        if g["status"] not in ("green", "red"):
            raise StateError(f"gate {name} has invalid status")
        if not _nonempty_str(g["reviewer"]) or not _nonempty_str(g["ts"]):
            raise StateError(f"gate {name} has empty reviewer/ts")
        if name in _ARTIFACT_GATES and not _nonempty_str(g["at_commit"]):
            raise StateError(f"gate {name} missing at_commit")
    rem = state["remediation"]
    if not isinstance(rem, dict) or set(rem) != {"open_findings"}:
        raise StateError("remediation must be exactly {open_findings}")
    if not _is_int(rem["open_findings"]) or rem["open_findings"] < 0:
        raise StateError("remediation.open_findings must be a non-negative integer")


def load_state(root: Path) -> dict | None:
    path = Path(root) / STATE_FILENAME
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise StateError(f"corrupt state file: {exc}") from exc
    validate_state(state)
    return state


def save_state(root: Path, state: dict) -> None:
    validate_state(
        state
    )  # never write schema-invalid state (e.g. negative open_findings)
    (Path(root) / STATE_FILENAME).write_text(json.dumps(state, indent=2) + "\n")


def gate_status(state: dict, gate: str, landing_commit: str) -> str:
    g = state.get("gates", {}).get(gate)
    if not g:
        return "missing"
    if g.get("status") != "green":
        return "red"
    if g.get("at_commit") != landing_commit:
        return "stale"
    return "green"


def docs_current(
    root: Path, slice_base: str, landing_commit: str, config: dict
) -> dict:
    out = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            f"{slice_base}..{landing_commit}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    changed = set(out.stdout.split())

    def _any(paths: list[str]) -> bool:
        return any(p in changed for p in paths)

    return {
        "roadmap": _any(config.get("roadmap_paths", [])),
        "shipped_log": _any(config.get("shipped_log_paths", [])),
    }


def is_mergeable(
    state: dict, landing_commit: str, root: Path, config: dict
) -> tuple[bool, list[str]]:
    validate_state(state)  # missing/unknown risk fails closed here
    reasons: list[str] = []
    required = ["dennis"]
    if state["risk"] in config.get("codex_required_risks", ["R2", "R3"]):
        required.append("codex")
    for gate in required:
        status = gate_status(state, gate, landing_commit)
        if status != "green":
            reasons.append(
                f"{gate} gate is {status} for landing commit {landing_commit[:8]}"
            )
    docs = docs_current(root, state["slice_base"], landing_commit, config)
    if config.get("roadmap_paths") and not docs["roadmap"]:
        reasons.append("roadmap not touched in this slice")
    if config.get("shipped_log_paths") and not docs["shipped_log"]:
        reasons.append("shipped_log not touched in this slice")
    if state.get("remediation", {}).get("open_findings", 0):
        reasons.append(
            f"{state['remediation']['open_findings']} open remediation finding(s)"
        )
    return (not reasons, reasons)


def _resolve_root(repo_arg: str | None) -> Path:
    import os

    start = Path(repo_arg or os.environ.get("PWD") or ".")
    return repo_root(start)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import datetime
    import sys

    p = argparse.ArgumentParser(prog="loop_state")
    p.add_argument("--repo", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--slice", required=True)
    pi.add_argument("--risk", required=True, choices=sorted(_RISKS))
    pi.add_argument("--rationale", required=True)

    pg = sub.add_parser("record-gate")
    pg.add_argument("gate", choices=["plan_review", "dennis", "codex"])
    pg.add_argument("--status", required=True, choices=["green", "red"])
    pg.add_argument("--reviewer", required=True)
    pg.add_argument("--commit", default="HEAD")

    pf = sub.add_parser("set-findings")
    pf.add_argument("n", type=int)

    pc = sub.add_parser("check-merge")
    pc.add_argument("--landing", default="HEAD")

    sub.add_parser("refresh-docs-current")
    sub.add_parser("status")

    args = p.parse_args(argv)
    root = _resolve_root(args.repo)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if args.cmd == "init":
        state = {
            "schema_version": SCHEMA_VERSION,
            "slice": args.slice,
            "risk": args.risk,
            "risk_rationale": args.rationale,
            "slice_base": rev_parse(root, "HEAD"),
            "gates": {},
            "remediation": {"open_findings": 0},
        }
        save_state(root, state)
        gi = root / ".gitignore"
        lines = gi.read_text().splitlines() if gi.exists() else []
        if STATE_FILENAME not in lines:
            gi.write_text(("\n".join(lines + [STATE_FILENAME])).strip() + "\n")
        return 0

    state = load_state(root)
    if state is None:
        print("no .loop-state.json; run 'init' first", file=sys.stderr)
        return 1

    if args.cmd == "record-gate":
        entry = {"status": args.status, "reviewer": args.reviewer, "ts": now}
        if args.gate in _ARTIFACT_GATES:
            entry["at_commit"] = rev_parse(root, args.commit)
        state.setdefault("gates", {})[args.gate] = entry
        save_state(root, state)
        return 0
    if args.cmd == "set-findings":
        if args.n < 0:
            print("open_findings must be >= 0", file=sys.stderr)
            return 1
        state.setdefault("remediation", {})["open_findings"] = args.n
        save_state(root, state)
        return 0
    if args.cmd == "refresh-docs-current":
        # informational: docs-current is computed live at check-merge; this just prints it.
        print(
            json.dumps(
                docs_current(
                    root,
                    state["slice_base"],
                    rev_parse(root, "HEAD"),
                    load_config(root),
                )
            )
        )
        return 0
    if args.cmd == "check-merge":
        landing = rev_parse(root, args.landing)
        ok, reasons = is_mergeable(state, landing, root, load_config(root))
        if ok:
            return 0
        print("; ".join(reasons), file=sys.stderr)
        return 1
    if args.cmd == "status":
        print(json.dumps(state, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
