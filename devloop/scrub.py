"""Second layer for public text. The first layer is never passing free text at all."""

import re

from .store import TOKEN

_TOKEN = re.compile(r"(?:ghp_|gho_|github_pat_|sk-|xox[abp]-|AKIA)[A-Za-z0-9_\-]{8,}")
# user:password@host in any URL, however short the password is.
_URL_CRED = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/@]*@\S*")
_LONG = re.compile(r"[A-Za-z0-9+/=_\-]{40,}")
_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
_PATH = re.compile(r"(?:~|(?<![\w.:/-]))/[^\s/]+(?:/[^\s/]+)+")


def scrub(text, limit: int = 160) -> str:
    """Replace secrets, commit ids and paths, then cut to `limit` characters."""
    out = " ".join(str(text).split())
    for rx, repl in (
        (_URL_CRED, "[redacted]"),
        (_TOKEN, "[redacted]"),
        (_LONG, "[redacted]"),
        (_PATH, "[ref]"),
        (_HEX, "[ref]"),
    ):
        out = rx.sub(repl, out)
    return out[:limit]


def scrub_lines(text, limit: int = 1500, keep=()) -> str:
    """Like `scrub`, but keeps line breaks and lets named paths (`keep`) through.

    The kept paths are the pages the owner is told to open; everything else that looks
    like a path, commit id or secret is still replaced.
    """
    marks = {}
    raw = str(text)
    for i, path in enumerate(p for p in keep if p):
        mark = f"@@KEEP{i}@@"
        marks[mark] = path
        raw = raw.replace(path, mark)
    lines = [scrub(line, len(raw) + 1) for line in raw.splitlines()]
    out = "\n".join(lines).strip("\n")
    for mark, path in marks.items():
        out = out.replace(mark, path)
    return out[:limit]


_HEX_RUN = re.compile(r"[0-9a-f]{7,}")


def plain(value) -> str:
    """One ID rule for every public surface: a plain token, or `?`.

    Event and config values can come from a shell (`devloop-state event`), so an ID
    that looks like a secret or a commit id is refused, not trusted.
    """
    text = str(value)
    if (
        TOKEN.fullmatch(text) is None
        or _TOKEN.search(text)
        or _HEX_RUN.search(text)
        or _LONG.search(text)
    ):
        return "?"
    return text
