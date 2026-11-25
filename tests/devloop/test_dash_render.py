"""The renderer: one page from one view, escaped, accessible and free of colour-only state."""

import copy
import re
import socket
from pathlib import Path

from dash_fixture import dash  # noqa: F401

from devloop import evidence, status
from devloop.dash import render, view

WEB = Path(view.__file__).parent / "web"


def built(proj, tmp_path=None):
    return view.build_view(proj, proc="/nonexistent-proc")


def test_page_renders_from_fixture_offline(proj, dash, monkeypatch):
    v = built(proj)

    def no_network(*a, **k):
        raise AssertionError("the renderer touched the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    page = render.render_page(v)
    for sid in render.SECTION_IDS:
        assert f'id="{sid}"' in page, sid
    assert page.count('aria-current="step"') == sum(len(r["cards"]) for r in v["rows"])
    assert "met</summary>" in page and re.search(r"\d+/\d+ met", page)
    assert 'href="https://github.com/own/main/pull/5"' in page
    for label in ("Developer", "Senior reviewer", "Tech lead", "Product owner (you)"):
        assert label in page
    assert "Developers" in page and "QA" in page
    assert "<script>" not in page  # no inline script; the one script is a file


def test_empty_project_shows_empty_states(proj):
    page = render.render_page(built(proj))
    for text in (
        "Nothing needs you right now.",
        "No slices in progress.",
        "No workers running.",
        "Nothing has shipped yet.",
        "No recent activity.",
        "Nothing is parked or held.",
        "Memory is not readable here.",
    ):
        assert text in page


def test_fragment_is_the_page_body_only(proj, dash):
    v = built(proj)
    frag = render.render_fragment(v)
    assert frag.startswith('<div id="app-body"') and "<html" not in frag
    assert frag in render.render_page(v)
    assert f'data-version="{v["version"]}"' in frag


def test_html_escapes_planted_markup(proj, dash):
    v = copy.deepcopy(built(proj))
    bad = '<script>alert(1)</script>"><img src=x onerror=y>'
    v["project"] = bad
    v["rows"][0]["row"] = bad
    v["rows"][0]["cards"][0]["slice"] = bad
    v["rows"][0]["cards"][0]["owner"] = bad
    v["rows"][0]["cards"][0]["url"] = 'https://x/"><script>1</script>'
    v["workers"][0]["role"] = bad
    v["needs_you"][0]["key"] = bad
    v["activity"].append(
        {"at": v["tick"]["last_pass_at"], "kind": "merged", "words": bad}
    )
    page = render.render_page(v)
    assert "<script>alert" not in page and "<img src=x" not in page
    assert "onerror=y>" not in page.replace("&gt;", "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '"><script>1' not in page


def test_every_code_and_word_has_wording():
    assert set(evidence.CODES) <= set(render.CODE_TEXT)
    assert set(evidence.REASON_WORDS) <= set(render.REASON_TEXT)
    assert set(view.NEEDS) == set(render.NEED_TEXT)
    assert set(view.RUN_STATES) == set(render.WORKER_STATE)
    assert set(view.MEMORY_STATES) == set(render.MEMORY_STATE)
    assert set(render.PHASE_TEXT) == set(view.CHIPS)
    assert set(status.EVENT_KINDS)  # activity wording comes from status


def test_state_colour_sits_only_on_chips_with_an_icon_and_a_word(proj, dash):
    page = render.render_page(built(proj))
    # Every st-* class is on a chip, and every chip has an icon and non-empty text.
    assert len(re.findall(r"st-(?:ok|warn|bad|info|idle)", page)) == len(
        re.findall(r'<span class="chip st-', page)
    )
    for m in re.finditer(
        r'<span class="chip st-\w+"[^>]*>(.*?)</span></span>|<span class="chip st-\w+"[^>]*>(.*?)</span>',
        page,
    ):
        body = m.group(1) or m.group(2) or ""
        assert 'class="ico" aria-hidden="true"' in body
        assert re.sub(r"<[^>]+>", "", body).strip(), body


def test_page_keeps_csp_friendly(proj, dash):
    page = render.render_page(built(proj))
    assert "style=" not in page and "javascript:" not in page
    assert page.count("<script") == 1 and 'src="/static/dash.js"' in page


def test_memory_panel_words(proj, dash, tmp_path):
    from test_dash_view import make_proc

    root = tmp_path / "proc"
    make_proc(root, avail_kb=2 * 1024 * 1024)
    page = render.render_page(view.build_view(proj, proc=str(root)))
    assert "Low" in page and "headroom" in page and "reserved for recent starts" in page


def _tokens(block):
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))


def _lum(h):
    r, g, b = (int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _themes():
    css = (WEB / "dash.css").read_text()
    blocks = re.findall(r"(:root[^{]*)\{([^}]*)\}", css)
    themes = [_tokens(body) for sel, body in blocks if "--ok-fg" in body]
    assert len(themes) == 3  # light, dark by media query, dark by attribute
    return themes


def test_css_tokens_meet_aa():
    for t in _themes():
        for state in ("ok", "warn", "bad", "info", "idle"):
            assert _ratio(t[f"{state}-fg"], t[f"{state}-bg"]) >= 4.5, state
        for fg in ("text", "muted", "accent"):
            for bg in ("surface", "bg"):
                assert _ratio(t[fg], t[bg]) >= 4.5, (fg, bg)


def test_css_light_and_dark_define_the_same_tokens():
    light, dark_media, dark_attr = _themes()
    assert set(light) == set(dark_media) == set(dark_attr)
    assert dark_media == dark_attr


def test_static_assets_have_no_inner_html_from_data():
    js = (WEB / "dash.js").read_text()
    assert "innerHTML" not in js and "eval(" not in js and "document.write" not in js


def test_every_page_state_is_a_chip_not_a_row_colour():
    css = (WEB / "dash.css").read_text()
    for sel in re.findall(r"([^{}]+)\{", css):
        if "st-" in sel:
            assert sel.strip().startswith(".st-"), sel  # only the chip classes


def test_counts_read_naturally(proj, dash):
    page = render.render_page(built(proj))
    assert "seen 1 time)" in page and "1 times" not in page
