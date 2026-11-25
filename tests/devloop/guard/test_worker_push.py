"""A worker that is not a Claude session cannot update a base branch by accident."""

import subprocess

from devloop import store, wrap
from test_push import commit, git, remote_head, repo  # noqa: F401
from test_wrap import mk


def run_payload(proj, tmp_path, wt, script):
    rd = mk(
        proj,
        tmp_path,
        ["sh", "-c", script],
        kind="duty",
        reader="exit",
        cwd=str(wt),
    )
    wrap.run(proj, "r1")
    return rd, (store.out_dir(rd) / "log").read_text()


def test_worker_push_to_main_is_refused_and_a_feature_push_works(proj, tmp_path, repo):
    wt, remote = repo
    main_before = remote_head(remote, "main")
    sha = commit(wt)
    script = (
        "git push origin HEAD:refs/heads/main; "
        "git push origin HEAD:refs/heads/master; "
        "git push origin :refs/heads/main; "
        "git push origin HEAD:refs/heads/feat/other"
    )
    _, log = run_payload(proj, tmp_path, wt, script)
    assert remote_head(remote, "main") == main_before
    assert remote_head(remote, "master") is None
    assert remote_head(remote, "feat/other") == sha
    assert "may not update the base branch main" in log


def test_repo_own_hooks_still_run_for_a_worker(proj, tmp_path, repo):
    wt, remote = repo
    marker = tmp_path / "marker"
    for name, extra in (("pre-commit", ""), ("pre-push", "")):
        hook = wt / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\necho {name} >> {marker}\n{extra}")
        hook.chmod(0o755)
    (wt / "w").write_text("w")
    script = "git add -A && git -c user.name=t -c user.email=t@t commit -qm w && "
    script += "git push origin HEAD:refs/heads/feat/w"
    run_payload(proj, tmp_path, wt, script)
    assert marker.read_text().split() == ["pre-commit", "pre-push"]
    assert remote_head(remote, "feat/w") is not None


def test_repo_pre_push_failure_still_blocks_a_worker_push(proj, tmp_path, repo):
    wt, remote = repo
    hook = wt / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    commit(wt)
    run_payload(proj, tmp_path, wt, "git push origin HEAD:refs/heads/feat/x")
    assert remote_head(remote, "feat/x") is None


def test_guard_leaves_the_repo_config_alone(proj, tmp_path, repo):
    wt, _ = repo
    run_payload(proj, tmp_path, wt, "true")
    r = subprocess.run(
        ["git", "-C", str(wt), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
