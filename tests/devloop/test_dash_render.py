"""The renderer: one page from one view, escaped, accessible and free of colour-only state."""

import copy
import re
import socket
from pathlib import Path

from dash_fixture import dash  # noqa: F401

from devloop import evidence, slices, status
from devloop.dash import render, view, words

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
    assert page.count('<tr class="slice"') == sum(len(r["cards"]) for r in v["rows"])
    assert 'href="https://github.com/own/main/pull/5"' in page
    assert "<script>" not in page  # no inline script; the one script is a file


def test_empty_project_shows_empty_states(proj):
    page = render.render_page(built(proj))
    for text in (
        "Nothing needs you right now.",
        "No slices in progress.",
        "Nothing has shipped yet.",
        "Nothing is waiting on other work.",
        "No known problems.",
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
    v["live"][0]["role"] = bad
    v["needs_you"][0]["key"] = bad
    v["needs_you"][0]["question"] = bad
    v["feed"].append(
        {
            "at": v["tick"]["last_pass_at"],
            "tag": "SHELL",
            "target": bad,
            "role": bad,
            "key": bad,
            "words": bad,
        }
    )
    v["live"][0]["key"] = bad
    v["live"][0]["model"] = bad
    v["drawers"][0]["calls"].append({"at": None, "tag": "READ", "target": bad})
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
    assert set(view.MEMORY_STATES) == set(render.MEMORY_STATE)
    assert set(words.STAGE) == set(slices.PHASES)
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


TAGS = (
    "read",
    "edit",
    "write",
    "shell",
    "search",
    "agent",
    "web",
    "plan",
    "mcp",
    "other",
    "event",
)


def _tokens(block):
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))


def _lum(h):
    r, g, b = (int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
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
        for tag in TAGS:
            assert _ratio(t[f"t-{tag}-fg"], t[f"t-{tag}-bg"]) >= 4.5, tag
        for fg in ("text", "muted", "accent"):
            for bg in ("surface", "bg"):
                assert _ratio(t[fg], t[bg]) >= 4.5, (fg, bg)


def test_css_light_and_dark_define_the_same_tokens():
    light, dark_media, dark_attr = _themes()
    assert set(light) == set(dark_media) == set(dark_attr)
    assert dark_media == dark_attr
    assert {f"t-{t}-{p}" for t in TAGS for p in ("fg", "bg")} <= set(light)


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
    assert "1 times" not in page


# --- mission control sections ------------------------------------------------------


def test_page_sections_in_order(proj, dash):
    frag = render.render_fragment(built(proj))
    found = re.findall(r'<section id="(\w+)" data-section', frag)
    assert found == list(render.SECTION_IDS)


def test_role_icons_are_a_fixed_set():
    from devloop.dash import live, render_live

    assert set(render_live.ICON_PATHS) == set(live.ICON_IDS)


def _big_view(proj):
    v = copy.deepcopy(built(proj))
    card = v["live"][0]
    v["live"] = [dict(card, ref=f"w{i:06d}", key=f"ROW1--S{i}") for i in range(16)]
    cards = [c for row in v["rows"] for c in row["cards"]]
    run = {
        "ref": "w000001",
        "role": "Developer",
        "icon": "build",
        "harness": "claude",
        "started_at": v["tick"]["last_pass_at"],
        "ended_at": v["tick"]["last_pass_at"],
        "summary": "built the fix",
        "status": "DONE",
        "verdict": "GO",
    }
    call = {"at": v["tick"]["last_pass_at"], "tag": "EDIT", "target": "view.py"}
    v["drawers"] = [
        {
            "key": cards[i % len(cards)]["key"],
            "runs": [run] * 8,
            "calls": [call] * 30,
            "files": [{"name": f"file{j}.py", "edits": j + 1} for j in range(20)],
        }
        for i in range(30)
    ]
    return v


def test_fragment_size_budget(proj, dash):
    frag = render.render_fragment(_big_view(proj))
    assert len(frag.encode()) < 200_000


def test_no_em_dash_in_assets_and_copy(proj, dash):
    dash_dir = WEB.parent
    texts = [p.read_text() for p in WEB.iterdir() if p.is_file()]
    texts += [p.read_text() for p in dash_dir.glob("*.py")]
    texts.append(render.render_page(built(proj)))
    for text in texts:
        assert "\u2014" not in text


def test_drawer_sources_capped_and_fallback_hooks(proj, dash):
    from dash_fixture import put_slice

    for i in range(61):
        put_slice(proj, "ROWX", f"S{i:02d}", "DEVELOP")
    page = render.render_page(built(proj))
    assert len(re.findall(r'<div id="ds-', page)) == 60
    keys = re.findall(r'data-card-key="([^"]+)"', page)
    sources = set(re.findall(r'<div id="ds-([^"]+)"', page))
    assert len(set(keys) - sources) >= 1  # a row with no source says it is not active
    assert 'id="drawer"' in page and 'id="drawer-body"' in page


def test_phone_rules_present():
    css = (WEB / "dash.css").read_text()
    phone = css[css.index("@media (max-width: 900px)") :]
    assert "attr(data-label)" in phone  # table rows stack as labelled lines
    assert ".drawer { position: fixed; inset: 0" in phone
    assert "width: 420px" in css
    assert "overflow-x: hidden" not in css  # no hidden horizontal scroll to mask a bug


def test_script_drives_the_drawer_without_inner_html():
    js = (WEB / "dash.js").read_text()
    assert "POLL_MS = 4000" in js
    for name in ("openDrawer", "closeDrawer", "fillDrawer"):
        assert f"function {name}" in js
    assert "cloneNode(true)" in js and "This slice is no longer active." in js
    assert "Escape" in js and "hashchange" in js
