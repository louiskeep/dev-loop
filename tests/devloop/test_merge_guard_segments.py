"""The merge guard reads the executable of each shell command segment."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hooks import headless_merge_guard as guard  # noqa: E402

WORKER_ALLOWED = [
    "rg devloop-merge README.md",
    "grep -rn devloop-merge docs/",
    'git commit -m "mention gh pr merge"',
    'grep "git push origin main" x',
    "cat notes.md | grep devloop-merge",
    "echo 'run devloop-merge later' > note.txt",
    "ls && rg 'gh pr merge' docs",
    "FOO=1 rg devloop-merge .",
    "timeout 30 rg devloop-merge .",
    "git push origin feat/x",
    "cat > notes.md <<EOF\ngh pr merge\nEOF",
    "git commit -F - <<EOF\ngh pr merge\nEOF",
    "cat > f.txt <<'EOF'\nit's not run: devloop-merge x; git push origin main\nEOF",
]
WORKER_DENIED = [
    "devloop-merge proj ROW--S1",
    "/home/x/bin/devloop-merge proj ROW--S1",
    "FOO=1 devloop-merge proj ROW--S1",
    "env FOO=1 devloop-merge proj ROW--S1",
    "sudo devloop-merge proj ROW--S1",
    "timeout 60 devloop-merge proj ROW--S1",
    "ls; devloop-merge proj ROW--S1",
    "ls && devloop-merge proj ROW--S1",
    "false || devloop-merge proj ROW--S1",
    "echo x | xargs devloop-merge",
    "ls\ndevloop-merge proj ROW--S1",
    "bash -c 'devloop-merge proj ROW--S1'",
    "echo $(devloop-merge proj ROW--S1)",
    "gh pr merge 5",
    "git push origin main",
    "cd x && git push origin HEAD:main",
    "echo 'unterminated",
    "bash <<EOF\ngh pr merge 5\nEOF",
    "sh <<'EOF'\nit's text\ngit push origin main\nEOF",
    "cat <<'EOF' | bash\ngh pr merge 5\nEOF",
    "bash <<EOF\ngh pr merge 5",
    "bash -s <<EOF\ndevloop-merge p k\nEOF",
    "if true; then gh pr merge 1; fi",
    "while true; do devloop-merge p k; done",
    "for x in a b; do git push origin main; done",
    "{ gh pr merge 1; }",
    "! gh pr merge 1",
    "env",
    "ls; sudo",
    "env -S 'gh pr merge 1'",
    "env --split-string='gh pr merge 1'",
]


@pytest.mark.parametrize("command", WORKER_ALLOWED)
def test_a_worker_may_name_a_command_without_running_it(command):
    assert guard.blocked(command, worker=True) is False


@pytest.mark.parametrize("command", WORKER_DENIED)
def test_a_worker_is_denied_a_real_invocation(command):
    assert guard.blocked(command, worker=True) is True
