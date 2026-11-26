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
DESC_LIMIT = 400
DESC_SENTENCES = 3
SLICE_DESC_LIMIT = 300
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


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CODE_PREFIX = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?:\s*\([^)]*\))?:?\s+")


def describe(text, limit, sentences=DESC_SENTENCES) -> str:
    """The first sentences of `text` that fit `limit`, cut at a word if one is too long."""
    text = " ".join(text.split())
    out = ""
    for s in _SENTENCE_END.split(text)[:sentences]:
        joined = f"{out} {s}" if out else s
        if len(joined) > limit:
            break
        out = joined
    if out:
        return out
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def _entries(text):
    """(keys, item cell) for each roadmap table row that has a usable key."""
    for cells in _rows(text):
        key, item = _clean(cells[0]), cells[1]
        if key in ("#", "Item", "Group") or _KEY.fullmatch(key) is None:
            continue
        keys = [key]
        first = _clean(item).split(" ", 1)[0].rstrip(":,")
        if _CODENAME.fullmatch(first):
            keys.append(first)
        yield keys, item


def parse_titles(text) -> dict:
    """Row key to plain name, from the roadmap's group tables.

    A row is keyed by its first cell (a number such as `24zj`, or a code name such as
    `EXPECT-OFF-ROUTE`). A numbered row also answers to the code name that starts its
    item text.
    """
    out = {}
    for keys, item in _entries(text):
        title = _title(item)
        if title:
            for k in keys:
                out.setdefault(k, title)
    return out


def parse_descriptions(text) -> dict:
    """Row key to what the row is and why, from the same tables as `parse_titles`."""
    out = {}
    for keys, item in _entries(text):
        desc = describe(_CODE_PREFIX.sub("", _clean(item)), DESC_LIMIT)
        desc = desc[:1].upper() + desc[1:]
        if desc:
            for k in keys:
                out.setdefault(k, desc)
    return out


def _merged(cfg, parse) -> dict:
    path = Path(cfg["roadmap"]["path"])
    files = [path, *sorted((path.parent / "roadmap" / "groups").glob("*.md"))]
    out = {}
    for f in files:
        text = _read(f)
        if text:
            for k, v in parse(text).items():
                out.setdefault(k, v)
    return out


def titles(cfg) -> dict:
    """Row names for a project: the roadmap file plus its `roadmap/groups/*.md` tables."""
    return _merged(cfg, parse_titles)


def descriptions(cfg) -> dict:
    """What each row is and why, from the same files as `titles`."""
    return _merged(cfg, parse_descriptions)


# --- slice plans ---------------------------------------------------------------------

_GOAL = re.compile(r"^\W*(?:goal|summary)\W*:\s*(.+)$", re.I)
_WHY = re.compile(r"^\W*(?:why|intent|motivation)\W*:\s*(.+)$", re.I)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_PROBLEM = re.compile(r"\bproblem\b", re.I)
_SKIP_SECTION = re.compile(r"revision|changes|review record|deviations", re.I)
_SKIP_LINE = ("Code head", "Status:", "Revision")
_KEYISH = re.compile(r"[0-9][0-9A-Za-z.-]*|[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+|\([^)]*\)")


def _paragraphs(lines):
    """Blank-line separated paragraphs; a heading ends the one before it and is dropped."""
    para = []
    for line in lines:
        if line.strip() and not _HEADING.match(line):
            para.append(line.strip())
        elif para:
            yield " ".join(para)
            para = []
    if para:
        yield " ".join(para)


def _plain_paragraph(lines):
    """First paragraph that is prose: not a field, list, table, code or a skipped line."""
    for para in _paragraphs(lines):
        if para.startswith(_SKIP_LINE + ("|", "```", "- ", "* ", ">")):
            continue
        if re.match(r"^\W*[A-Za-z][A-Za-z ]{0,24}\W*:\s", para):
            continue
        return _clean(para)
    return None


def _sections(lines):
    """(heading text, body lines up to the next heading) for each heading."""
    heads = [i for i, ln in enumerate(lines) if _HEADING.match(ln)]
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        yield _HEADING.match(lines[i]).group(2), lines[i + 1 : end]


def _what(lines, keys):
    """The plan title without its row and slice prefix, first letter capitalised."""
    title = next(
        (m.group(2) for m in map(_HEADING.match, lines) if m and len(m.group(1)) == 1),
        "",
    )
    title = _clean(title)
    head, sep, rest = title.partition(": ")
    if sep and len(head) <= 80:
        title = rest
    else:
        words = title.split()
        while words and (words[0] in keys or _KEYISH.fullmatch(words[0])):
            words.pop(0)
        title = " ".join(words)
        if title.startswith("("):
            title = ""
    title = title.strip(" .:;")
    return title[:1].upper() + title[1:] if title else ""


def _why(lines):
    for head, body in _sections(lines):
        if _PROBLEM.search(head) and not _SKIP_SECTION.search(head):
            para = _plain_paragraph(body)
            if para:
                return para
    for line in lines:
        m = _WHY.match(line)
        if m:
            return _clean(m.group(1))
    return None


def plan_summary(path, limit=SLICE_DESC_LIMIT, keys=()):
    """`<what>. Why: <why>` from a slice plan, capped; None when the plan gives no title.

    What is the plan's H1 title without its row and slice prefix; why is the first prose
    paragraph under a Problem heading (or a `Why:` line), cut to two sentences.
    """
    if not path or not Path(path).is_file():
        return None
    text = _read(path)
    if not text:
        return None
    lines = text.splitlines()
    what = _what(lines, set(keys))
    if not what:
        for line in lines:
            m = _GOAL.match(line)
            if m:
                what = describe(_clean(m.group(1)), limit)
                break
    if not what:
        return None
    what = describe(what, limit)
    if not what.endswith("..."):
        what = what.rstrip(". ") + "."
    why = _why(lines)
    room = limit - len(what) - len(" Why: ")
    if why and room >= 40:
        cut = describe(why, room, sentences=2)
        if cut.endswith(":"):  # the paragraph led into a list
            cut = cut[:-1] + "."
        return f"{what} Why: {cut}"
    return what


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
