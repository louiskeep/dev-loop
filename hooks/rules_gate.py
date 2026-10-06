"""Make planners and coders read the dev-rules before they write plans or code.

PreToolUse (Edit|Write|MultiEdit): writing a plan (`docs/plans/*.md`,
`docs/specs/*.md`) or a source file inside a git repository is denied until this
session (or this subagent) has read the routing table and the core rulebooks:
`README.md`, `00-universal.md` and `development-loop.md`, from the plugin's
`rules/` snapshot or the canonical dev-rules directory. The check reads the
session transcript for Read tool calls (or shell commands) that opened those
files, so it proves the files were opened in THIS session, not that they were
remembered from an earlier one. Once satisfied, a marker file skips the
transcript scan for the rest of the session.

PostToolUse (Edit|Write|MultiEdit): after a plan is written, a missing
"Rules consulted:" line is fed back to the model as a blocking reason, so the
plan names the rulebooks it was written against (rules/development-loop.md, PLAN).

Plugin PreToolUse hooks also fire for subagent tool calls. When the event carries
`agent_transcript_path`, the subagent's own transcript is checked, so a builder
must read the rules itself; otherwise the session transcript is used.

This is best-effort enforcement of a reading habit, not a boundary: opening a
file is not understanding it. Plan review (which checks the "Rules consulted"
list fits the work) is what catches a plan written against the wrong rules.

Config keys (plugin config.json, overridable per repo):
  rules_gate_mode: "block" (default) | "warn" | "off"
  rules_dirs: extra directories whose copies of the rulebooks count as read
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    from . import loop_state as ls
except ImportError:  # run standalone: python hooks/rules_gate.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import loop_state as ls

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
REQUIRED_RULES = ("README.md", "00-universal.md", "development-loop.md")
_DEFAULT_RULES_DIRS = ("/home/cam/dev-rules",)
_CODE_SUFFIXES = {
    ".py",
    ".pyi",
    ".rs",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".sql",
    ".sh",
    ".toml",
    ".yml",
    ".yaml",
    ".css",
    ".scss",
    ".html",
    ".vue",
    ".svelte",
}
_PLAN_DIRS = ("docs/plans", "docs/specs")
_RULES_LINE = re.compile(r"^\s*(\*\*)?Rules consulted:", re.IGNORECASE | re.MULTILINE)


def classify(file_path: str, repo: Path | None) -> str | None:
    """'plan', 'code', or None (not gated). Only files inside a git repo are gated."""
    if repo is None:
        return None
    path = Path(file_path)
    try:
        rel = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    if path.suffix == ".md" and any(f"/{d}/" in f"/{rel}" for d in _PLAN_DIRS):
        return "plan"
    if path.suffix in _CODE_SUFFIXES:
        return "code"
    return None


def rules_dirs(config: dict) -> list[str]:
    dirs = [str(ls._plugin_root() / "rules")]
    dirs += [d for d in _DEFAULT_RULES_DIRS if Path(d).is_dir()]
    dirs += [str(d) for d in config.get("rules_dirs", [])]
    return [d.rstrip("/") for d in dirs]


def _opened(item: dict, dirs: list[str]) -> set[str]:
    """Rulebook names a single tool_use item opened from one of `dirs`."""
    if item.get("type") != "tool_use":
        return set()
    inp = item.get("input") or {}
    found: set[str] = set()
    if item.get("name") == "Read":
        fp = str(inp.get("file_path", ""))
        for d in dirs:
            for name in REQUIRED_RULES:
                if fp == f"{d}/{name}":
                    found.add(name)
    elif item.get("name") == "Bash":
        cmd = str(inp.get("command", ""))
        for d in dirs:
            for name in REQUIRED_RULES:
                if f"{d}/{name}" in cmd:
                    found.add(name)
    return found


def rules_read(transcript: Path, dirs: list[str]) -> set[str]:
    """Which REQUIRED_RULES this transcript shows were opened."""
    found: set[str] = set()
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        if "tool_use" not in line or not any(n in line for n in REQUIRED_RULES):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    found |= _opened(item, dirs)
        if found >= set(REQUIRED_RULES):
            break
    return found


def _marker(event: dict) -> Path:
    base = (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "dev-loop"
        / "rules-gate"
    )
    key = str(event.get("session_id") or "nosession")
    if event.get("agent_id"):
        key += f"-{event['agent_id']}"
    return base / re.sub(r"[^A-Za-z0-9_.-]", "_", key)


def _repo_for(file_path: str) -> Path | None:
    start = Path(file_path).parent
    while not start.exists() and start != start.parent:
        start = start.parent
    try:
        root = ls.repo_root(start)
    except ls.GitError:
        return None
    # A repository rooted at /tmp or the home directory is a machine-wide accident,
    # not a project; never gate edits there.
    if root in (Path("/tmp"), Path.home()):
        return None
    return root


def _deny_reason(kind: str, missing: list[str], dirs: list[str]) -> str:
    where = dirs[1] if len(dirs) > 1 else dirs[0]
    paths = ", ".join(f"{where}/{name}" for name in missing)
    what = "writing a plan" if kind == "plan" else "writing code"
    return (
        f"dev-loop rules_gate: read the development rules before {what}. "
        f"Not yet read in this session: {paths}. Read them (Read tool), then read the "
        "task rulebooks the README routing table selects for this work, and retry. "
        "Plans must also carry a 'Rules consulted:' line listing the rulebooks used."
    )


def pre_tool_use(event: dict, config: dict) -> dict | None:
    file_path = str((event.get("tool_input") or {}).get("file_path", ""))
    if not file_path:
        return None
    kind = classify(file_path, _repo_for(file_path))
    if kind is None:
        return None
    marker = _marker(event)
    if marker.exists():
        return None
    transcript = event.get("agent_transcript_path") or event.get("transcript_path")
    dirs = rules_dirs(config)
    found = rules_read(Path(transcript), dirs) if transcript else set()
    missing = [n for n in REQUIRED_RULES if n not in found]
    if not missing:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(file_path)
        return None
    reason = _deny_reason(kind, missing, dirs)
    if config.get("rules_gate_mode", "block") == "warn":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": reason,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def post_tool_use(event: dict, config: dict) -> dict | None:
    file_path = str((event.get("tool_input") or {}).get("file_path", ""))
    if not file_path or classify(file_path, _repo_for(file_path)) != "plan":
        return None
    try:
        text = Path(file_path).read_text(errors="replace")
    except OSError:
        return None
    if _RULES_LINE.search(text):
        return None
    return {
        "decision": "block",
        "reason": (
            "dev-loop rules_gate: this plan has no 'Rules consulted:' line. Add one near "
            "the top listing the rulebooks the README routing table selected and that you "
            "read (always 00-universal and development-loop), e.g. "
            "'Rules consulted: 00-universal, development-loop, feature-dev, testing'."
        ),
    }


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if event.get("tool_name") not in _EDIT_TOOLS:
        return 0
    file_path = str((event.get("tool_input") or {}).get("file_path", ""))
    repo = _repo_for(file_path) if file_path else None
    config = ls.load_config(repo) if repo else ls.load_config(Path.cwd())
    if config.get("rules_gate_mode", "block") == "off":
        return 0
    handler = (
        post_tool_use if event.get("hook_event_name") == "PostToolUse" else pre_tool_use
    )
    out = handler(event, config)
    if out is not None:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
