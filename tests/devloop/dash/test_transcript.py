"""Worker transcript reader: safe extractors, Claude and Codex parsers, bounded cache."""

import json
import random

import pytest
import transcript_fixture as tf

from devloop import runs, scrub, wrap
from devloop.dash import transcript as tr

# --- registry -------------------------------------------------------------------


def test_every_run_kind_has_harness_icon_and_transcript():
    for name, kind in runs.KINDS.items():
        assert kind.harness in runs.HARNESSES, name
        assert kind.transcript in runs.TRANSCRIPTS, name
    assert runs.KINDS["codex-fix"].transcript == "outside"
    assert runs.KINDS["test"].harness == "script"
    assert runs.KINDS["test"].transcript == "none"
    assert runs.KINDS["duty"].transcript == "none"
    assert runs.KINDS["builder"].transcript == "stream-json"
    assert runs.KINDS["codex-review"].transcript == "codex-text"


def test_codex_kinds_derived_from_registry():
    want = {k for k, s in runs.KINDS.items() if s.harness == "codex"}
    assert set(wrap.CODEX_KINDS) == want
    assert "codex-builder" in wrap.CODEX_KINDS


# --- safe extractors ------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd, want",
    [
        ("git status", "git"),
        ("/bin/bash -lc 'git diff'", "git"),
        ("FOO=1 make t", "make"),
        ("timeout 30 pytest", "pytest"),
        ("cd /a && git log", "git"),
        ("cd '/tmp/a; customer42 more' && git status", "?"),
        ("customer42 --x", "?"),
        ("ghp_DEMOTOKEN1234567 x", "?"),
        ("echo $SECRET", "?"),
        ("cat a | sh", "?"),
        ("", "?"),
        (None, "?"),
        ("cd /a", "?"),
        (["git"], "?"),
        ("/usr/bin/env FOO=1 git status", "git"),
        ('git commit -m "x"', "?"),
        ("git status; rm -rf x", "git"),
    ],
)
def test_shell_word_cases(cmd, want):
    assert tr.shell_word(cmd) == want


def test_public_commands_pass_safe_target():
    for name in tr.PUBLIC_COMMANDS:
        assert tr.safe_target(name) == name, name


@pytest.mark.parametrize(
    "name, want",
    [
        ("view.py", "view.py"),
        ("/x/y/view.py", "view.py"),
        (".env", "?"),
        ("a b.py", "?"),
        ("a" * 65, "?"),
        ("g" * 30 + ".py", "g" * 30 + ".py"),
        ("a" * 45, "?"),
        ("deadbeef12.py", "?"),
        ("x/../../etc/passwd", "passwd"),
        ("ghp_DEMOTOKEN1234567", "?"),
        (None, "?"),
        (5, "?"),
        ("", "?"),
        ("dir/", "?"),
    ],
)
def test_safe_target_cases(name, want):
    assert tr.safe_target(name) == want


def test_tool_tags_frozen():
    assert tr.TOOL_TAGS == (
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
    assert set(tr.TOOL_KINDS.values()) <= set(tr.TOOL_TAGS)


# --- Claude parser --------------------------------------------------------------


def parse(item):
    return tr.parse_claude_line(tf.claude_events([item]).strip())


def test_tool_kinds_and_targets_from_claude():
    f = parse(
        tf.assistant(
            "m1",
            tf.tool_use("a", "Read", file_path="/x/y/view.py"),
            tf.tool_use("b", "Grep", pattern="customer42"),
            tf.tool_use("c", "Agent", prompt="customer42", subagent_type="x"),
            tf.tool_use("d", "mcp__qmd__query", searches="customer42"),
            tf.tool_use("e", "Bash", command="git status"),
            tf.tool_use("f", "Write", file_path="/x/new.md"),
            tf.tool_use("g", "NotebookEdit", notebook_path="/x/n.ipynb"),
            tf.tool_use("h", "WebFetch", url="https://user:pw@host/x"),
            tf.tool_use("i", "Zork", x=1),
            tf.tool_use("j", "TodoWrite", todos=[]),
        )
    )
    got = [(c.tag, c.target, c.call_id) for c in f.calls]
    assert got == [
        ("READ", "view.py", "a"),
        ("SEARCH", "Grep", "b"),
        ("AGENT", None, "c"),
        ("MCP", None, "d"),
        ("SHELL", "git", "e"),
        ("WRITE", "new.md", "f"),
        ("EDIT", "n.ipynb", "g"),
        ("WEB", "WebFetch", "h"),
        ("OTHER", None, "i"),
        ("PLAN", "TodoWrite", "j"),
    ]


def test_init_gives_model_only():
    f = parse(tf.init(model="claude-opus-5-5", cwd="/home/someone/private-dir"))
    assert f.model == "claude-opus-5-5"
    assert not f.calls and not f.results and f.usage is None
    assert parse(tf.init(model="has space")).model is None
    assert parse(tf.init(model="ghp_DEMOTOKEN1234567")).model is None


def test_text_only_message_counts_tokens():
    f = parse(tf.assistant("m1", tf.text("hello"), out=42, inp=3, cache_read=100))
    assert f.calls == []
    assert f.usage == ("m1", 42, 103)


def test_two_results_with_different_error_flags():
    f = parse(tf.user(tf.tool_result("a", True), tf.tool_result("b", False)))
    assert f.results == [("a", True), ("b", False)]


def test_system_noise_and_result_lines_are_skipped():
    hook = {"type": "system", "subtype": "hook_response", "output": "x" * 5000}
    assert parse(hook) is None
    assert parse(tf.result("done")) is None


def test_invalid_utf8_and_junk_lines_are_ignored():
    for raw in (b"\xff\xfe\x00junk", b"not json", b"[1,2]", b"null", b"", b'{"type":'):
        assert tr.parse_claude_line(raw) is None


def test_wrong_types_in_tool_input_are_ignored():
    f = parse(
        {
            "type": "assistant",
            "message": {
                "id": 5,
                "usage": {"output_tokens": "9", "input_tokens": True},
                "content": [
                    {"type": "tool_use", "id": "a", "name": "Read", "input": [1]},
                    {
                        "type": "tool_use",
                        "id": "b",
                        "name": "Edit",
                        "input": {"file_path": 5},
                    },
                    {
                        "type": "tool_use",
                        "id": 7,
                        "name": "Bash",
                        "input": {"command": 1},
                    },
                    {"type": "tool_use", "id": "d", "name": None, "input": None},
                    "junk",
                ],
            },
        }
    )
    assert [(c.tag, c.target) for c in f.calls] == [("READ", "?"), ("EDIT", "?")]
    assert f.usage is None
    g = parse({"type": "user", "message": {"content": [{"type": "tool_result"}, 3]}})
    assert g.results == []
    assert parse({"type": "assistant", "message": []}) is None


def _mutated_lines(rng):
    base = [
        tf.assistant("m1", tf.tool_use("a", "Bash", command="git x"), out=5),
        tf.user(tf.tool_result("a", True)),
        tf.init(),
    ]
    for item in base:
        raw = bytearray(json.dumps(item).encode())
        for _ in range(rng.randint(0, 6)):
            op = rng.randint(0, 2)
            pos = rng.randrange(len(raw) + 1)
            if op == 0:
                raw[pos : pos + 1] = bytes([rng.randrange(256)])
            elif op == 1 and raw:
                del raw[min(pos, len(raw) - 1)]
            else:
                raw[pos:pos] = bytes(
                    rng.randrange(256) for _ in range(rng.randint(1, 4))
                )
        yield bytes(raw)
    yield bytes(rng.randrange(256) for _ in range(rng.randint(0, 80)))


def test_parser_never_raises_on_random_bytes():
    """Seeded fuzz: the same 500 cases on every run."""
    rng = random.Random(1234)
    for _ in range(500):
        for raw in _mutated_lines(rng):
            f = tr.parse_claude_line(raw)
            assert f is None or isinstance(f, tr.ClaudeFacts)


def test_scrub_agrees_with_target_rule():
    assert scrub.plain("view.py") == "view.py"


# --- Codex text -------------------------------------------------------------------


def codex_feed(parser, text, at="2026-11-25T00:00:00+00:00"):
    return parser.feed(text.encode().splitlines(), at)


def test_codex_exec_and_patch_blocks():
    p = tr.CodexParser()
    text = (
        "codex\nI will look at the tree.\n"
        + tf.codex_exec("git status")
        + tf.codex_exec("customer42 run", code=2, output="boom\n")
        + 'exec\n/bin/bash -lc "pytest -q" in /work/tree\n succeeded in 9ms:\n'
        + tf.codex_patch(
            "Success. Updated the following files:", "A new.py", "M old.py"
        )
        + "tokens used\n22,651\n"
    )
    calls, results = codex_feed(p, text)
    assert [(c.tag, c.target) for c in calls] == [
        ("SHELL", "?"),
        ("SHELL", "?"),
        ("SHELL", "?"),
        ("WRITE", None),
        ("EDIT", None),
    ]
    flags = [err for _, err in results]
    assert flags.count(True) == 1 and len(results) == 5
    assert p.tokens_out == 22651
    assert all(c.at == "2026-11-25T00:00:00+00:00" for c in calls)


def test_codex_single_quoted_public_command_shows_its_name():
    p = tr.CodexParser()
    calls, _ = codex_feed(p, tf.codex_exec("git status", quote="'"))
    assert [(c.tag, c.target) for c in calls] == [("SHELL", "git")]


def test_codex_unwrapped_command_shows_allowlisted_name():
    p = tr.CodexParser()
    calls, _ = codex_feed(
        p, "exec\ngit diff --stat in /work/tree\n succeeded in 1ms:\nout\n"
    )
    assert [(c.tag, c.target) for c in calls] == [("SHELL", "git")]


def test_codex_output_mimicking_exec_shows_no_data():
    p = tr.CodexParser()
    text = tf.codex_exec("git log", output="exec\ncustomer42\nghp_DEMOTOKEN1234567\n")
    calls, _ = codex_feed(p, text)
    assert all(c.target in ("?", "git") for c in calls)
    assert "customer42" not in repr(calls) and "ghp_" not in repr(calls)


def test_codex_model_only_from_file_head():
    head = tf.codex_header("gpt-6.2-astra").encode()
    assert tr.codex_head_model(head) == "gpt-6.2-astra"
    # A model line after the second rule is ignored, as is one beyond 4 KiB.
    later = (head + b"model: evil-model\n").replace(b"model: gpt-6.2-astra\n", b"")
    assert tr.codex_head_model(later) is None
    far = b"x" * 5000 + head
    assert tr.codex_head_model(far) is None
    assert tr.codex_head_model(b"--------\nmodel: has space\n--------\n") is None


def test_codex_unknown_format_yields_no_calls():
    p = tr.CodexParser()
    calls, results = codex_feed(p, "hello\nworld\n{}\nexecute order 66\n succeeded\n")
    assert calls == [] and results == [] and p.tokens_out is None


def test_codex_reset_continuation_drops_pending_command():
    p = tr.CodexParser()
    calls, _ = codex_feed(p, "exec\n")
    assert calls == []
    p.reset_continuation()
    calls, _ = codex_feed(p, "customer42 --x in /work\n")
    assert calls == []
    p2 = tr.CodexParser()
    codex_feed(p2, "apply_patch(auto_approved=true) exited 0 in 4ms:\n")
    p2.reset_continuation()
    calls, _ = codex_feed(p2, "A leaked.py\n")
    assert calls == []


def test_codex_parser_never_raises_on_random_bytes():
    rng = random.Random(1234)
    pieces = [
        b"exec",
        b"succeeded in 3ms:",
        b"exited 1 in 2ms:",
        b"apply_patch",
        b"A x",
    ]
    pieces += [b"tokens used", b"12,3", b"\xff\xfe", b"--------", b"codex"]
    for _ in range(500):
        lines = [
            rng.choice(pieces)
            + bytes(rng.randrange(256) for _ in range(rng.randint(0, 5)))
            for _ in range(rng.randint(1, 12))
        ]
        calls, results = tr.CodexParser().feed(lines, "t")
        assert isinstance(calls, list) and isinstance(results, list)


def test_codex_huge_token_count_is_ignored():
    p = tr.CodexParser()
    codex_feed(p, "tokens used\n" + "9" * 5000 + "\n")
    assert p.tokens_out is None
    codex_feed(p, "tokens used\n" + "9" * 18 + "\n")
    assert p.tokens_out == int("9" * 18)


# --- bounded reader and cache -------------------------------------------------------

import os  # noqa: E402

LABEL = ("Builder", "ROW--S1")


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def cache():
    return tr.Cache(clock=Clock())


def bash_line(call_id="a", cmd="git x", pad=0):
    item = tf.assistant(
        "m" + call_id, tf.tool_use(call_id, "Bash", command=cmd, x="g" * pad)
    )
    return tf.claude_events([item])


def padded(total, call_id="p"):
    """One valid line of exactly `total` bytes, newline included."""
    base = len(bash_line(call_id))
    assert total >= base
    line = bash_line(call_id, pad=total - base)
    assert len(line) == total
    return line


def read(cache, path, executing=True, transcript="stream-json"):
    return cache.read(path, transcript, executing, LABEL)


def test_first_read_is_bounded_by_tail_bytes(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "STEP_BYTES", 10**9)  # only the tail rule may cut the read
    p = tmp_path / "log"
    p.write_bytes(bash_line() * 20000)  # several MiB
    assert p.stat().st_size > 4 * tr.TAIL_BYTES
    st = read(cache, p)
    assert st.bytes_read <= tr.TAIL_BYTES + 1
    assert 0 < st.tools < 20000 and st.partial


def test_steady_poll_reads_only_new_bytes(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line("a"))
    st = read(cache, p)
    first = st.bytes_read
    read(cache, p)
    assert st.bytes_read == first  # unchanged file: no read
    extra = bash_line("b")
    with open(p, "ab") as fh:
        fh.write(extra)
    st = read(cache, p)
    assert st.bytes_read == first + len(extra) and st.tools == 2


def test_partial_last_line_waits_for_newline(cache, tmp_path):
    p = tmp_path / "log"
    line = bash_line("a")
    p.write_bytes(line[:-1])
    st = read(cache, p)
    assert st.tools == 0
    with open(p, "ab") as fh:
        fh.write(b"\n")
    assert read(cache, p).tools == 1


def test_aligned_tail_start_keeps_first_record(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "TAIL_BYTES", 400)
    p = tmp_path / "log"
    p.write_bytes(bash_line("old") * 5 + padded(400, "keep"))
    st = read(cache, p)
    assert [c.call_id for c in st.calls][0] == "keep"


def test_unaligned_tail_start_drops_the_cut_record(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "TAIL_BYTES", 400)
    p = tmp_path / "log"
    p.write_bytes(bash_line("old") * 5 + padded(401, "cut") + padded(350, "next"))
    st = read(cache, p)
    ids = [c.call_id for c in st.calls]
    assert "cut" not in ids and "next" in ids


def test_overlimit_line_across_polls_is_dropped(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "MAX_LINE", 1000)
    p = tmp_path / "log"
    p.write_bytes(b"x" * 600)
    st = read(cache, p)
    for _ in range(3):
        with open(p, "ab") as fh:
            fh.write(b"y" * 600)
        st = read(cache, p)
        assert len(st.pending) <= tr.MAX_LINE
    with open(p, "ab") as fh:
        fh.write(b"\n" + bash_line("good"))
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["good"] and st.partial


def test_overlimit_line_in_one_chunk_is_dropped(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "MAX_LINE", 1000)
    p = tmp_path / "log"
    p.write_bytes(bash_line("a") + b"z" * 1500 + b"\n" + bash_line("b"))
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["a", "b"] and st.partial


def test_tail_jump_clears_codex_continuation(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "STEP_BYTES", 400)
    monkeypatch.setattr(tr, "TAIL_BYTES", len(b"git status in /w\n"))
    p = tmp_path / "codex.out"
    p.write_bytes(b"exec\n")
    st = read(cache, p, transcript="codex-text")
    assert st.tools == 0
    with open(p, "ab") as fh:
        fh.write(b"filler line that is not a header\n" * 30 + b"git status in /w\n")
    st = read(cache, p, transcript="codex-text")
    assert st.tools == 0 and st.partial


def test_rotation_by_shorter_file(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line("old1") + bash_line("old2"))
    read(cache, p)
    new = tmp_path / "new"
    new.write_bytes(bash_line("fresh"))
    os.replace(new, p)
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["fresh"]
    with open(p, "ab") as fh:
        fh.write(bash_line("later"))
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["fresh", "later"]


def test_rotation_by_same_size_file(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line("aaaa"))
    read(cache, p)
    new = tmp_path / "new"
    new.write_bytes(bash_line("bbbb"))
    assert new.stat().st_size == p.stat().st_size
    os.replace(new, p)
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["bbbb"]
    with open(p, "ab") as fh:
        fh.write(bash_line("cccc"))
    assert [c.call_id for c in read(cache, p).calls] == ["bbbb", "cccc"]


def test_truncate_between_fstat_and_read_then_regrow(cache, tmp_path, monkeypatch):
    p = tmp_path / "log"
    p.write_bytes(bash_line("a"))
    read(cache, p)
    with open(p, "ab") as fh:
        fh.write(bash_line("b"))
    real = os.fstat
    big = len(bash_line("a")) + len(bash_line("b")) + len(bash_line("c"))

    def lying(fd):
        s = real(fd)
        return os.stat_result((*tuple(s)[:6], big, *tuple(s)[7:]))

    monkeypatch.setattr(tr.os, "fstat", lying)
    read(cache, p)  # fstat claims more bytes than the file holds
    monkeypatch.setattr(tr.os, "fstat", real)
    with open(p, "ab") as fh:
        fh.write(bash_line("c"))
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["a", "b", "c"]


def test_path_replaced_between_stat_and_open_reads_new_file_only(
    cache, tmp_path, monkeypatch
):
    p = tmp_path / "log"
    p.write_bytes(bash_line("old"))
    read(cache, p)
    new = tmp_path / "new"
    new_bytes = bash_line("n1") + bash_line("n2")
    new.write_bytes(new_bytes)
    new_ino = new.stat().st_ino
    real = os.stat

    def racing(path, *a, **k):
        with open(p, "ab") as fh:
            fh.write(bash_line("grown"))
        s = real(path, *a, **k)
        os.replace(new, p)
        return s

    monkeypatch.setattr(tr.os, "stat", racing)
    st = read(cache, p)
    monkeypatch.setattr(tr.os, "stat", real)
    assert st.ident[1] == new_ino and st.offset == len(new_bytes)
    assert [c.call_id for c in st.calls] == ["n1", "n2"]
    with open(p, "ab") as fh:
        fh.write(bash_line("n3"))
    assert [c.call_id for c in read(cache, p).calls] == ["n1", "n2", "n3"]


def test_unmatched_calls_keep_state_bounded(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(b"")
    clock = cache.clock
    for poll in range(50):
        with open(p, "ab") as fh:
            for i in range(20):
                fh.write(bash_line(f"c{poll}-{i}"))
        clock.t += 7
        st = read(cache, p)
    assert st.tools == 1000
    assert len(st.calls) == tr.KEEP_CALLS
    assert len(st.seconds) <= tr.RATE_SECONDS + 1
    assert len(st.minutes) <= tr.SPARK_MINUTES + 1
    assert len(st.pending) == 0


def test_rate_is_exact_across_a_minute_boundary(tmp_path):
    clock = Clock(1_700_000_000.0 - 1_700_000_000.0 % 60 - 30)  # 30 s before a boundary
    cache = tr.Cache(clock=clock)
    p = tmp_path / "log"
    p.write_bytes(b"")
    for sec in range(60):
        with open(p, "ab") as fh:
            for i in range(2 if sec < 20 else 1):
                fh.write(bash_line(f"s{sec}-{i}"))
        st = read(cache, p)
        clock.t += 1
    assert st.tools == 80
    assert st.rate(int(clock.t) - 1) == 80
    assert st.rate(int(clock.t) + 30) < 80  # the window moves on


def test_token_dedup_and_total_survive_eviction(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "TAIL_BYTES", 10**8)
    p = tmp_path / "log"
    items = [
        tf.assistant("same", tf.text("a"), out=5),
        tf.assistant("same", tf.text("a"), out=9),
    ]
    items += [tf.assistant(f"id{i}", tf.text("a"), out=1) for i in range(2500)]
    p.write_bytes(tf.claude_events(items))
    st = read(cache, p)
    assert st.token_total == 9 + 2500
    assert len(st.seen_tokens) <= tr.DEDUP_IDS


def test_results_set_the_running_flag_and_errors(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(
        bash_line("a") + tf.claude_events([tf.user(tf.tool_result("a", True))])
    )
    st = read(cache, p)
    call, running = st.doing()
    assert call.call_id == "a" and not running and st.errors == 1
    with open(p, "ab") as fh:
        fh.write(bash_line("b"))
    call, running = read(cache, p).doing()
    assert call.call_id == "b" and running


def test_model_from_init_and_codex_head(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(tf.claude_events([tf.init("claude-opus-5-5")]))
    assert read(cache, p).model == "claude-opus-5-5"
    q = tmp_path / "codex.out"
    q.write_bytes((tf.codex_header("gpt-6.2-astra") + tf.codex_exec("ls")).encode())
    st = read(cache, q, transcript="codex-text")
    assert st.model == "gpt-6.2-astra" and st.tools == 1


def test_missing_and_special_states(cache, tmp_path):
    p = tmp_path / "nope"
    assert read(cache, p, executing=True).state == "starting"
    assert read(cache, p, executing=False).state == "none"
    assert cache.read(None, "outside", True, LABEL).state == "outside"
    assert cache.read(None, "none", True, LABEL).state == "none"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_unreadable_file_marks_only_that_entry(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line())
    p.chmod(0)
    try:
        assert read(cache, p).state == "unreadable"
    finally:
        p.chmod(0o600)


def test_parser_error_on_one_line_keeps_the_next(cache, tmp_path, monkeypatch):
    p = tmp_path / "log"
    p.write_bytes(bash_line("a") + bash_line("b"))
    real = tr.parse_claude_line

    def flaky(raw):
        if b'"a"' in raw:
            raise RecursionError
        return real(raw)

    monkeypatch.setattr(tr, "parse_claude_line", flaky)
    st = read(cache, p)
    assert [c.call_id for c in st.calls] == ["b"]


def test_feed_log_keeps_facts_after_eviction(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line("a", "git x"))
    read(cache, p)
    assert len(cache.feed_log) == 1
    at, tag, target, role, key = cache.feed_log[0]
    assert (tag, target, role, key) == ("SHELL", "git", "Builder", "ROW--S1")
    cache.collect(set())
    assert p not in cache.entries and len(cache.feed_log) == 1


def test_cache_evicts_a_previously_cached_path(cache, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(bash_line("a"))
    b.write_bytes(bash_line("b"))
    read(cache, a)
    read(cache, b)
    assert a in cache.entries
    cache.collect({b})
    assert a not in cache.entries and b in cache.entries


def test_cache_lru_cap(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "CACHE_ENTRIES", 3)
    paths = []
    for i in range(5):
        p = tmp_path / f"f{i}"
        p.write_bytes(bash_line(f"c{i}"))
        paths.append(p)
        read(cache, p)
    assert list(cache.entries) == paths[2:]


def test_transcript_reader_never_writes(cache, tmp_path):
    p = tmp_path / "log"
    p.write_bytes(bash_line())
    before = (sorted(os.listdir(tmp_path)), p.stat().st_mtime_ns, p.read_bytes())
    read(cache, p)
    read(cache, p)
    after = (sorted(os.listdir(tmp_path)), p.stat().st_mtime_ns, p.read_bytes())
    assert before == after


def test_request_out_path_outside_run_dir_is_ignored(tmp_path):
    rd = tmp_path / "run"
    (rd / "out").mkdir(parents=True)
    elsewhere = tmp_path / "other" / "codex.out"
    req = {"kind": "codex-review", "out": str(elsewhere)}
    assert tr.transcript_path(rd, req) == rd / "out" / "codex.out"
    assert tr.transcript_path(rd, {"kind": "builder"}) == rd / "out" / "log"
    assert tr.transcript_path(rd, {"kind": "codex-fix"}) is None
    assert tr.transcript_path(rd, {"kind": "test"}) is None
    assert tr.transcript_path(rd, {"kind": "mystery"}) is None
