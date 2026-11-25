import subprocess

import pytest

from devloop import slices

pytestmark = pytest.mark.real_git

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


@pytest.fixture
def repo(proj, tmp_path, cfg_edit):
    path = tmp_path / "gitrepo"
    path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["commit", "-q", "--allow-empty", "-m", "c1"],
    ):
        subprocess.run([*GIT, "-C", str(path), *args], check=True)
    cfg_edit(lambda c: c["repos"][0].update(path=str(path)))
    sha = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return path, sha


def test_set_fields_stores_the_full_sha(proj, mk_slice, repo):
    path, sha = repo
    k = mk_slice(proj, phase="DEVELOP", risk="R1")
    for name in (sha[:8], "main", "HEAD", sha):
        assert slices.set_fields(proj, k, head=name)["head"] == sha


def test_set_fields_refuses_a_head_that_is_not_a_commit(proj, mk_slice, repo):
    k = mk_slice(proj, phase="DEVELOP", risk="R1")
    for bad in ("deadbeef", "not-a-ref", "a1"):
        with pytest.raises(slices.SliceError, match="not a commit"):
            slices.set_fields(proj, k, head=bad)
    assert slices.load(proj, k)["head"] is None


def test_set_fields_resolves_in_the_slice_worktree(proj, mk_slice, repo, tmp_path):
    other = tmp_path / "wt"
    other.mkdir()
    subprocess.run([*GIT, "-C", str(other), "init", "-q", "-b", "x"], check=True)
    subprocess.run(
        [*GIT, "-C", str(other), "commit", "-q", "--allow-empty", "-m", "o"], check=True
    )
    sha = subprocess.run(
        ["git", "-C", str(other), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    k = mk_slice(proj, phase="DEVELOP", risk="R1", worktree=str(other))
    assert slices.set_fields(proj, k, head=sha[:10])["head"] == sha


def test_import_resolves_the_head(proj, repo, monkeypatch):
    path, sha = repo
    s = slices.import_slice(
        proj,
        "ROWX",
        "s1",
        repo="main",
        risk="R1",
        rulebooks=["a.md"],
        worktree=None,
        branch="b",
        pr=None,
        head=sha[:9],
        plan_path=None,
        purpose=None,
        phase="DEVELOP",
    )
    assert s["head"] == sha


def test_selfcheck_and_docs_accept_a_short_head(proj, mk_slice, repo):
    path, sha = repo
    k = mk_slice(proj, phase="SELF_CHECK", risk="R1", head=sha)
    slices.selfcheck(proj, k, "GO", head=sha[:8])
    assert slices.load(proj, k)["evidence"][sha]["selfcheck"]["head"] == sha
    k2 = mk_slice(proj, phase="DOCUMENT", risk="R1", head=sha)
    slices.docs(proj, k2, "GO", head=sha[:8])
    assert slices.load(proj, k2)["evidence"][sha]["docs"]["head"] == sha
