import json
import subprocess
import sys
from pathlib import Path

from hooks import board_update, loop_board as lb

REPO = Path(__file__).resolve().parent.parent
BOARD = str(REPO / "hooks" / "loop_board.py")

SHIPPED = """# Recently shipped

---

## 2026-10-26 (engine): cross-column PR1, release-hygiene, v0.14.2

Body text.

## 2026-10-25 (docs + web currency): D-1/D-2 refresh

More.

## 2026-10-20: untagged scope entry
"""

ROADMAP = """# Roadmap

## Current parallel assignment

| Lane | Owner | Slice | Branch | State |
|---|---|---|---|---|
| **Local** | orch | **A-4/B-5 file-access authorization** | feat/x | Rebase on main, gate both halves. |
| Remote | worker | **A-2 100M scale run** | bench | Handoff written. |

## Build order

### Milestone R1
- **A-1 Release hygiene.** DONE except the proof regen.
- **A-2 100M Synthea run.** Capture wall-clock.
- **A-3 Route page.** DONE (#305).
- **A-5 STORM rename.** Pre-GA rename.

## Recently shipped
- not a task bullet
"""


# ---- parsing -------------------------------------------------------------


def test_parse_shipped_takes_top_n_with_scope_and_date():
    cards = lb.parse_shipped(SHIPPED, count=2)
    assert len(cards) == 2
    assert cards[0]["date"] == "2026-10-26"
    assert cards[0]["scope"] == "engine"
    assert cards[0]["id"] == "2026-10-26-engine"
    assert "cross-column" in cards[0]["title"]


def test_parse_shipped_handles_missing_scope():
    cards = lb.parse_shipped(SHIPPED, count=9)
    assert cards[-1]["id"] == "2026-10-20"  # no scope -> date-only id


def test_parse_next_up_skips_done_and_scopes_to_section():
    cards = lb.parse_next_up(ROADMAP, "Build order", ["DONE"])
    ids = [c["id"] for c in cards]
    assert ids == ["A-2", "A-5"]  # A-1, A-3 are DONE; current-assignment not in section


def test_parse_current_assignment_reads_table_rows():
    cards = lb.parse_current_assignment(ROADMAP, "Current parallel assignment")
    ids = [c["id"] for c in cards]
    assert ids == ["A-4/B-5", "A-2"]
    assert cards[0]["lane"] == "Local"
    assert "Rebase" in cards[0]["note"]


# ---- phase derivation ----------------------------------------------------


def _state(**gates):
    g = {}
    for name, status in gates.items():
        g[name] = {"status": status}
    return {"gates": g, "remediation": {"open_findings": 0}}


def test_derive_phase_progression():
    assert lb.derive_phase(_state()) == "PLAN"
    assert lb.derive_phase(_state(plan_review="green")) == "DEVELOP"
    assert lb.derive_phase(_state(plan_review="green", dennis="green")) == "GATE"
    s = _state(plan_review="green")
    s["gates"]["dennis"] = {"status": "green"}
    s["gates"]["codex"] = {"status": "green"}
    assert lb.derive_phase(s) == "GATE"


def test_derive_phase_open_findings_is_remediate():
    s = _state(dennis="green")
    s["remediation"]["open_findings"] = 2
    assert lb.derive_phase(s) == "REMEDIATE"


def test_derive_phase_red_gate_is_remediate():
    assert lb.derive_phase(_state(dennis="red")) == "REMEDIATE"


# ---- refresh is non-destructive ------------------------------------------


def _project(tmp_path):
    (tmp_path / "ROADMAP.md").write_text(ROADMAP)
    (tmp_path / "SHIPPED.md").write_text(SHIPPED)
    return {
        "roadmap": "ROADMAP.md",
        "shipped_log": "SHIPPED.md",
        "current_section": "Current parallel assignment",
        "next_up_section": "Build order",
        "done_markers": ["DONE"],
        "recently_completed_count": 8,
        "next_up_count": 6,
        "repos": [],
    }


def test_refresh_seeds_columns(tmp_path):
    cfg = _project(tmp_path)
    board = lb.refresh(lb.empty_board(), cfg, tmp_path)
    cols = board["columns"]
    assert [c["id"] for c in cols["recently_completed"]][:1] == ["2026-10-26-engine"]
    assert {c["id"] for c in cols["current"]} == {"A-4/B-5", "A-2"}
    assert [c["id"] for c in cols["next_up"]] == ["A-5"]  # A-2 already in current
    assert board["generated_at"]


def test_refresh_preserves_authored_content(tmp_path):
    cfg = _project(tmp_path)
    board = lb.empty_board()
    board["questions"].append({"id": "q1", "title": "keep me"})
    board["columns"]["current"].append(
        {"id": "A-4/B-5", "title": "file-access", "note": "authored note", "phase": "VERIFY", "phase_locked": True}
    )
    board["columns"]["next_up"].append({"id": "CUSTOM-1", "title": "hand-added"})
    lb.refresh(board, cfg, tmp_path)
    assert board["questions"] == [{"id": "q1", "title": "keep me"}]
    card = next(c for c in board["columns"]["current"] if c["id"] == "A-4/B-5")
    assert card["note"] == "authored note"  # not overwritten
    assert card["phase"] == "VERIFY"  # locked phase survives
    assert card.get("lane") == "Local"  # but derived fields still fill in
    assert any(c["id"] == "CUSTOM-1" for c in board["columns"]["next_up"])


def test_refresh_overlays_loop_state_phase_when_unlocked(tmp_path):
    cfg = _project(tmp_path)
    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / ".loop-state.json").write_text(
        json.dumps({"slice": "A-2 100M scale run", "risk": "R2",
                    "gates": {"dennis": {"status": "green"}}, "remediation": {"open_findings": 0}})
    )
    cfg["repos"] = [str(sub)]
    board = lb.refresh(lb.empty_board(), cfg, tmp_path)
    card = next(c for c in board["columns"]["current"] if c["id"] == "A-2")
    assert card["phase"] == "GATE"  # derived from the live state, matched by title
    assert card["risk"] == "R2"


# ---- render --------------------------------------------------------------


def test_render_html_has_columns_and_escapes(tmp_path):
    board = lb.empty_board("My Board")
    board["columns"]["current"].append({"id": "x", "title": "a <script> b", "phase": "REVIEW"})
    board["questions"].append({"id": "q1", "title": "why?"})
    out = lb.render_html(board)
    assert "Recently completed" in out and "Current" in out and "Next up" in out
    assert "&lt;script&gt;" in out  # title escaped
    assert "Side discussions" in out
    assert out.count('class="ph on"') == 1  # exactly one current phase lit


def test_validate_rejects_bad_phase():
    board = lb.empty_board()
    board["columns"]["current"].append({"id": "x", "phase": "NOPE"})
    try:
        lb.validate_board(board)
        assert False, "expected BoardError"
    except lb.BoardError:
        pass


# ---- CLI -----------------------------------------------------------------


def _cli(tmp, *args):
    return subprocess.run(
        [sys.executable, BOARD, "--repo", str(tmp), *args],
        capture_output=True, text=True, check=False,
    )


def test_cli_init_render_question_phase(tmp_path):
    assert _cli(tmp_path, "init", "--title", "T").returncode == 0
    assert (tmp_path / ".loop-board.json").exists()
    # init refuses to clobber
    assert _cli(tmp_path, "init").returncode == 1
    # add a current card by hand, then exercise set-phase lock + add-question
    board = json.loads((tmp_path / ".loop-board.json").read_text())
    board["columns"]["current"].append({"id": "A-1", "title": "t"})
    (tmp_path / ".loop-board.json").write_text(json.dumps(board))
    assert _cli(tmp_path, "add-question", "is it ready?").returncode == 0
    assert _cli(tmp_path, "set-phase", "A-1", "VERIFY").returncode == 0
    board = json.loads((tmp_path / ".loop-board.json").read_text())
    card = board["columns"]["current"][0]
    assert card["phase"] == "VERIFY" and card["phase_locked"] is True
    assert board["questions"][0]["title"] == "is it ready?"
    r = _cli(tmp_path, "render", "--out", str(tmp_path / "b.html"))
    assert r.returncode == 0 and (tmp_path / "b.html").exists()


def test_cli_set_phase_unknown_card_fails(tmp_path):
    _cli(tmp_path, "init")
    assert _cli(tmp_path, "set-phase", "ghost", "PLAN").returncode == 1


def test_cli_move_between_columns(tmp_path):
    _cli(tmp_path, "init")
    board = json.loads((tmp_path / ".loop-board.json").read_text())
    board["columns"]["current"].append({"id": "A-1", "title": "t"})
    (tmp_path / ".loop-board.json").write_text(json.dumps(board))
    assert _cli(tmp_path, "move", "A-1", "recently_completed").returncode == 0
    board = json.loads((tmp_path / ".loop-board.json").read_text())
    assert not board["columns"]["current"]
    assert board["columns"]["recently_completed"][0]["id"] == "A-1"


# ---- hook ----------------------------------------------------------------


def test_hook_is_noop_without_board(tmp_path):
    # no .loop-board.json -> hook does nothing, no file created
    assert board_update.main_from_event({"cwd": str(tmp_path), "tool_name": "Stop"}) == 0
    assert not (tmp_path / "board.html").exists()


def test_hook_skips_irrelevant_bash(tmp_path):
    _cli(tmp_path, "init")
    rc = board_update.main_from_event(
        {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls -la"}}
    )
    assert rc == 0 and not (tmp_path / "board.html").exists()  # not triggered


def test_hook_refreshes_and_renders_on_trigger(tmp_path):
    cfg = _project(tmp_path)
    (tmp_path / ".loop-config.json").write_text(json.dumps({"board": cfg}))
    _cli(tmp_path, "init")
    rc = board_update.main_from_event(
        {"cwd": str(tmp_path), "tool_name": "Bash",
         "tool_input": {"command": "python loop_state.py record-gate dennis"}}
    )
    assert rc == 0
    assert (tmp_path / "board.html").exists()
    board = json.loads((tmp_path / ".loop-board.json").read_text())
    assert board["columns"]["recently_completed"]  # seeded from shipped log
    assert board["generated_at"]
