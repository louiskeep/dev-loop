"""Notes the loop keeps: tick log, known problems, lessons, owner questions.

Problems and lessons live in JSON (the source of truth) and are rendered to Markdown
after each write. Questions live in one Markdown block so the owner can answer by
editing a line in a page they already keep.
"""

import re
import subprocess
from pathlib import Path

from . import project as projmod
from . import store

TICKLOG_KEEP = 200
BLOCK_START = "<!-- devloop:questions -->"
BLOCK_END = "<!-- /devloop:questions -->"
_TICK = re.compile(r"<!-- tick (\S+) -->")
_MONTH = re.compile(r"\d{4}-\d{2}")
_OPTION_SEP = " | "


class NotesError(Exception):
    pass


def _key(key):
    if not isinstance(key, str) or store.TOKEN.fullmatch(key) is None:
        raise NotesError("bad note key")
    return key


def _line(value, limit, what):
    """One short line of text; a note field never carries a newline into a page."""
    if not isinstance(value, str):
        raise NotesError(f"{what} must be text")
    return " ".join(value.split())[:limit]


def _dir(project):
    return store.state_dir(project)


# --- tick log ----------------------------------------------------------------


def _read_ticklog(path):
    entries, at, body = [], None, []
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for line in text.splitlines():
        m = _TICK.fullmatch(line)
        if m:
            if at is not None:
                entries.append((at, "\n".join(body)))
            at, body = m.group(1), []
        elif at is not None:
            body.append(line)
    if at is not None:
        entries.append((at, "\n".join(body)))
    return entries


def _render_ticklog(entries):
    return "".join(f"<!-- tick {at} -->\n{body}\n" for at, body in entries)


def ticklog(project: str, text: str) -> None:
    """Append an entry; entries past the newest 200 move to a file for their month."""
    if not isinstance(text, str):
        raise NotesError("tick log entry must be text")
    # An entry line shaped like a marker would split the entry in two when read back.
    body = "\n".join(
        f" {ln}" if _TICK.fullmatch(ln) else ln
        for ln in text.strip()[:4000].splitlines()
    )
    d = _dir(project)
    path = d / "tick-log.md"
    with store.locked(path):
        entries = _read_ticklog(path) + [(store.now_iso(), body)]
        if len(entries) > TICKLOG_KEEP:
            old, entries = entries[:-TICKLOG_KEEP], entries[-TICKLOG_KEEP:]
            by_month = {}
            for at, entry_body in old:
                month = at[:7] if _MONTH.fullmatch(at[:7]) else "unknown"
                by_month.setdefault(month, []).append((at, entry_body))
            # Month files first: a crash then leaves a duplicate, never a lost entry.
            for month, items in by_month.items():
                mp = d / f"tick-log-{month}.md"
                with store.locked(mp):
                    prior = mp.read_text(encoding="utf-8") if mp.exists() else ""
                    store.write_text(mp, prior + _render_ticklog(items))
        store.write_text(path, _render_ticklog(entries))


# --- problems and lessons ----------------------------------------------------


def _update(project, filename, section, key, make, post=None):
    """Create the entry or bump its count, then write the file once."""
    path = _dir(project) / filename
    with store.locked(path):
        data = store.read_json(path)
        table = data.setdefault(section, {})
        now = store.now_iso()
        entry = table.get(key)
        if entry is None:
            entry = {"key": key, "count": 0, "first_seen": now}
        entry.update(make())
        entry["count"] += 1
        entry["last_seen"] = now
        table[key] = entry
        if post:
            post(table)
        store.write_json(path, data)
        return table


def problem(project: str, key: str, *, impact: str, fix: str, detail: str = "") -> None:
    _key(key)
    fields = {
        "impact": _line(impact, 300, "impact"),
        "fix": _line(fix, 300, "fix"),
        "detail": _line(detail, 1000, "detail"),
        "open": True,
    }
    table = _update(project, "problems.json", "problems", key, lambda: fields)
    _render_problems(project, table)


def problem_close(project: str, key: str) -> None:
    _key(key)
    path = _dir(project) / "problems.json"
    with store.locked(path):
        data = store.read_json(path)
        entry = data.get("problems", {}).get(key)
        if entry is None:
            raise NotesError("no such problem")
        entry["open"], entry["closed_at"] = False, store.now_iso()
        store.write_json(path, data)
        _render_problems(project, data["problems"])


def _render_problems(project, table):
    live = [e for e in table.values() if e["open"]]
    shut = [e for e in table.values() if not e["open"]]
    lines = ["# Known problems", ""]
    for e in sorted(live, key=lambda e: e["key"]):
        times = f"seen {e['count']} time{'s' if e['count'] != 1 else ''}"
        lines.append(
            f"- {e['key']} ({times}, last {e['last_seen'][:10]}): {e['impact']}"
        )
        lines.append(f"  Fix: {e['fix']}")
    if not live:
        lines.append("None open.")
    if shut:
        lines += ["", "## Closed", ""]
        lines += [f"- {e['key']}" for e in sorted(shut, key=lambda e: e["key"])]
    store.write_text(_dir(project) / "known-problems.md", "\n".join(lines) + "\n")


def lesson(project: str, key: str, *, area: str, wrong: str, instead: str) -> None:
    _key(key)
    area = _key(area)
    fields = {
        "area": area,
        "wrong": _line(wrong, 300, "wrong"),
        "instead": _line(instead, 300, "instead"),
    }

    def flag(table):
        for entry in table.values():
            # Three repeats means the rule belongs in the defect catalog, not only here.
            entry["catalog_candidate"] = entry["count"] >= 3

    table = _update(project, "lessons.json", "lessons", key, lambda: fields, flag)
    _render_lessons(project, table)


def lessons_for(project: str, area: str) -> list:
    table = store.read_json(_dir(project) / "lessons.json").get("lessons", {})
    hits = [e for e in table.values() if e["area"] == area]
    return sorted(hits, key=lambda e: (-e["count"], e["key"]))


def _render_lessons(project, table):
    lines = ["# Lessons", ""]
    for area in sorted({e["area"] for e in table.values()}):
        lines += [f"## {area}", ""]
        for e in sorted(
            (e for e in table.values() if e["area"] == area), key=lambda e: e["key"]
        ):
            flag = " (add to the defect catalog)" if e["catalog_candidate"] else ""
            lines.append(
                f"- {e['key']} x{e['count']}{flag}: wrong: {e['wrong']} Instead: {e['instead']}"
            )
        lines.append("")
    store.write_text(_dir(project) / "lessons.md", "\n".join(lines))


# --- questions ---------------------------------------------------------------

_FIELD = re.compile(
    r"- (Question|Options|Recommended|Wait|Asked|Applied|Answer):[ ]?(.*)"
)


def _questions_path(project) -> Path:
    cfg = projmod.load_project(project)
    custom = (cfg.get("questions") or {}).get("path")
    return Path(custom) if custom else _dir(project) / "questions.md"


def _lock_for(project) -> Path:
    # The lock sits in the state dir so no lock file lands in the owner's synced page folder.
    return _dir(project) / "questions.md"


def _split_page(text):
    """Return (before, block body, after); the block is absent when no marker exists."""
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    if start < 0 and end < 0:
        return text, None, ""
    if start < 0 or end < start:
        raise store.StoreError("questions page has unbalanced markers")
    if text.count(BLOCK_START) != 1 or text.count(BLOCK_END) != 1:
        raise store.StoreError("questions page has repeated markers")
    return (
        text[:start],
        text[start + len(BLOCK_START) : end],
        text[end + len(BLOCK_END) :],
    )


def _parse_questions(body):
    out = {}
    cur = None
    for line in (body or "").splitlines():
        if line.startswith("### "):
            cur = {"key": line[4:].strip()}
            out[cur["key"]] = cur
            continue
        m = _FIELD.fullmatch(line.rstrip())
        if cur is not None and m:
            cur[m.group(1).lower()] = m.group(2).strip()
    result = {}
    for key, raw in out.items():
        options = [o for o in raw.get("options", "").split(_OPTION_SEP) if o]
        result[key] = {
            "key": key,
            "question": raw.get("question", ""),
            "options": options,
            "recommended": raw.get("recommended", ""),
            "wait": raw.get("wait") == "yes",
            "asked_at": raw.get("asked", ""),
            "applied": raw.get("applied") or None,
            "answer": raw.get("answer") or None,
        }
    return result


def _render_questions(qs):
    lines = [
        "",
        "<!-- Managed by devloop. Answer by writing an option after the Answer: line. -->",
    ]
    for q in qs.values():
        lines += [
            "",
            f"### {q['key']}",
            f"- Question: {q['question']}",
            f"- Options: {_OPTION_SEP.join(q['options'])}",
            f"- Recommended: {q['recommended']}",
            f"- Wait: {'yes' if q['wait'] else 'no'}",
            f"- Asked: {q['asked_at']}",
            f"- Applied: {q['applied'] or ''}",
            f"- Answer: {q['answer'] or ''}",
        ]
    return "\n".join(lines) + "\n"


def _load_questions(path):
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    before, body, after = _split_page(text)
    return before, _parse_questions(body), after, body is not None


def _save_questions(path, before, qs, after, had_block):
    block = BLOCK_START + _render_questions(qs) + BLOCK_END
    if had_block:
        text = before + block + after
    else:
        text = (
            before
            + ("\n" if before and not before.endswith("\n") else "")
            + block
            + "\n"
        )
    store.write_text(path, text)


def _notify(project, text):
    cfg = projmod.load_project(project)
    cmd = (cfg.get("notify") or {}).get("cmd")
    if not cmd:
        return
    ok = False
    try:
        r = subprocess.run(
            cmd + [text],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    if not ok:
        store.append_event(project, "notify-failed")


def _slice_for(project, key):
    # Imported here because slices imports this module to record board failures.
    from . import slices

    try:
        path = slices.slice_path(project, key)
    except slices.SliceError:
        return None
    return slices.load(project, key) if path.is_file() else None


def ask(
    project: str,
    key: str,
    *,
    question: str,
    options: list,
    recommended: str,
    wait: bool,
) -> dict:
    """Record a question for the owner. Without `wait` the recommended option applies now."""
    from . import slices

    _key(key)
    text = _line(question, 500, "question")
    if not isinstance(options, list) or not options:
        raise NotesError("a question needs options")
    clean = [_line(o, 200, "option") for o in options]
    if any(not o or _OPTION_SEP in o for o in clean):
        raise NotesError("options must be non-empty text without ' | '")
    if recommended not in clean:
        raise NotesError("recommended must be one of the options")
    if store.is_headless():
        held = _slice_for(project, key)
        if held and (
            held["phase"] == "HELD"
            or (held["phase"] == "PARKED" and held["park_reason"] in slices.OWNER_PARKS)
        ):
            raise NotesError("a held slice's question is the owner's to ask")
    path = _questions_path(project)
    with store.locked(_lock_for(project)):
        before, qs, after, had = _load_questions(path)
        if key in qs and qs[key]["answer"]:
            raise NotesError("question already answered")
        q = {
            "key": key,
            "question": text,
            "options": clean,
            "recommended": recommended,
            "wait": bool(wait),
            "asked_at": store.now_iso(),
            "applied": None if wait else recommended,
            "answer": None,
        }
        qs[key] = q
        _save_questions(path, before, qs, after, had)
    if wait:
        if _slice_for(project, key):
            slices.set_fields(project, key, waiting_on=f"question {key}")
        store.append_event(project, "question-waiting", key=key)
        _notify(
            project, f"Question {key} needs your answer. The work is waiting on it."
        )
    else:
        store.append_event(project, "question-defaulted", key=key)
        _notify(
            project,
            f"Question {key}: the recommended answer is in use. You can overrule it on the questions page.",
        )
    return q


def ensure_hold_question(project, key):
    """Make the hold's question offer `approve` and one other option.

    The release option is the fixed word `approve`, never text an agent wrote, and the
    check that the slice is really held happens under the same lock as the edit.
    """
    from . import slices

    path = _questions_path(project)
    with store.locked(_lock_for(project)):
        s = _slice_for(project, key)
        held = s and (
            s["phase"] == "HELD"
            or (s["phase"] == "PARKED" and s["park_reason"] in slices.OWNER_PARKS)
        )
        if not held:
            return
        before, qs, after, had = _load_questions(path)
        q = qs.get(key)
        if q is None:
            q = qs[key] = {
                "key": key,
                "question": "Release the hold on this slice?",
                "options": [slices.HOLD_RELEASE_OPTION, "keep-held"],
                "recommended": "keep-held",
                "wait": True,
                "asked_at": store.now_iso(),
                "applied": None,
                "answer": None,
            }
        else:
            if slices.HOLD_RELEASE_OPTION not in q["options"]:
                q["options"] = q["options"] + [slices.HOLD_RELEASE_OPTION]
            if len(q["options"]) < 2:
                q["options"] = q["options"] + ["keep-held"]
            # A new hold needs a fresh answer.
            q["answer"] = q["applied"] = None
        _save_questions(path, before, qs, after, had)


def _lifts_hold(s, key, choice):
    return s["hold_question"] == key and s["hold_release_option"] == choice


def _apply_answer(project, key, choice):
    from . import slices

    path = _questions_path(project)
    with store.locked(_lock_for(project)):
        before, qs, after, had = _load_questions(path)
        q = qs.get(key)
        if q is None:
            raise NotesError("no such question")
        if choice not in q["options"]:
            raise NotesError("answer must be one of the question's options")
        q["answer"] = q["applied"] = choice
        _save_questions(path, before, qs, after, had)
    store.append_event(project, "question-answered", key=key)
    s = _slice_for(project, key)
    if s and s["waiting_on"] == f"question {key}":
        if s["phase"] == "HELD":
            # Any other choice is recorded above but keeps the slice held.
            if _lifts_hold(s, key, choice):
                slices.release_hold(project, key)
        elif s["phase"] != "DONE":
            slices.set_fields(project, key, waiting_on=None)
    if s and s["phase"] == "PARKED" and s["park_reason"] in slices.OWNER_PARKS:
        if _lifts_hold(s, key, choice):
            slices.owner_answer(project, key)


def answer(project: str, key: str, choice: str) -> None:
    """Owner only: the headless tick may not answer its own questions."""
    store.require_interactive("answer")
    _key(key)
    _apply_answer(project, key, choice)


def read_answers(project: str) -> list:
    """Apply answers the owner typed into the page. Returns the keys applied."""
    path = _questions_path(project)
    with store.locked(_lock_for(project)):
        _, qs, _, _ = _load_questions(path)
    applied = []
    for key, q in qs.items():
        if not q["answer"] or q["answer"] == q["applied"]:
            continue
        gated = _slice_for(project, key)
        if store.is_headless() and gated and gated["phase"] in ("HELD", "PARKED"):
            continue  # releasing a held slice is interactive only
        if q["answer"] not in q["options"]:
            problem(
                project,
                f"answer-invalid-{key}",
                impact="the owner's answer was ignored",
                fix="write one of the listed options after Answer:",
            )
            continue
        _apply_answer(project, key, q["answer"])
        applied.append(key)
    return applied
