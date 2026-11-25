"""Owner rule: code written by Codex is also reviewed by a dennis-kind reviewer first."""

import pytest
from runs_support import (  # noqa: F401
    brief,
    config,
    env,
    git,
    go,
    make_repo,
    ready,
)

from devloop import runs, slices


def _code_items(proj, key):
    return [
        label
        for label, _ in slices.exit_checklist(proj, key, "GATE")
        if label.startswith("code review")
    ]


def test_r1_slice_fixed_by_codex_builder_needs_dennis_then_codex(
    proj, ready, mk_run, give
):
    key, _, head = ready("REVIEW", risk="R1", round=4)
    mk_run(proj, "cb1", kind="codex-builder", key=key, head=head)
    assert _code_items(proj, key) == [
        "code review dennis GO on head",
        "code review codex GO on head",
    ]
    d = give(proj, key, "code:dennis", "d1")
    give(proj, key, "code:codex", "c1", pred=d)
    assert _code_items(proj, key) == [
        "code review dennis GO on head",
        "code review codex GO on head",
    ]
    assert all(
        ok
        for label, ok in slices.exit_checklist(proj, key, "GATE")
        if "code review" in label
    )


def test_codex_without_dennis_go_is_not_enough(proj, ready, mk_run, give):
    key, _, head = ready("REVIEW", risk="R1", round=4)
    mk_run(proj, "cb1", kind="codex-builder", key=key, head=head)
    give(proj, key, "code:codex", "c1")
    missing = [n for n, ok in slices.exit_checklist(proj, key, "GATE") if not ok]
    assert any(n.startswith("code review dennis") for n in missing)


def test_r1_slice_fixed_by_a_claude_builder_needs_only_codex(proj, ready, mk_run):
    key, _, head = ready("REVIEW", risk="R1", round=2)
    mk_run(proj, "b1", kind="builder", key=key, head=head)
    assert _code_items(proj, key) == ["code review codex GO on head"]


def test_dispatch_orders_dennis_before_codex_on_a_codex_written_head(
    proj, ready, brief, mk_run
):
    key, _, head = ready("REVIEW", risk="R1", round=4)
    mk_run(proj, "cb1", kind="codex-builder", key=key, head=head)
    with pytest.raises(runs.Refused, match="dennis has no applied GO"):
        go(proj, brief, "codex-review", key, reviewer="codex")
    assert go(proj, brief, "dennis", key, reviewer="dennis")


def test_dennis_is_refused_on_a_claude_written_r1_head(proj, ready, brief, mk_run):
    key, _, head = ready("REVIEW", risk="R1", round=2)
    mk_run(proj, "b1", kind="builder", key=key, head=head)
    with pytest.raises(runs.Refused, match="not in the R1 code roster"):
        go(proj, brief, "dennis", key, reviewer="dennis")


def test_dennis_listed_later_moves_first_without_a_duplicate(
    proj, ready, mk_run, cfg_edit
):
    cfg_edit(lambda c: c["reviews"]["R1"].update(code=["codex", "dennis"]))
    key, _, head = ready("REVIEW", risk="R1", round=4)
    mk_run(proj, "cb1", kind="codex-builder", key=key, head=head)
    assert _code_items(proj, key) == [
        "code review dennis GO on head",
        "code review codex GO on head",
    ]
