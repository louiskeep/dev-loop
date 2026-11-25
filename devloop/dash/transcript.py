"""Worker transcripts as safe facts: tool kind tags, allowlisted names, counts and times.

Everything a transcript contributes to the dashboard passes through `safe_target` or
`shell_word`. Prompts, tool results, model text and most tool input never leave this
module; a value that is not on an allowlist reads `?`.
"""

import collections
import dataclasses
import datetime
import json
import os
import re
import time
from pathlib import Path

from .. import runs, scrub, store

TAIL_BYTES = 524288  # the first read starts this far from the end
STEP_BYTES = 1048576  # most bytes read from one file per build
MAX_LINE = 2097152  # 2 MiB: a longer line is dropped before parsing
KEEP_CALLS = 50
SPARK_MINUTES = 15
RATE_SECONDS = 60
DEDUP_IDS = 2000
CACHE_ENTRIES = 64
FEED_LIMIT = 40

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


# --- bounded tail reader ----------------------------------------------------------

# One bad line never takes down a file; these are every error a line can raise.
_LINE_ERRORS = (ValueError, RecursionError, TypeError, KeyError)


class FileState:
    """What one worker output file has told us so far. Reset on a new file generation."""

    def __init__(self):
        self.ident: tuple | None = None
        self.offset = 0
        self.size = 0
        self.mtime_ns = 0
        self.pending = bytearray()
        self.skip_to_newline = False
        self.partial = False
        self.state = "starting"
        self.bytes_read = 0
        self.head_done = False
        self._reset_counters()

    def _reset_counters(self):
        self.calls: collections.deque = collections.deque(maxlen=KEEP_CALLS)
        self.tools = 0
        self.errors = 0
        self.newest_id: str | None = None
        self.newest_done = False
        self.token_total = 0
        self.seen_tokens: collections.OrderedDict = collections.OrderedDict()
        self.context: int | None = None
        self.model: str | None = None
        self.minutes: dict = {}
        self.seconds: dict = {}
        self.codex: CodexParser | None = None

    def new_generation(self):
        """A different or shorter file sits at the path: start over, keep the identity."""
        self.offset = 0
        self.pending = bytearray()
        self.skip_to_newline = False
        self.partial = True
        self.head_done = False
        self._reset_counters()

    def discontinuity(self):
        """Bytes were skipped, so half-read blocks and the running flag mean nothing."""
        self.pending = bytearray()
        self.partial = True
        self.newest_done = True
        if self.codex is not None:
            self.codex.reset_continuation()

    def doing(self):
        """The newest call and whether it is still running, or None."""
        if not self.calls:
            return None
        return self.calls[-1], not self.newest_done

    def rate(self, now_s: int) -> int:
        """Calls in the trailing RATE_SECONDS seconds, from one-second buckets."""
        return sum(
            n for s, n in self.seconds.items() if now_s - RATE_SECONDS < s <= now_s
        )

    def spark(self, now: float) -> list:
        """Calls per minute for the last SPARK_MINUTES minutes, oldest first."""
        cur = int(now // 60)
        return [self.minutes.get(m, 0) for m in range(cur - SPARK_MINUTES + 1, cur + 1)]

    def count(self, at_s: int):
        self.tools += 1
        self.seconds[at_s] = self.seconds.get(at_s, 0) + 1
        self.minutes[at_s // 60] = self.minutes.get(at_s // 60, 0) + 1
        for s in [s for s in self.seconds if s <= at_s - RATE_SECONDS - 1]:
            del self.seconds[s]
        for m in [m for m in self.minutes if m <= at_s // 60 - SPARK_MINUTES]:
            del self.minutes[m]


def transcript_path(rd, req: dict) -> Path | None:
    """Where a run's activity is written, from its kind. Never from the request."""
    kind = runs.KINDS.get(req.get("kind"))
    if kind is None:
        return None
    if kind.transcript == "stream-json":
        return store.out_dir(rd) / "log"
    if kind.transcript == "codex-text":
        return store.out_dir(rd) / "codex.out"
    return None


def _iso(t: float) -> str:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


class Cache:
    """Per-file state across polls. Callers serialize builds, so there is no lock."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.entries: collections.OrderedDict = collections.OrderedDict()
        self.feed_log: collections.deque = collections.deque(maxlen=FEED_LIMIT)

    def collect(self, keep: set) -> None:
        for path in [p for p in self.entries if p not in keep]:
            del self.entries[path]

    def read(self, path, transcript: str, executing: bool, label: tuple) -> FileState:
        if path is None or transcript in ("outside", "none"):
            st = FileState()
            st.state = "outside" if transcript == "outside" else "none"
            return st
        entry = self.entries.get(path)
        if entry is None:
            entry = self.entries[path] = FileState()
        self.entries.move_to_end(path)
        while len(self.entries) > CACHE_ENTRIES:
            self.entries.popitem(last=False)
        try:
            st = os.stat(path)
        except OSError:
            entry.state = "starting" if executing else "none"
            return entry
        if (
            entry.ident == (st.st_dev, st.st_ino)
            and entry.size == st.st_size
            and entry.mtime_ns == st.st_mtime_ns
        ):
            entry.state = "live"
            return entry
        try:
            with open(path, "rb") as fh:
                self._pull(entry, fh, transcript)
        except OSError:
            entry.state = "unreadable"
            return entry
        entry.state = "live"
        self._frame(entry, transcript, label)
        return entry

    def _pull(self, entry, fh, transcript):
        fs = os.fstat(fh.fileno())
        ident, end = (fs.st_dev, fs.st_ino), fs.st_size
        if entry.ident is not None and (ident != entry.ident or end < entry.offset):
            entry.new_generation()
        first = entry.offset == 0
        if transcript == "codex-text" and not entry.head_done:
            entry.head_done = True
            entry.bytes_read += HEAD_BYTES
            entry.model = codex_head_model(fh.read(HEAD_BYTES))
        if (first and end > TAIL_BYTES) or end - entry.offset > STEP_BYTES:
            start = end - TAIL_BYTES
            entry.discontinuity()
            fh.seek(start - 1)
            entry.skip_to_newline = fh.read(1) != b"\n"
            entry.bytes_read += 1
        else:
            start = entry.offset
        fh.seek(start)
        data = fh.read(max(0, end - start))
        # The cursor follows the bytes actually read: the file may have shrunk since fstat.
        entry.offset = start + len(data)
        entry.size, entry.mtime_ns, entry.ident = end, fs.st_mtime_ns, ident
        entry.bytes_read += len(data)
        entry.pending += data

    def _frame(self, entry, transcript, label):
        buf = bytes(entry.pending)
        entry.pending = bytearray()
        if entry.skip_to_newline:
            cut = buf.find(b"\n")
            if cut < 0:
                return
            buf, entry.skip_to_newline = buf[cut + 1 :], False
        *lines, last = buf.split(b"\n")
        for line in lines:
            if len(line) > MAX_LINE:
                entry.discontinuity()
                continue
            self._one(entry, line, transcript, label)
        if len(last) > MAX_LINE:
            entry.discontinuity()
            entry.skip_to_newline = True
        else:
            entry.pending = bytearray(last)

    def _one(self, entry, line, transcript, label):
        now = self.clock()
        at = _iso(now)
        try:
            if transcript == "codex-text":
                if entry.codex is None:
                    entry.codex = CodexParser()
                calls, results = entry.codex.feed([line], at)
                if entry.codex.tokens_out is not None:
                    entry.token_total = entry.codex.tokens_out
                facts = ClaudeFacts(calls=calls, results=results)
            else:
                facts = parse_claude_line(line)
        except _LINE_ERRORS:
            return
        if facts is None:
            return
        if facts.model:
            entry.model = facts.model
        if facts.usage:
            mid, out, ctx = facts.usage
            seen = entry.seen_tokens.get(mid, 0)
            if out > seen:
                entry.token_total += out - seen
                entry.seen_tokens[mid] = out
            if mid in entry.seen_tokens:
                entry.seen_tokens.move_to_end(mid)
            while len(entry.seen_tokens) > DEDUP_IDS:
                entry.seen_tokens.popitem(last=False)
            entry.context = ctx
        for call in facts.calls:
            call = call._replace(at=at)
            entry.calls.append(call)
            entry.count(int(now))
            entry.newest_id, entry.newest_done = call.call_id, False
            self.feed_log.append((at, call.tag, call.target, label[0], label[1]))
        for cid, failed in facts.results:
            entry.errors += failed
            if cid == entry.newest_id:
                entry.newest_done = True
