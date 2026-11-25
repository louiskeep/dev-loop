import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Private values live only in a project's own config, never in shipped code.
PRIVATE = re.compile(
    r"/home/|/Users/|decoy|louis|ckeep|camkeep|goodneighbor|\bcam\b|192\.168\.|\.116\b|\.55\b|obsidian|\.local/state/devloop/[a-z]+/project",
    re.IGNORECASE,
)
EM_DASH = chr(0x2014)


def shipped():
    files = list((ROOT / "devloop").rglob("*.py"))
    files += [p for p in (ROOT / "bin").glob("devloop-*") if p.is_file()]
    files += [p for p in (ROOT / "skills" / "dev-loop").rglob("*") if p.is_file()]
    return files


def test_no_private_strings_in_shipped_files():
    hits = [
        f"{p.relative_to(ROOT)}: {m.group(0)}"
        for p in shipped()
        for m in PRIVATE.finditer(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_no_em_dashes_anywhere_in_devloop_files():
    files = shipped() + list((ROOT / "tests" / "devloop").rglob("*.py"))
    assert [str(p) for p in files if EM_DASH in p.read_text(encoding="utf-8")] == []
