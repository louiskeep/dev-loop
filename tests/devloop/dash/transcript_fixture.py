"""Synthetic worker output files with the real shapes (field names only, no real values)."""

import json


def init(model="claude-opus-5-5", cwd="/work/tree"):
    return {"type": "system", "subtype": "init", "model": model, "cwd": cwd}


def tool_use(call_id, name, **inp):
    return {"type": "tool_use", "id": call_id, "name": name, "input": inp}


def text(value):
    return {"type": "text", "text": value}


def thinking(value=""):
    return {"type": "thinking", "thinking": value}


def assistant(msg_id, *blocks, out=10, inp=2, cache_read=0, cache_create=0):
    return {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "content": list(blocks),
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
        },
    }


def tool_result(call_id, is_error=False, content=""):
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "is_error": is_error,
        "content": content,
    }


def user(*results):
    return {"type": "user", "message": {"role": "user", "content": list(results)}}


def result(value="done"):
    return {"type": "result", "subtype": "success", "result": value}


def claude_events(items) -> bytes:
    """One JSON object per line; `bytes` items pass through as raw lines."""
    out = []
    for item in items:
        out.append(item if isinstance(item, bytes) else json.dumps(item).encode())
    return b"\n".join(out) + b"\n"


# --- Codex text -------------------------------------------------------------


def codex_header(model="gpt-6.2-astra"):
    return (
        "Reading additional input from stdin...\n"
        "OpenAI Codex v0.171.0\n"
        "--------\n"
        "workdir: /work/tree\n"
        f"model: {model}\n"
        "provider: openai\n"
        "sandbox: read-only\n"
        "--------\n"
    )


def codex_exec(cmd, code=0, output="", quote='"'):
    """The real shape: a quoted `/bin/bash -lc` wrapper and a trailing directory."""
    status = " succeeded in 3ms:" if code == 0 else f" exited {code} in 3ms:"
    return (
        f"exec\n/bin/bash -lc {quote}{cmd}{quote} in /work/tree\n{status}\n{output}"
        + ("\n" if output and not output.endswith("\n") else "")
    )


def codex_patch(*lines):
    return (
        "apply_patch(auto_approved=true) exited 0 in 4ms:\n" + "\n".join(lines) + "\n"
    )


def codex_text(blocks) -> bytes:
    parts = []
    for b in blocks:
        parts.append(b)
    return "".join(parts).encode()
