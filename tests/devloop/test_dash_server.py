"""Routes: what is served, what is refused, and what must never appear in a response."""

import json
import pathlib
import re
import sys


sys.path.insert(0, str(pathlib.Path(__file__).parent / "dash"))
import transcript_fixture as tf  # noqa: E402
from dash_fixture import AGENT_NAMES, call, dash, put_slice, srv  # noqa: F401

from devloop import notes, slices
from devloop.dash import server, view

ROUTES = ("/", "/fragment", "/api/state")
HEADER_NAMES = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Cache-Control",
)


def test_the_five_routes_answer(srv):
    for path, ctype in (
        ("/", "text/html"),
        ("/fragment", "text/html"),
        ("/api/state", "application/json"),
        ("/static/dash.css", "text/css"),
        ("/static/dash.js", "text/javascript"),
    ):
        status, headers, body = call(srv, path)
        assert status == 200 and headers["Content-Type"].startswith(ctype), path
        assert body
    assert json.loads(call(srv, "/api/state")[2])["schema"] == 2


def test_head_has_headers_and_no_body(srv):
    status, headers, body = call(srv, "/api/state", "HEAD")
    assert status == 200 and body == b"" and headers["ETag"]
    status, headers, body = call(srv, "/fragment", "HEAD")
    assert status == 200 and body == b"" and "ETag" not in headers


def test_etag_304_on_unchanged_poll(srv):
    _, h, _ = call(srv, "/api/state")
    status, _, body = call(srv, "/api/state", headers={"If-None-Match": h["ETag"]})
    assert status == 304 and body == b""
    # The pages carry the write token, so they are never revalidated.
    for path in ("/", "/fragment"):
        status, h2, _ = call(srv, path, headers={"If-None-Match": h["ETag"]})
        assert status == 200 and "ETag" not in h2 and h2["Cache-Control"] == "no-store"


def test_etag_changes_after_slice_move(proj, dash, srv):
    _, h, _ = call(srv, "/api/state")
    s = slices.load(proj, dash["frame"])
    s["phase"] = "PLAN"
    slices._save(proj, s)
    srv.cache._value = None  # the two second cache would hide the change
    status, h2, _ = call(srv, "/api/state", headers={"If-None-Match": h["ETag"]})
    assert status == 200 and h2["ETag"] != h["ETag"]


def test_the_view_is_cached_between_polls(srv, monkeypatch):
    calls = []
    real = view.build_view
    monkeypatch.setattr(
        view, "build_view", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    srv.cache._value = None
    for _ in range(5):
        call(srv, "/fragment")
    assert len(calls) == 1


def test_only_known_routes_and_methods(srv):
    for path in ("/nope", "/fragment/", "/api", "/static/", "/static/other.js"):
        assert call(srv, path)[:1] == (404,), path
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        status, _, body = call(srv, "/api/state", method)
        assert status == 501 and body == b"not supported", method
    assert call(srv, "/api/state", "POST")[:1] == (405,)
    assert call(srv, "/fragment?x=1&y=<script>")[0] == 200  # the query is ignored


def test_static_traversal_404(srv):
    for path in (
        "/static/../server.py",
        "/static/%2e%2e/server.py",
        "/static/..%2fserver.py",
        "/static/dash.css/../../server.py",
        "/static/../../../../etc/passwd",
    ):
        status, _, body = call(srv, path)
        assert status == 404 and body == b"not found", path


def test_every_response_sends_security_headers(srv):
    cases = [
        call(srv, "/"),
        call(srv, "/fragment"),
        call(srv, "/static/dash.js"),
        call(srv, "/nope"),
        call(srv, "/", "POST"),
        call(
            srv,
            "/api/state",
            headers={"If-None-Match": call(srv, "/api/state")[1]["ETag"]},
        ),
    ]
    assert {c[0] for c in cases} == {200, 404, 405, 304}
    for status, headers, _ in cases:
        for name in HEADER_NAMES:
            assert name in headers, (status, name)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "script-src 'self'" in headers["Content-Security-Policy"]


def test_a_broken_view_is_a_fixed_500_and_logs_only_the_class(srv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail /etc/shadow")

    monkeypatch.setattr(view, "build_view", boom)
    srv.cache._value = None
    status, headers, body = call(srv, "/fragment")
    assert (status, body) == (500, b"state unreadable")
    assert "Content-Security-Policy" in headers
    log = srv.log_text.getvalue()
    assert "RuntimeError" in log and "secret" not in log and "/etc" not in log


def test_log_has_method_path_and_status_only(srv):
    call(srv, "/fragment?token=abc123")
    log = srv.log_text.getvalue()
    assert "GET /fragment 200" in log and "abc123" not in log


def _plant(proj, dash):
    planted = "password hunter2 https://user:pw@host/x /etc/passwd ghp_PLANTEDSECRET1"
    for key in dash.values():
        try:
            slices.set_fields(
                proj, key, purpose=planted, next_action=planted, waiting_on=planted
            )
        except slices.SliceError:
            pass
    notes.ticklog(proj, planted)
    notes.problem(proj, "leaky", impact=planted, fix=planted, detail=planted)
    return planted


def test_routes_leak_nothing_planted(proj, dash, srv, tmp_path):
    _plant(proj, dash)
    for path in ROUTES:
        text = call(srv, path)[2].decode()
        # The questions file path appears only in the "Copy for Claude" text (owner request).
        text = re.sub(r'data-copy="[^"]*"', "", text)
        for needle in (
            "hunter2",
            "user:pw",
            "/etc/passwd",
            "PLANTEDSECRET",
            str(tmp_path),
            "space is short",
        ):
            assert needle not in text, (path, needle)
        assert not [
            w
            for w in text.split()
            if len(w) >= 40 and set(w) <= set("0123456789abcdef")
        ]


def test_token_in_id_is_redacted_on_all_routes(proj, dash, srv):
    put_slice(proj, "ghp_DEMOTOKEN123", "S1", "PLAN")
    notes.problem(proj, "ghp_DEMOTOKEN123", impact="x", fix="y")
    srv.cache._value = None
    for path in ROUTES:
        text = call(srv, path)[2].decode()
        assert "ghp_DEMOTOKEN" not in text, path
        assert "?" in text


def test_no_agent_names_in_output(proj, dash, srv):
    for path in ROUTES:
        text = call(srv, path)[2].decode()
        for name in (
            *AGENT_NAMES,
            "builder.live",
            "builder.done",
            "test.lost",
            "dennis",
        ):
            assert name not in text, (path, name)
    page = call(srv, "/")[2].decode()
    assert "Senior reviewer" in page and "Outside reviewer" in page


def test_server_never_runs_configured_commands(proj, dash, srv, cfg_edit, tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "cmd.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    cfg_edit(
        lambda c: c.update(
            snapshot_cmds=[{"name": "n", "argv": [str(script)]}],
            board={"cmd": [str(script)], "phase_map": {p: "x" for p in slices.PHASES}},
            notify={"cmd": [str(script)]},
        )
    )
    srv.cache._value = None
    for _ in range(10):
        for path in ROUTES:
            call(srv, path)
        srv.cache._value = None
    assert not marker.exists()


def test_the_server_writes_no_state(proj, dash, srv, home):
    def listing():
        return sorted(
            (str(p.relative_to(home)), p.stat().st_mtime_ns) for p in home.rglob("*")
        )

    before = listing()
    for path in ROUTES:
        call(srv, path)
    assert listing() == before


def test_make_server_installs_no_signal_handlers(proj, dash, monkeypatch):
    import signal

    def refuse(*a, **k):
        raise AssertionError("make_server touched signals")

    monkeypatch.setattr(signal, "signal", refuse)
    s = server.make_server(proj)
    s.server_close()


def test_html_routes_serve_200_and_schema_2(srv):
    """Every commit of the mission-control slice keeps both HTML routes serving."""
    from devloop.dash import render

    for path in ("/", "/fragment"):
        status, headers, body = call(srv, path)
        assert status == 200, path
        text = body.decode()
        for sid in render.SECTION_IDS:
            assert f'id="{sid}"' in text, (path, sid)
    assert json.loads(call(srv, "/api/state")[2])["schema"] == 2


# --- transcript facts are the only thing a transcript contributes ------------------

PLANTED_TRANSCRIPT = (
    "ghp_DEMOTOKEN1234567",
    "AKIAABCDEFGH1234",
    "hunter2",
    "user:pw@host",
    "/home/someone/private-dir",
    "0123456789abcdef0123456789abcdef01234567",
    "customer42",
)


def _plant_claude(proj, run_id, key, alive):
    from dash_fixture import put_run

    from devloop import store

    secret = " ".join(PLANTED_TRANSCRIPT)
    items = [
        tf.init("claude-opus-5-5", cwd="/home/someone/private-dir"),
        tf.assistant(
            "m1",
            tf.text(secret),
            tf.thinking(secret),
            tf.tool_use("a", "Bash", command="git status"),
            tf.tool_use("b", "Bash", command="customer42 --x ghp_DEMOTOKEN1234567"),
            tf.tool_use("c", "Bash", command="password hunter2 && AKIAABCDEFGH1234"),
            tf.tool_use(
                "d",
                "Edit",
                file_path="/home/someone/private-dir/view.py",
                old_string=secret,
                new_string=secret,
            ),
            tf.tool_use("e", "Write", file_path="/x/.env", content=secret),
            tf.tool_use("f", "Grep", pattern=secret, path="/home/someone/private-dir/"),
            tf.tool_use("g", "WebFetch", url="https://user:pw@host/x", prompt=secret),
            tf.tool_use("h", "Agent", prompt=secret, subagent_type="customer42"),
        ),
        tf.user(
            tf.tool_result("a", False, secret),
            tf.tool_result("b", True, secret),
        ),
        tf.result(secret),
    ]
    rd = put_run(proj, run_id, kind="builder", key=key, alive=alive)
    (store.out_dir(rd) / "log").write_bytes(tf.claude_events(items))
    return rd


def _plant_codex(proj, run_id, key):
    from dash_fixture import put_run

    from devloop import store

    secret = " ".join(PLANTED_TRANSCRIPT)
    text = (
        tf.codex_header("gpt-6.2-astra")
        + f"user\n{secret}\ncodex\n{secret}\n"
        + tf.codex_exec("git diff", output=f"{secret}\n")
        + tf.codex_exec(
            "customer42 --x ghp_DEMOTOKEN1234567", code=1, output=secret + "\n"
        )
        + tf.codex_patch(
            "Success. Updated the following files:", "M /home/someone/private-dir/x.py"
        )
    )
    rd = put_run(proj, run_id, kind="codex-review", key=key, alive=True)
    (store.out_dir(rd) / "codex.out").write_bytes(text.encode())


def test_transcript_secrets_reach_no_route(proj, dash, srv):
    from devloop import store

    _plant_claude(proj, "builder.leak", dash["plan"], alive=True)
    finished = _plant_claude(proj, "builder.fin", dash["gate"], alive=False)
    store.write_json(
        finished / "exit",
        {"code": 0, "reason": "exit", "ended_at": "2026-11-25T00:00:00+00:00"},
    )
    (finished / "applied").write_text("")
    _plant_codex(proj, "codex-review.leak", dash["review"])
    bodies = {}
    for path in ROUTES:
        status, _, body = call(srv, path)
        assert status == 200, path
        bodies[path] = body.decode()
    state = json.loads(bodies["/api/state"])
    # Positive facts: the page does show tool kinds, allowlisted names and counts.
    plan = next(c for c in state["live"] if c["key"] == dash["plan"])
    assert plan["tools"] == 8 and plan["model"] == "claude-opus-5-5"
    feed = [(f["tag"], f["target"]) for f in state["feed"] if f["role"]]
    assert ("SHELL", "git") in feed and ("EDIT", "view.py") in feed
    assert ("SHELL", "?") in feed
    codex = next(c for c in state["live"] if c["harness"] == "codex")
    assert codex["model"] == "gpt-6.2-astra" and codex["tools"] == 3
    gate = next(d for d in state["drawers"] if d["key"] == dash["gate"])
    assert {"name": "view.py", "edits": 1} in gate["files"]
    assert "SHELL" in bodies["/"] and "view.py" in bodies["/fragment"]
    for path, text in bodies.items():
        for value in PLANTED_TRANSCRIPT:
            assert value not in text, (path, value)
