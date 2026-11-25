"""Worker transcripts as safe facts: tool kind tags, allowlisted names, counts and times.

Everything a transcript contributes to the dashboard passes through `safe_target` or
`shell_word`. Prompts, tool results, model text and most tool input never leave this
module; a value that is not on an allowlist reads `?`.
"""

import collections
import dataclasses
import json
import re

from .. import scrub

MAX_LINE = 2097152  # 2 MiB: a longer line is dropped before parsing

TOOL_TAGS = (
    "READ",
    "EDIT",
    "WRITE",
    "SHELL",
    "SEARCH",
    "AGENT",
    "WEB",
    "PLAN",
    "MCP",
    "OTHER",
)

TOOL_KINDS = {
    "Read": "READ",
    "NotebookRead": "READ",
    "Edit": "EDIT",
    "MultiEdit": "EDIT",
    "NotebookEdit": "EDIT",
    "Write": "WRITE",
    "Bash": "SHELL",
    "bash": "SHELL",
    "BashOutput": "SHELL",
    "Monitor": "SHELL",
    "Grep": "SEARCH",
    "Glob": "SEARCH",
    "ToolSearch": "SEARCH",
    "Task": "AGENT",
    "Agent": "AGENT",
    "WebFetch": "WEB",
    "WebSearch": "WEB",
    "TodoWrite": "PLAN",
    "Skill": "PLAN",
    "ExitPlanMode": "PLAN",
}

# The only command names a page may show. Anything else is data until proven otherwise.
PUBLIC_COMMANDS = frozenset(
    """git gh python python3 uv uvx pip pytest ruff mypy node npm npx pnpm make cargo go
    rustc docker jq rg grep find fd ls cat sed head tail nl wc diff sort uniq mkdir cp mv
    rm touch chmod echo printf sleep curl tar bash sh codex claude devloop-state
    devloop-dash devloop-tick devloop-dispatch""".split()
)

Call = collections.namedtuple("Call", "tag target at call_id")

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_MODEL = re.compile(r"[a-z][a-z0-9.-]{1,48}")
_WRAPPED = re.compile(r"(?:/(?:usr/)?bin/)?(?:ba)?sh -l?c '([^']*)'")
_UNSAFE = re.compile(r"""['"\\$`<>|]""")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = ("env", "sudo", "nice", "nohup", "time", "timeout")
_TIMEOUT_ARG = re.compile(r"\d+[smhd]?")
_SEGMENT_END = re.compile(r";|\n")


def safe_target(name: object) -> str:
    """A file base name that passes the plain-ID rule, else `?`."""
    if not isinstance(name, str):
        return "?"
    part = name.rsplit("/", 1)[-1]
    if _NAME.fullmatch(part) and scrub.plain(part) == part:
        return part
    return "?"


def safe_model(name: object) -> str | None:
    if (
        isinstance(name, str)
        and _MODEL.fullmatch(name)
        and not scrub._TOKEN.search(name)
        and scrub.plain(name) == name
    ):
        return name
    return None


def shell_word(cmd: object) -> str:
    """The command name of a shell line when it is on `PUBLIC_COMMANDS`, else `?`."""
    if not isinstance(cmd, str):
        return "?"
    text = cmd[:400]
    wrapped = _WRAPPED.fullmatch(text)
    if wrapped:
        text = wrapped.group(1)
    for segment in text.split("&&")[:3]:
        first = _SEGMENT_END.split(segment, 1)[0]
        if _UNSAFE.search(first):
            return "?"
        tokens = first.split()
        while tokens:
            base = tokens[0].rsplit("/", 1)[-1]
            if _ASSIGN.match(tokens[0]) or base in _WRAPPERS:
                tokens.pop(0)
                if base == "timeout" and tokens and _TIMEOUT_ARG.fullmatch(tokens[0]):
                    tokens.pop(0)
            else:
                break
        if not tokens:
            return "?"
        word = tokens[0].rsplit("/", 1)[-1]
        if word == "cd":
            continue
        return word if word in PUBLIC_COMMANDS else "?"
    return "?"


# --- Claude stream-json ----------------------------------------------------------


@dataclasses.dataclass
class ClaudeFacts:
    calls: list = dataclasses.field(default_factory=list)
    results: list = dataclasses.field(default_factory=list)
    model: str | None = None
    usage: tuple | None = None


def _tool_tag(name: object) -> str:
    if not isinstance(name, str):
        return "OTHER"
    if name.startswith("mcp__"):
        return "MCP"
    return TOOL_KINDS.get(name, "OTHER")


def _call_target(tag: str, name: str, inp: object):
    inp = inp if isinstance(inp, dict) else {}
    if tag in ("READ", "EDIT", "WRITE"):
        return safe_target(inp.get("file_path") or inp.get("notebook_path"))
    if tag == "SHELL":
        return shell_word(inp.get("command"))
    if tag in ("SEARCH", "WEB", "PLAN"):
        return name
    return None


def _int(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _usage(msg: dict):
    mid, usage = msg.get("id"), msg.get("usage")
    if not isinstance(mid, str) or not isinstance(usage, dict):
        return None
    context = sum(
        _int(usage.get(k))
        for k in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
    return (mid, _int(usage.get("output_tokens")), context)


def parse_claude_line(raw: bytes) -> ClaudeFacts | None:
    """Facts from one stream-json line, or None when the line carries none."""
    if len(raw) > MAX_LINE:
        return None
    # Hook output and progress lines are large and never matter here.
    if raw.startswith((b'{"type":"system"', b'{"type": "system"')) and (
        b'"subtype":"init"' not in raw[:60] and b'"subtype": "init"' not in raw[:60]
    ):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind == "system":
        if obj.get("subtype") != "init":
            return None
        return ClaudeFacts(model=safe_model(obj.get("model")))
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    blocks = content if isinstance(content, list) else []
    facts = ClaudeFacts()
    if kind == "assistant":
        facts.usage = _usage(msg)
        for b in blocks:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            cid, name = b.get("id"), b.get("name")
            if not isinstance(cid, str) or not isinstance(name, str):
                continue
            tag = _tool_tag(name)
            facts.calls.append(
                Call(tag, _call_target(tag, name, b.get("input")), None, cid)
            )
        return facts
    if kind == "user":
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                cid = b.get("tool_use_id")
                if isinstance(cid, str):
                    facts.results.append((cid, b.get("is_error") is True))
        return facts
    return None


# --- Codex text ------------------------------------------------------------------

_DONE = re.compile(r"succeeded in \d+\s*ms:?")
_EXITED = re.compile(r"exited (-?\d+) in \d+\s*ms:?")
_PATCH_LINE = re.compile(r"([AMD]) \S")
_DIRECTORY = re.compile(r" in /\S*$")
_COUNT = re.compile(r"[\d,]+")
_BLOCK_HEADS = ("codex", "thinking", "user")
HEAD_BYTES = 4096


def codex_head_model(head: bytes) -> str | None:
    """The model line between the first two rules of the file head, never a tail."""
    lines = head[:HEAD_BYTES].decode("utf-8", "replace").splitlines()
    rules = [i for i, line in enumerate(lines) if line.strip() == "--------"]
    if len(rules) < 2:
        return None
    for line in lines[rules[0] + 1 : rules[1]]:
        if line.startswith("model:"):
            return safe_model(line.partition(":")[2].strip())
    return None


class CodexParser:
    """Codex `exec` text mixes commands with their output, so only block headers count.

    Output that imitates a header can add a fake call, but a call can only carry an
    allowlisted name, so the worst case is a wrong count.
    """

    def __init__(self):
        self.tokens_out: int | None = None
        self._n = 0
        self.reset_continuation()

    def reset_continuation(self) -> None:
        """Forget half-read blocks after any gap in the byte stream."""
        self._want_command = False
        self._open: str | None = None
        self._in_patch = False
        self._want_tokens = False

    def _next_id(self) -> str:
        self._n += 1
        return f"cx{self._n}"

    def feed(self, lines: list, seen_at: str) -> tuple[list, list]:
        calls: list = []
        results: list = []
        for raw in lines:
            line = raw.decode("utf-8", "replace").strip()
            if self._want_tokens:
                self._want_tokens = False
                if _COUNT.fullmatch(line):
                    digits = line.replace(",", "")
                    # Python refuses int() past 4300 digits; a real count is far shorter.
                    ok = digits.isdigit() and len(digits) <= 18
                    self.tokens_out = int(digits) if ok else None
                    continue
            if self._want_command:
                if not line:
                    continue
                self._want_command = False
                cid = self._next_id()
                word = shell_word(_DIRECTORY.sub("", line))
                calls.append(Call("SHELL", word, seen_at, cid))
                self._open = cid
                continue
            if line == "exec":
                self._want_command, self._in_patch = True, False
                continue
            done, exited = _DONE.fullmatch(line), _EXITED.fullmatch(line)
            if done or exited:
                if self._open is not None:
                    failed = bool(exited) and exited.group(1) != "0"
                    results.append((self._open, failed))
                    self._open = None
                self._in_patch = False
                continue
            if line.startswith("apply_patch") or line in (
                "file update",
                "file update:",
            ):
                self._in_patch = True
                continue
            if line == "tokens used":
                self._want_tokens, self._in_patch = True, False
                continue
            if self._in_patch:
                hit = _PATCH_LINE.match(line)
                if hit:
                    cid = self._next_id()
                    tag = "WRITE" if hit.group(1) == "A" else "EDIT"
                    calls.append(Call(tag, None, seen_at, cid))
                    results.append((cid, False))
                elif not line or line in _BLOCK_HEADS:
                    self._in_patch = False
        return calls, results
