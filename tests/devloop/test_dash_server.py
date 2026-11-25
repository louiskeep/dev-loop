"""Routes: what is served, what is refused, and what must never appear in a response."""

import http.client
import io
import json
import threading

import pytest
from dash_fixture import AGENT_NAMES, dash, put_slice  # noqa: F401

from devloop import notes, slices
from devloop.dash import server, view

ROUTES = ("/", "/fragment", "/api/state")
HEADER_NAMES = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Cache-Control",
)


@pytest.fixture
def srv(proj, dash):
    log = io.StringIO()
    s = server.make_server(proj, "127.0.0.1", 0, proc="/nonexistent-proc", log=log)
    s.log_text = log
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def call(srv, path, method="GET", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    try:
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


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
    assert json.loads(call(srv, "/api/state")[2])["schema"] == 1


def test_head_has_headers_and_no_body(srv):
    status, headers, body = call(srv, "/fragment", "HEAD")
    assert status == 200 and body == b"" and headers["ETag"]


def test_etag_304_on_unchanged_poll(srv):
    _, h, _ = call(srv, "/fragment")
    status, h2, body = call(srv, "/fragment", headers={"If-None-Match": h["ETag"]})
    assert status == 304 and body == b""
    status, h3, _ = call(srv, "/api/state")
    assert h3["ETag"] == h["ETag"]  # one version for the page, the fragment and the API
    assert call(srv, "/api/state", headers={"If-None-Match": h["ETag"]})[0] == 304


def test_etag_changes_after_slice_move(proj, dash, srv):
    _, h, _ = call(srv, "/fragment")
    s = slices.load(proj, dash["frame"])
    s["phase"] = "PLAN"
    slices._save(proj, s)
    srv.cache._value = None  # the two second cache would hide the change
    status, h2, _ = call(srv, "/fragment", headers={"If-None-Match": h["ETag"]})
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
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        status, _, body = call(srv, "/api/state", method)
        assert status == 501 and body == b"not supported", method
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
            "/fragment",
            headers={"If-None-Match": call(srv, "/fragment")[1]["ETag"]},
        ),
    ]
    assert {c[0] for c in cases} == {200, 404, 501, 304}
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
