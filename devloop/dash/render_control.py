"""HTML for acting on the loop: the decisions drawer sources, "Decided for you", controls.

Like the other renderers this only formats the sealed view. The write token and the
questions file path come from the server and are passed in, never read from the view.
Every form posts to a route in `server.ROUTES`; the page script submits them with fetch
and shows the server's message.
"""

from . import render as r

e = r.e
RECOMMENDED = "Go with recommended"


def _hidden(**fields) -> str:
    return "".join(
        f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items()
    )


def _option_forms(item, token):
    """The choices a form may submit: only options whose shown text is the real value."""
    real = [o for o, ok in zip(item["options"], item["option_ok"]) if ok]
    rec = item["recommended"] if item["recommended"] in real else None
    return rec, [o for o in real if o != rec]


def _form(action, item, token, label, cls="qform", **fields):
    key, version = item["answer_key"], item["version"]
    return (
        f'<form class="{cls}" method="post" action="{e(action)}" '
        f'data-key="{e(key)}" data-version="{e(version)}">'
        f"{_hidden(t=token, key=key, version=version, **fields)}"
        f'<button type="submit">{e(label)}</button></form>'
    )


def _free_text(item, token):
    key, version = item["answer_key"], item["version"]
    ident = f"ta-{key}-{version}"
    return (
        f'<form class="qform qtext" method="post" action="/act/answer" '
        f'data-key="{e(key)}" data-version="{e(version)}">'
        f"{_hidden(t=token, key=key, version=version)}"
        f'<label for="{e(ident)}">Your answer</label>'
        f'<textarea id="{e(ident)}" name="other" rows="3" maxlength="500" '
        'placeholder="Write one short instruction"></textarea>'
        '<button type="submit">Save</button></form>'
    )


def copy_text(project, key, path) -> str:
    return (
        f"Help me decide dev loop question {key} in project {project}. "
        f"The question, its options and the background are in {path}. "
        "Read it, explain the trade-off, and recommend an option."
    )


def _source(v, item, token, copy_path, note="") -> str:
    key, hold = item["answer_key"], item["answer_key"].startswith("hold:")
    rec, others = _option_forms(item, token)
    forms = []
    if rec is not None:
        forms.append(
            _form("/act/answer", item, token, f"{RECOMMENDED}: {rec}", choice=rec)
        )
    for opt in others:
        forms.append(_form("/act/answer", item, token, opt, choice=opt))
    if not hold:
        forms.append(_free_text(item, token))
    detail = f'<p class="detail">{e(item["detail"])}</p>' if item["detail"] else ""
    text = copy_text(v["project"], key, copy_path)
    cmd = f"devloop-state {v['project']} answer {key} <option>"
    cli = f'<p class="muted">Or in a terminal: <code>{e(cmd)}</code></p>'
    copy = f'<button type="button" class="copy" data-copy="{e(text)}">Copy for Claude</button>'
    return (
        f'<div id="qs-{e(key)}" data-title="{e(key)}" data-version="{e(item["version"])}">'
        f'<h3 class="qtitle">{e(item["question"] or "Question")}</h3>{detail}{note}'
        f'<div class="qforms">{"".join(forms)}</div>{cli}'
        f'<div class="qcopy">{copy}</div>'
        '<p class="qmsg muted" role="status" aria-live="polite"></p></div>'
    )


def _as_item(d):
    """A decided entry shaped like a needs_you item, so one renderer serves both."""
    return {**d, "answer_key": d["key"]}


def question_sources(v, token, copy_path) -> str:
    out = []
    for n in v["needs_you"]:
        if n["answer_key"] and n["version"]:
            out.append(_source(v, n, token, copy_path))
    for d in v["decided"]:
        now = f"Tech lead chose: {d['applied']}." if d["applied"] else ""
        if d["answer"]:
            now = f"You chose: {d['answer']}."
        note = f'<p class="muted">{e(now)} Pick another answer to override it.</p>'
        out.append(_source(v, _as_item(d), token, copy_path, note))
    return f'<section id="qsources" data-section="qsources" hidden>{"".join(out)}</section>'


def decided_list(v, now) -> str:
    items = []
    for d in v["decided"]:
        who = (
            f"You chose {e(d['answer'])}"
            if d["answer"]
            else f"Tech lead chose {e(d['applied'])}"
        )
        items.append(
            f"<li><code>{e(d['key'])}</code> {e(d['question'] or '')} "
            f'<span class="muted">{who} {r._time(d["at"], now)}</span> '
            f'<button type="button" class="link" data-open-kind="q" '
            f'data-open-key="{e(d["key"])}">Override</button></li>'
        )
    body = (
        f'<ul class="list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">Nothing decided for you yet.</p>'
    )
    return r._details("decided", "Decided for you", len(items), body)


def controls(v, token) -> str:
    c = v["controls"]
    pause = (
        _simple("/act/resume", token, "Resume")
        if c["paused"]
        else _simple("/act/pause", token, "Pause")
    )
    if c["tick_running"]:
        run = '<button type="button" disabled>A tick is running</button>'
    else:
        run = _simple("/act/tick", token, "Run a tick now")
    return f'<div class="controls">{pause}{run}<span class="cmsg muted" role="status"></span></div>'


def _simple(action, token, label) -> str:
    return (
        f'<form class="cform" method="post" action="{e(action)}">'
        f'{_hidden(t=token)}<button type="submit">{e(label)}</button></form>'
    )
