"""PreToolUse hook: flag an edit that WEAKENS a protected test file.

The recurring failure mode this guards (see rules/testing.md, rules/delegation.md):
a builder whose fast/native path diverges from the oracle loosens the acceptance
test to green the suite (drops `check_metadata=True`, narrows an equality assert
to values-only, adds `@pytest.mark.skip`/`xfail`, deletes a case) and frames the
divergence as benign. A subagent is the common offender; plugin PreToolUse hooks
fire for subagent tool calls, and the input carries `agent_id`/`agent_type` when
the call came from one, so this records who did it.

Detection is DIRECTIONAL: it fires only when a guard present in the old text is
gone (or a suppressor appears) in the new text, so additive test authoring never
trips it. It is a heuristic, so the default mode is WARN, not block: every hit is
appended to a `.loop-test-guard.log` audit line (the channel to watch), and the
edit proceeds. Set `test_guard_mode: "block"` once the log shows it is precise.

This is best-effort in-session enforcement, not a boundary: an independent
adversarial gate (dennis + a cross-model final review) on the exact artifact is
what actually catches a laundered divergence. This just makes the shortcut
visible and, optionally, hard.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import re
import sys
from pathlib import Path

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

# A file is a protected test if its BASENAME matches one of these globs, or if any
# directory SEGMENT of its path is one of the test dirs. Precise on both axes: a
# non-test source file does not become protected just because some ancestor
# directory happens to start with "test_".
_DEFAULT_FILE_GLOBS = ["test_*.py", "*_test.py"]
_DEFAULT_TEST_DIRS = ["tests"]

# A "strong" assertion whose removal is a real coverage loss: an equality /
# schema-parity / identity check. A bare `assert x` (truthiness) is not counted,
# so ordinary refactors that move soft asserts around do not fire.
_STRONG_ASSERT = re.compile(r"^\s*assert\b.*(==|!=|\.equals\(|check_metadata| is )")
_CHECK_METADATA = re.compile(r"check_metadata\s*=\s*True")
_EQUALS_PARITY = re.compile(r"\.equals\(")
# Suppressors whose APPEARANCE weakens a test.
_SUPPRESSOR_ADD = re.compile(r"@?\s*pytest\.mark\.(skip|xfail)\b|@\s*(skip|xfail)\b|pytest\.skip\(")


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text))


def _count_strong_asserts(text: str) -> int:
    return sum(1 for line in text.splitlines() if _STRONG_ASSERT.match(line))


def detect_weakening(old: str, new: str) -> list[str]:
    """Directional weakening signals present in `old` but lost (or suppressors
    gained) in `new`. Pure and side-effect-free so it is unit-testable."""
    signals: list[str] = []
    if _count(_CHECK_METADATA, new) < _count(_CHECK_METADATA, old):
        signals.append("removed check_metadata=True")
    if _count(_EQUALS_PARITY, new) < _count(_EQUALS_PARITY, old):
        signals.append("removed a .equals(...) parity assertion")
    if _count_strong_asserts(new) < _count_strong_asserts(old):
        signals.append("removed a strong (==/equals/is) assertion")
    if _count(_SUPPRESSOR_ADD, new) > _count(_SUPPRESSOR_ADD, old):
        signals.append("added a skip/xfail suppressor")
    return signals


def _pairs_from_event(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    """(old, new) text pairs for the edit. Write has no old_string, so the
    on-disk file is its `old` (a Write replaces the whole file)."""
    if tool_name == "Edit":
        return [(tool_input.get("old_string", "") or "", tool_input.get("new_string", "") or "")]
    if tool_name == "MultiEdit":
        return [
            (e.get("old_string", "") or "", e.get("new_string", "") or "")
            for e in tool_input.get("edits", [])
            if isinstance(e, dict)
        ]
    if tool_name == "Write":
        new = tool_input.get("content", "") or ""
        fp = tool_input.get("file_path") or ""
        old = ""
        try:
            old = Path(fp).read_text()
        except OSError:
            old = ""  # new file: nothing weakened
        return [(old, new)]
    return []


def _is_protected_test(file_path: str, file_globs: list[str], test_dirs: list[str]) -> bool:
    if not file_path:
        return False
    p = Path(file_path)
    if any(fnmatch.fnmatch(p.name, g) for g in file_globs):
        return True
    return any(d in p.parts for d in test_dirs)


def _load_config(cwd: Path) -> dict:
    """Plugin config.json merged with a repo .loop-config.json under `cwd`, if
    present. Kept independent of loop_state so the guard works outside a slice."""
    cfg: dict = {}
    root = Path(__file__).resolve().parent.parent
    plugin_cfg = root / "config.json"
    if plugin_cfg.exists():
        try:
            cfg.update(json.loads(plugin_cfg.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    repo_cfg = cwd / ".loop-config.json"
    if repo_cfg.exists():
        try:
            cfg.update(json.loads(repo_cfg.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def _audit(event: dict, cwd: Path, file_path: str, signals: list[str], mode: str) -> None:
    """Append one JSON line to the audit log. This is the observability channel;
    it records who (agent) weakened which test and how, in warn AND block mode."""
    line = json.dumps(
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "agent_id": event.get("agent_id") or None,
            "agent_type": event.get("agent_type") or None,
            "session_id": event.get("session_id"),
            "tool": event.get("tool_name"),
            "file": file_path,
            "signals": signals,
            "mode": mode,
        }
    )
    fallback = Path(__file__).resolve().parent.parent / ".loop-test-guard.log"
    for target in (cwd / ".loop-test-guard.log", fallback):
        try:
            with target.open("a") as fh:
                fh.write(line + "\n")
            return
        except OSError:
            continue


def _message(file_path: str, signals: list[str], is_subagent: bool) -> str:
    who = "A subagent" if is_subagent else "This session"
    return (
        f"dev-loop test-guard: {who} is editing a protected test file "
        f"({Path(file_path).name}) in a way that WEAKENS it: {'; '.join(signals)}. "
        "A reference/oracle divergence is fixed at the source, not tested around. If "
        "this is a legitimate change (an obsolete test, a genuinely wrong assertion), "
        "say so explicitly in your report so the orchestrator can verify it at the "
        "merge gate."
    )


def main_from_event(event: dict) -> int:
    if event.get("tool_name") not in _EDIT_TOOLS:
        return 0
    cwd = Path(event.get("cwd") or ".")
    cfg = _load_config(cwd)
    mode = cfg.get("test_guard_mode", "warn")
    if mode == "off":
        return 0
    tool_input = event.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    file_globs = cfg.get("test_guard_file_globs", _DEFAULT_FILE_GLOBS)
    test_dirs = cfg.get("test_guard_dirs", _DEFAULT_TEST_DIRS)
    if not _is_protected_test(file_path, file_globs, test_dirs):
        return 0
    is_subagent = bool(event.get("agent_id"))
    if cfg.get("test_guard_subagent_only") and not is_subagent:
        return 0

    signals: list[str] = []
    for old, new in _pairs_from_event(event["tool_name"], tool_input):
        for s in detect_weakening(old, new):
            if s not in signals:
                signals.append(s)
    if not signals:
        return 0

    _audit(event, cwd, file_path, signals, mode)
    reason = _message(file_path, signals, is_subagent)

    if mode == "block":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        print(reason, file=sys.stderr)
        return 2

    # warn (default): never stall; surface the reason best-effort and rely on the
    # audit log as the guaranteed-visible channel.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    return main_from_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
