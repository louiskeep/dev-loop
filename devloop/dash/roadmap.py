"""Plain row names and the recently shipped record, read from the owner's own files.

Both readers are bounded (size cap per file) and cached by file mtime and size, so a poll
every few seconds reads a file again only after it changed. A missing file is an empty
answer; an unreadable one raises OSError so the view can name it.
"""

import datetime
import re
from pathlib import Path

MAX_BYTES = 1_000_000
TITLE_LIMIT = 80
SHIPPED_SHOWN = 10
BULLET_LIMIT = 300
BULLETS_PER_ENTRY = 40
SHIPPED_SECTION_LIMIT = 200

_cache: dict = {}


def _read(path):
    """File text, cached by mtime and size; None when the file does not exist."""
    p = Path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(str(p))
    if hit and hit[0] == stamp:
        return hit[1]
    with open(p, "rb") as fh:
        text = fh.read(MAX_BYTES).decode("utf-8", "replace")
    _cache[str(p)] = (stamp, text)
    return text


# --- row names -----------------------------------------------------------------------

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PAREN = re.compile(r"\s*\([^)]*\)")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_CODENAME = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")


def _clean(text) -> str:
    text = _MD_LINK.sub(r"\1", text).replace("`", "").replace("*", "")
    return " ".join(text.split())


def _title(item) -> str:
    """The first sentence of a roadmap item, without parenthetical notes."""
    text = _PAREN.sub("", _clean(item))
    for stop in (". ", "; ", ": "):
        i = text.find(stop)
        if 0 < i:
            # Keep "NAME: what it does" together; cut at the sentence after it.
            if stop == ": " and i <= 24:
                continue
            text = text[:i]
    text = text.strip(" .;:")
    if len(text) > TITLE_LIMIT:
        text = text[: TITLE_LIMIT - 3].rsplit(" ", 1)[0] + "..."
    return text


def _rows(text):
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        yield cells


def parse_titles(text) -> dict:
    """Row key to plain name, from the roadmap's group tables.

    A row is keyed by its first cell (a number such as `24zj`, or a code name such as
    `EXPECT-OFF-ROUTE`). A numbered row also answers to the code name that starts its
    item text.
    """
    out = {}
    for cells in _rows(text):
        key, item = _clean(cells[0]), cells[1]
        if key in ("#", "Item", "Group") or _KEY.fullmatch(key) is None:
            continue
        title = _title(item)
        if not title:
            continue
        out.setdefault(key, title)
        first = _clean(item).split(" ", 1)[0].rstrip(":,")
        if _CODENAME.fullmatch(first):
            out.setdefault(first, title)
    return out


def titles(cfg) -> dict:
    """Row names for a project: the roadmap file plus its `roadmap/groups/*.md` tables."""
    path = Path(cfg["roadmap"]["path"])
    files = [path, *sorted((path.parent / "roadmap" / "groups").glob("*.md"))]
    out = {}
    for f in files:
        text = _read(f)
        if text:
            for k, v in parse_titles(text).items():
                out.setdefault(k, v)
    return out


# --- recently shipped ----------------------------------------------------------------

_SECTION = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2}):\s*(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


def parse_shipped(text) -> list:
    """Sections headed `## <date>: <title>`, newest first, as {date, title, bullets}.

    A heading that does not match is skipped, and so are its bullets. Bullets stay plain
    text: the renderer escapes them and no markup is interpreted.
    """
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            m = _SECTION.match(line)
            try:
                if not m:
                    raise ValueError
                datetime.date.fromisoformat(m.group(1))
                cur = {"date": m.group(1), "title": _clean(m.group(2)), "bullets": []}
                out.append(cur)
            except ValueError:
                cur = None
            continue
        b = _BULLET.match(line)
        if cur is not None and b and len(cur["bullets"]) < BULLETS_PER_ENTRY:
            cur["bullets"].append(_clean(b.group(1))[:BULLET_LIMIT])
        if len(out) >= SHIPPED_SECTION_LIMIT:
            break
    out.sort(key=lambda s: s["date"], reverse=True)
    return out[:SHIPPED_SHOWN]


def shipped(cfg) -> list:
    path = cfg.get("shipped_doc")
    if not path:
        return []
    text = _read(path)
    return parse_shipped(text) if text else []
