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
