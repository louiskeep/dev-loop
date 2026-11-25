"""Second layer for public text. The first layer is never passing free text at all."""

import re

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
