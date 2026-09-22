"""PreToolUse hook: flag an edit that WEAKENS a protected test file.

The recurring failure mode this guards (see rules/testing.md, rules/delegation.md):
a builder whose fast/native path diverges from the oracle loosens the acceptance
test to green the suite and frames the divergence as benign. A subagent is the
common offender; plugin PreToolUse hooks fire for subagent tool calls, and the
input carries `agent_id`/`agent_type` when the call came from one, so this records
who did it.

Detection is DIRECTIONAL and COUNT-BASED: it fires only when the new text has
fewer of a guard than the old (or gains a suppressor), so additive test authoring
never trips it. What it actually detects (see `detect_weakening`):
  - a `check_metadata=True` removed or flipped to `False`,
  - a `.equals(...)` parity assertion removed,
  - a strong (`==` / `!=` / `.equals` / ` is `) assertion removed,
  - a `skip` / `xfail` suppressor added.
What it does NOT detect (heuristic limits, acceptable for a warn-default v1, must
not be relied on in block mode): a comparison narrowed in place (`x == y` ->
`x.shape == y.shape` keeps the count), a `pytest.approx` tolerance widened, a
`parametrize` case list shortened without touching asserts, and tokens inside
comments or strings. Edit/MultiEdit see only the changed hunk, so an assertion
merely moved elsewhere in the file reads as removed (a warn-mode false positive).

It is a heuristic, so the default mode is WARN, not block: every hit is appended
to a `.loop-test-guard.log` audit line (the channel to watch) and the edit
proceeds with NO permission decision emitted. Set `test_guard_mode: "block"` once
the log shows it is precise for your repo.

This is best-effort in-session enforcement, not a boundary: an independent
adversarial gate (a fresh-context review + a cross-model final review) on the
exact artifact is what actually catches a laundered divergence. This just makes
the shortcut visible and, optionally, hard.
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
            # errors="replace" so a pre-existing non-UTF8 test file cannot crash
            # the hook (which would fail-open and drop the signal in block mode).
            old = Path(fp).read_text(encoding="utf-8", errors="replace")
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


def _repo_root(start: Path) -> Path:
    """Nearest ancestor of `start` containing a `.git`, else `start`. Resolving
    config and the audit log at the repo root (not the raw cwd) means invoking
    from a subdirectory still finds the repo's `.loop-config.json`, matching how
    `gate_guard` resolves via `loop_state.repo_root`."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return start


def _load_config(root: Path) -> dict:
    """Plugin config.json merged with the repo's .loop-config.json, if present.
    Kept independent of loop_state so the guard works outside a slice. A repo
    file can set the policy (mode/globs); this is a best-effort workflow guard,
    not a boundary, so a repo that can also write that file is out of threat
    model (documented in the README)."""
    cfg: dict = {}
    plugin_cfg = Path(__file__).resolve().parent.parent / "config.json"
    if plugin_cfg.exists():
        try:
            cfg.update(json.loads(plugin_cfg.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    repo_cfg = root / ".loop-config.json"
    if repo_cfg.exists():
        try:
            cfg.update(json.loads(repo_cfg.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def _audit(event: dict, root: Path, file_path: str, signals: list[str], mode: str) -> bool:
    """Append one JSON line to the audit log at the repo root (fallback: the
    plugin dir). Returns whether a line was written, so the caller can fall back
    to stderr when neither path is writable (a real install may make the plugin
    dir read-only, so this is best-effort, not guaranteed)."""
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
    for target in (root / ".loop-test-guard.log", fallback):
        try:
            with target.open("a") as fh:
                fh.write(line + "\n")
            return True
        except OSError:
            continue
    return False


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
    root = _repo_root(Path(event.get("cwd") or "."))
    cfg = _load_config(root)
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

    logged = _audit(event, root, file_path, signals, mode)
    reason = _message(file_path, signals, is_subagent)

    if mode == "block":
        # deny is the only permission decision this hook ever emits.
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

    # warn (default): never stall, and NEVER emit a permissionDecision -- an
    # explicit "allow" would suppress the user's ordinary edit-confirmation prompt
    # on exactly the edits this hook flags. The audit log is the channel to watch;
    # stderr carries the reason too (and is the only channel if the log write
    # failed). Matches gate_guard's "JSON only on deny" contract.
    if not logged:
        print(reason, file=sys.stderr)
    else:
        print(f"{reason} [logged to .loop-test-guard.log]", file=sys.stderr)
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    try:
        return main_from_event(event)
    except Exception as exc:  # noqa: BLE001
        # An advisory guard must never crash the user's edit. Fail OPEN (allow the
        # edit) rather than closed, and leave a breadcrumb on stderr; a guard bug
        # is not a reason to block legitimate work.
        print(f"dev-loop test-guard: internal error, not enforcing: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
