import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import check, project, runs, slices, store, tick

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "dev-loop"


def test_example_project_validates(home, write_project):
    cfg = json.loads((SKILL / "project.example.json").read_text())
    write_project(home, "example", cfg)
    assert project.load_project("example")["name"] == "example"


def sections(text, level):
    marker = "#" * level + " "
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith(marker) and not line.startswith(marker + "#"):
            cur = line[len(marker) :].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def test_playbook_covers_every_phase_and_kind():
    text = (SKILL / "PLAYBOOK.md").read_text()
    top = sections(text, 2)
    done = {k for k in re.findall(r"^### (\w+)$", top["Done state per phase"], re.M)}
    assert set(slices.PHASES) <= done
    briefs = set(re.findall(r"^### ([\w-]+)$", top["Worker briefs"], re.M))
    assert set(runs.KINDS) <= briefs
    for needed in ("Scenarios", "Testing per phase and risk", "Close-out"):
        assert needed in top


def _label_prefixes():
    src = (ROOT / "devloop" / "slices.py").read_text()
    out = set()
    for m in re.finditer(r'f?"([^"\n]{6,})"', src):
        out.add(m.group(1).split("{")[0].rstrip())
    return {x for x in out if len(x) >= 4}


def test_playbook_code_items_match_checklist_names():
    prefixes = _label_prefixes()
    items = [
        line[2:].rsplit(" [code]", 1)[0]
        for line in (SKILL / "PLAYBOOK.md").read_text().splitlines()
        if line.startswith("- ") and line.rstrip().endswith("[code]")
    ]
    assert items
    bad = [i for i in items if not any(i.startswith(p) for p in prefixes)]
    assert bad == []


def _setup_docs(proj, tmp_path, cfg_edit):
    pb = tmp_path / "addendum.md"
    pb.write_text(
        "## Worker briefs\n\n### builder\n\nPROJECT-BUILDER-MARK\n\n### test\n\nPROJECT-TEST-MARK\n"
    )
    td = tmp_path / "tick-doc.md"
    td.write_text("PROJECT-TICK-MARK\n")
    cfg_edit(lambda c: c.update(playbook_doc=str(pb), tick_doc=str(td)))


def test_tick_stdin_has_playbook(proj, tmp_path, cfg_edit, monkeypatch, fakebin):
    _setup_docs(proj, tmp_path, cfg_edit)
    monkeypatch.setenv("FAKE_ARGV_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "ok")
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(10**8))
    tick.tick(proj)
    call = json.loads((tmp_path / "argv.log").read_text().splitlines()[0])
    for marker in (
        "# Dev loop playbook",
        "Done state per phase",
        "PROJECT-BUILDER-MARK",
        "PROJECT-TICK-MARK",
        "# Dev loop tick",
    ):
        assert marker in call["stdin"], marker


def test_brief_has_role_section(proj, tmp_path, cfg_edit, mk_slice, monkeypatch):
    _setup_docs(proj, tmp_path, cfg_edit)
    k = mk_slice(proj, phase="DEVELOP", risk="R1", rulebooks=["a.md"])
    text = runs.build_brief(proj, k, "builder", "BODY")
    assert "Work only in the given worktree" in text and "PROJECT-BUILDER-MARK" in text
    assert "PROJECT-TEST-MARK" not in text
    for kind in runs.KINDS:
        assert runs.role_section(proj, kind), kind


def run_env(home, extra=None):
    py = str(Path(sys.executable).parent)
    env = {
        "HOME": str(home),
        "PATH": f"{py}:{ROOT / 'tests' / 'devloop' / 'fakebin'}:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra or {})
    return env


def test_install_replaces_atomically(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    binp = tmp_path / "bin"
    for _ in range(2):
        out = subprocess.run(
            [str(ROOT / "bin" / "devloop-install"), str(binp)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=run_env(h),
        )
        assert out.returncode == 0, out.stderr
    lib = h / ".local" / "share" / "devloop" / "lib"
    assert lib.is_symlink() and (lib / "devloop" / "__init__.py").is_file()
    assert len(list((h / ".local/share/devloop/versions").iterdir())) >= 1
    names = sorted(p.name for p in binp.iterdir())
    assert "devloop-tick" in names and not [n for n in names if n.endswith(".tmp")]
    assert (h / ".claude" / "skills" / "dev-loop").is_symlink()


def test_installed_skill_files_come_from_the_installed_version(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    out = subprocess.run(
        [str(ROOT / "bin" / "devloop-install"), str(tmp_path / "bin")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=run_env(h),
    )
    assert out.returncode == 0, out.stderr
    lib = (h / ".local" / "share" / "devloop" / "lib").resolve()
    assert (lib / "skills" / "dev-loop" / "TICK.md").is_file()
    where = subprocess.run(
        [
            sys.executable,
            "-c",
            "from devloop import store; print(store.skill_dir())",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**run_env(h), "PYTHONPATH": str(lib)},
    )
    assert Path(where.stdout.strip()) == lib / "skills" / "dev-loop", where.stderr
    link = h / ".claude" / "skills" / "dev-loop"
    assert link.resolve() == lib / "skills" / "dev-loop"


def test_install_never_replaces_a_real_skill_folder(tmp_path):
    h = tmp_path / "h"
    real = h / ".claude" / "skills" / "dev-loop"
    real.mkdir(parents=True)
    (real / "mine.md").write_text("keep")
    out = subprocess.run(
        [str(ROOT / "bin" / "devloop-install"), str(tmp_path / "bin")],
        capture_output=True,
        text=True,
        env=run_env(h),
    )
    assert out.returncode == 0 and (real / "mine.md").read_text() == "keep"
    assert not real.is_symlink()


def test_installed_start_stop_start(tmp_path, minimal_project):
    h = tmp_path / "h"
    h.mkdir()
    binp = tmp_path / "bin"
    other = tmp_path / "elsewhere"
    other.mkdir()
    crontab = tmp_path / "crontab"
    crontab.write_text("0 3 * * * backup-job\n")
    env = run_env(
        h,
        {
            "DEVLOOP_CRONTAB": str(ROOT / "tests/devloop/fakebin/crontab"),
            "FAKE_CRONTAB": str(crontab),
        },
    )
    cfg = minimal_project("p")
    state = h / ".local" / "state" / "devloop" / "p"
    state.mkdir(parents=True)
    (state / "project.json").write_text(json.dumps(cfg))

    def sh(cmd, *args):
        r = subprocess.run(
            [str(binp / cmd), *args], capture_output=True, text=True, cwd=other, env=env
        )
        assert r.returncode == 0, (cmd, args, r.stderr)
        return r.stdout

    inst = subprocess.run(
        [str(ROOT / "bin" / "devloop-install"), str(binp)],
        capture_output=True,
        text=True,
        cwd=other,
        env=env,
    )
    assert inst.returncode == 0, inst.stderr
    # start
    sh("devloop-state", "p", "resume")
    sh("devloop-cron", "add", "p", "--bin-dir", str(binp))
    # stop
    sh("devloop-state", "p", "pause")
    assert (state / "PAUSE").exists()
    sh("devloop-cron", "remove", "p")
    assert "# devloop:p" not in crontab.read_text()
    # start again
    sh("devloop-state", "p", "resume")
    sh("devloop-cron", "add", "p", "--bin-dir", str(binp))
    text = crontab.read_text()
    assert not (state / "PAUSE").exists()
    assert text.count("# devloop:p") == 1 and "backup-job" in text
    line = next(x for x in text.splitlines() if "devloop:p" in x)
    assert f"{binp}/devloop-tick p" in line
    assert (
        sh("devloop-state", "p", "slice", "list") == ""
    )  # the launcher imports the package


@pytest.fixture
def ready_cfg(proj, cfg_edit, tmp_path, fakebin):
    (tmp_path / "ROADMAP.md").write_text("roadmap")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "README.md").write_text("r")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir()

    def edit(c):
        c["roadmap"]["path"] = str(tmp_path / "ROADMAP.md")
        c["rules_dir"] = str(rules)
        c["repos"][0]["path"] = str(repo)

    cfg_edit(edit)


def test_check_passes_on_a_complete_setup(proj, ready_cfg):
    results = check.run_checks(proj)
    assert all(ok for ok, _ in results), [m for ok, m in results if not ok]


def test_check_names_each_problem(proj, ready_cfg, cfg_edit, tmp_path):
    def edit(c):
        c["roadmap"]["path"] = str(tmp_path / "gone.md")
        c["board"] = {"cmd": ["no-such-board"], "phase_map": {"FRAME": "f"}}
        c["tests"] = {"t1": {"argv": ["no-such-runner"]}}
        c["snapshot_cmds"] = [{"name": "x", "argv": ["false"]}]

    cfg_edit(edit)
    bad = " | ".join(m for ok, m in check.run_checks(proj) if not ok)
    for needle in (
        "roadmap: missing",
        "no phase map entry for PLAN",
        "no-such-board",
        "no-such-runner",
        "snapshot_cmds[0]",
    ):
        assert needle in bad, needle


def test_check_fails_when_a_dennis_agent_is_missing(
    proj, ready_cfg, cfg_edit, monkeypatch
):
    cfg_edit(
        lambda c: c["reviewers"].update(ghost={"kind": "dennis", "kinds": ["dennis"]})
    )
    results = check.run_checks(proj)
    assert (True, "agents: reviewer agent dennis exists") in results
    assert (False, "agents: reviewer agent ghost not found") in results


def test_check_only_warns_when_claude_cannot_list_agents(proj, ready_cfg, monkeypatch):
    monkeypatch.setenv("FAKE_AGENTS_EXIT", "2")
    results = check.run_checks(proj)
    assert all(ok for ok, _ in results)
    assert any("could not list claude agents" in m for _, m in results)


def test_check_cli_exit_code(proj, ready_cfg, home, cfg_edit):
    state = ROOT / "bin" / "devloop-state"
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    ok = subprocess.run(
        [str(state), proj, "check"], capture_output=True, text=True, env=env
    )
    assert ok.returncode == 0 and "FAIL" not in ok.stdout
    cfg_edit(lambda c: c["roadmap"].update(path="/nowhere/at/all.md"))
    bad = subprocess.run(
        [str(state), proj, "check"], capture_output=True, text=True, env=env
    )
    assert bad.returncode == 3 and "FAIL: roadmap: missing" in bad.stdout


def test_pause_and_resume(proj, home):
    state = ROOT / "bin" / "devloop-state"
    env = {**os.environ, "DEVLOOP_HOME": str(home)}
    subprocess.run([str(state), proj, "pause"], env=env, check=True)
    assert (home / "p" / "PAUSE").exists()
    subprocess.run([str(state), proj, "resume"], env=env, check=True)
    assert not (home / "p" / "PAUSE").exists()


def test_ci_lints_the_devloop_package():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "ruff check hooks/ devloop/ tests/" in ci
    assert "ruff format --check hooks/ devloop/ tests/" in ci


def test_playbook_and_skill_files_exist():
    for name in ("SKILL.md", "TICK.md", "PLAYBOOK.md", "brief-template.md"):
        assert (SKILL / name).is_file()
    assert store.skill_dir() == SKILL
