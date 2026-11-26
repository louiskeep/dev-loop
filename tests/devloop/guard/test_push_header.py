"""The pushed SHA reaches the result header however the worker wrote it."""

import pytest

from devloop import runs, store, wrap
from test_push import commit, remote_head, repo  # noqa: F401
from test_wrap import mk


def run_with_header(proj, tmp_path, wt, header_lines, sep="---"):
    """A writer whose result.md has exactly these header lines."""
    text = "\\n".join(header_lines)
    rd = mk(
        proj,
        tmp_path,
        [
            "sh",
            "-c",
            f"printf '{text}\\n{sep}\\nok\\n' > $DEVLOOP_RUN_DIR/result.md",
        ],
        kind="builder",
        worktree=str(wt),
        branch="feat/s",
        bases="main,master",
    )
    wrap.run(proj, "r1")
    return rd


@pytest.mark.parametrize(
    "lines",
    [
        ["status: DONE", "head : STALE"],
        ["status: DONE", "head: STALE", "head: OLDER"],
        ["status: DONE"],
        ["status: DONE", "head:STALE", "tests: green"],
    ],
    ids=["spaced-key", "duplicate", "absent", "no-space"],
)
def test_confirmed_sha_is_the_only_head_the_reader_sees(proj, tmp_path, repo, lines):
    wt, remote = repo
    sha = commit(wt)
    rd = run_with_header(proj, tmp_path, wt, lines)
    assert remote_head(remote) == sha
    res = runs.parse_result(rd, "builder")
    assert (res.status, res.head) == ("DONE", sha)
    text = (store.out_dir(rd) / "result.md").read_text()
    assert text.count("head") == 1 and "STALE" not in text and "OLDER" not in text
    assert text.endswith("---\nok\n")


def test_a_separator_with_trailing_spaces_keeps_the_body(proj, tmp_path, repo):
    wt, _ = repo
    sha = commit(wt)
    rd = run_with_header(proj, tmp_path, wt, ["status: DONE", "head: STALE"], "---  ")
    res = runs.parse_result(rd, "builder")
    assert (res.head, res.summary) == (sha, "ok")


def test_a_stale_head_before_the_correct_one_leaves_exactly_one(proj, tmp_path, repo):
    wt, _ = repo
    sha = commit(wt)
    rd = run_with_header(
        proj, tmp_path, wt, ["status: DONE", "head: STALE", f"head: {sha}"]
    )
    text = (store.out_dir(rd) / "result.md").read_text()
    assert text.count("head:") == 1 and "STALE" not in text


def test_result_publication_failure_sets_a_failed_exit(
    proj, tmp_path, repo, monkeypatch
):
    wt, remote = repo
    sha = commit(wt)
    real = store.write_text

    def flaky(path, text):
        if str(path).endswith("result.md") and f"head: {sha}" in text:
            raise OSError("disk full")
        return real(path, text)

    monkeypatch.setattr(store, "write_text", flaky)
    rd = run_with_header(proj, tmp_path, wt, ["status: DONE", f"head: {'0' * 40}"])
    ex = store.read_json(rd / "exit")
    assert ex["code"] != 0
    assert runs.parse_result(rd, "builder").status == "FAILED"


def test_invalid_utf8_fails_publication_and_preserves_result_bytes(
    proj, tmp_path, repo
):
    wt, _ = repo
    sha = commit(wt)
    rd = mk(proj, tmp_path, ["true"], worktree=str(wt), branch="feat/s", bases="main")
    path = store.out_dir(rd) / "result.md"
    path.parent.mkdir(exist_ok=True)
    original = f"status: DONE\nhead: {sha}\n---\n".encode() + b"caf\xe9"
    path.write_bytes(original)
    wrap.run(proj, "r1")
    assert store.read_json(rd / "exit")["code"] != 0
    assert runs.parse_result(rd, "builder").status == "FAILED"
    assert path.read_bytes() == original


def test_successful_publication_preserves_crlf_body_bytes(proj, tmp_path, repo):
    wt, remote = repo
    sha = commit(wt)
    rd = mk(proj, tmp_path, ["true"], worktree=str(wt), branch="feat/s", bases="main")
    path = store.out_dir(rd) / "result.md"
    path.parent.mkdir(exist_ok=True)
    body = "café\r\n\r\nlast line".encode()
    path.write_bytes(f"status: DONE\nhead: stale\nhead: {sha}\n---\n".encode() + body)

    wrap.run(proj, "r1")

    assert store.read_json(rd / "exit")["code"] == 0
    assert remote_head(remote) == sha
    assert path.read_bytes() == f"status: DONE\nhead: {sha}\n---\n".encode() + body
