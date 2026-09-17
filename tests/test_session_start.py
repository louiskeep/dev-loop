import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SS = str(REPO / "hooks" / "session_start.py")


def test_session_start_emits_preamble_context():
    env = {"CLAUDE_PLUGIN_ROOT": str(REPO), "PATH": __import__("os").environ["PATH"]}
    out = subprocess.run([sys.executable, SS], input="{}", capture_output=True, text=True, check=False, env=env)
    assert out.returncode == 0
    payload = json.loads(out.stdout)
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "conductor" in hso["additionalContext"].lower()
