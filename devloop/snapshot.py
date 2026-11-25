"""The tick's snapshot: everything the model needs to decide, in one capped page.

It is read-only. The hash covers state that should wake the model, not clocks, so an
unchanged loop skips the model call.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import notes, runs, slices, store
from . import project as projmod
from .scrub import scrub

CAP = 60000
STALE_SECS = 2 * 3600
DEFAULT_CMD_CHARS = 4000
NODE_LINES = 60
DIGITS = re.compile(r"\d+")


def _age(iso) -> float:
    import datetime

    try:
        then = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds()


def _run_summary(rd, req):
    res = runs.parse_result(rd, req["kind"])
    return {
        "id": rd.name,
        "kind": req["kind"],
        "key": req.get("key"),
        "status": res.status,
        "verdict": res.verdict or "",
        "reason": res.reason,
    }


def finished_unapplied(project):
    return [
        _run_summary(rd, req)
        for rd, req in runs.iter_runs(project)
        if (rd / "exit").is_file() and not runs.is_done(rd)
    ]


def held_tests(project):
    """Ended test runs that still hold a tester slot until the node is checked."""
    out = []
    for rd, req in runs.iter_runs(project):
        if (
            req["kind"] == "test"
            and (rd / "exit").is_file()
            and runs.holds_slot(rd, req)
        ):
            ex = runs.read_exit(rd)
            out.append(
                {
                    "id": rd.name,
                    "host": req.get("host") or "-",
                    "job": req.get("job") or "-",
                    "reason": ex.get("reason"),
                    "code": ex.get("code"),
                    "age_min": int(_age(ex.get("ended_at") or req["started_at"]) // 60),
                }
            )
    return out


def _running_keys(project):
    return {r.get("key") for _, r in runs.live_runs(project)}


def next_command(project, s, checklist) -> str:
    """The command the tick is expected to run for this slice, from its checklist."""
    key, p = s["key"], s["phase"]
    missing = [name for name, ok in checklist if not ok]
    if p in ("DONE", "PARKED", "HELD"):
        return "none (owner decision needed)" if p != "DONE" else "none"
    forward = slices.FORWARD.get(p)
    if p == "GATE" and checklist and not missing:
        return f"devloop-merge {project} {key}"  # the only way out of GATE
    if forward and checklist and not missing:
        return f"devloop-state {project} slice move {key} {forward} --reason ..."
    hints = {
        "FRAME": f"devloop-state {project} slice set {key} --risk ... --rulebook ...",
        "PLAN": f"devloop-dispatch {project} planner|dennis|codex-review {key} --brief ...",
        "DEVELOP": f"devloop-dispatch {project} builder {key} --brief ...",
        "SELF_CHECK": f"read the diff, then devloop-state {project} selfcheck {key} GO --head {s['head'] or '<head>'}",
        "VERIFY": f"devloop-dispatch {project} test {key} --level t1 --brief ...",
        "REVIEW": f"devloop-dispatch {project} dennis|codex-review {key} --brief ...",
        "REMEDIATE": (
            f"devloop-dispatch {project} "
            + ("codex-builder" if s["round"] >= slices.CODEX_ROUNDS_FROM else "builder")
            + f" {key} --brief ..."
        ),
        "GATE": f"devloop-merge {project} {key}",
        "DOCUMENT": f"devloop-dispatch {project} docs {key} --brief ...",
    }
    return hints.get(p, "none")


def _slice_block(project, s, running):
    try:
        checklist = slices.exit_checklist(project, s["key"])
    except slices.SliceError:
        checklist = []
    lines = [
        f"- {s['key']}: {s['phase']}, risk {s['risk']}, head {(s['head'] or '-')[:8]}, "
        f"PR {s['pr'] or '-'}, round {s['round']}, "
        f"{'running' if s['key'] in running else 'idle'}, "
        f"last moved {int(_age(s['last_moved_at']) // 60)} min ago",
        f"  next action: {scrub(s['next_action'] or '-')}",
        f"  next command: {next_command(project, s, checklist)}",
    ]
    for name, ok in checklist:
        lines.append(f"  [{'x' if ok else ' '}] {scrub(name, 200)}")
    return lines


def command_outputs(project, cfg):
    """Run the configured snapshot commands: [(name, text, ok)]. Only the tick calls this."""
    out = []
    for spec in cfg.get("snapshot_cmds") or []:
        text, ok = "", False
        try:
            r = subprocess.run(
                spec["argv"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            text, ok = (r.stdout or "") + (r.stderr or ""), r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            text = "(command did not run)"
        limit = spec.get("max_chars", DEFAULT_CMD_CHARS)
        name = spec.get("name") or Path(spec["argv"][0]).name
        out.append((name, text[:limit], ok))
    return out


def record_node_status(project, outputs) -> None:
    """Keep name, exit status and time only, so the dashboard can show node health
    without ever running a command or reading output text."""
    at = store.now_iso()
    store.write_json(
        store.state_dir(project) / "node-status.json",
        {name: {"ok": ok, "at": at} for name, _text, ok in outputs},
    )


def _questions(project):
    path = notes.questions_path(project)
    if not path.is_file():
        return []
    _, qs, _, _ = notes.load_questions(path)
    return [q for q in qs.values() if q["wait"] and not q["answer"]]


def _node_summary(cfg):
    directory = (cfg.get("nodes") or {}).get("directory")
    if not directory:
        return ""
    try:
        text = Path(directory).read_text(encoding="utf-8")
    except OSError:
        return "(node directory not readable)"
    return "\n".join(text.splitlines()[:NODE_LINES])


def build_snapshot(project) -> tuple:
    cfg = projmod.load_project(project)
    running = _running_keys(project)
    all_slices = slices.list_slices(project)
    active = [s for s in all_slices if s["phase"] in slices.ACTIVE]
    stale = [
        s
        for s in active
        if s["key"] not in running and _age(s["last_moved_at"]) > STALE_SECS
    ]
    finished = finished_unapplied(project)
    failed = [r for r in finished if r["status"] == "FAILED"]
    qs = _questions(project)
    sd = store.state_dir(project)
    problems = [
        e
        for e in store.read_json(sd / "problems.json").get("problems", {}).values()
        if e.get("open")
    ]
    ticks = notes.read_ticklog(sd / "tick-log.md")[-5:]
    areas = sorted({s["row"] for s in active})
    outputs = command_outputs(project, cfg)
    record_node_status(project, outputs)

    used = runs.slot_usage(project)
    free = {k: cfg["capacity"][k] - used.get(k, 0) for k in cfg["capacity"]}
    mem_gb = runs.mem_available_kb() / 1024 / 1024

    sections = []
    sections.append(
        ["## Stale and failed"]
        + (
            [f"- stale: {s['key']} in {s['phase']}" for s in stale]
            + [
                f"- failed run: {r['id']} ({r['kind']}, {r['reason'] or 'no reason'})"
                for r in failed
            ]
            or ["- none"]
        )
    )
    sections.append(
        ["## Finished runs not yet applied"]
        + (
            [
                f"- {r['id']}: {r['kind']} on {r['key']}, {r['status']}"
                f"{', ' + r['verdict'] if r['verdict'] else ''}"
                f"  -> devloop-state {project} applied {r['id']}"
                for r in finished
            ]
            or ["- none"]
        )
    )
    inflight = ["## In flight"]
    for s in active:
        inflight += _slice_block(project, s, running)
    sections.append(inflight if len(inflight) > 1 else inflight + ["- none"])
    held = held_tests(project)
    sections.append(
        ["## Test runs holding a slot until release"]
        + (
            [
                f"- {h['id']}: host {h['host']}, job {h['job']}, ended {h['reason']} "
                f"code {h['code']}, {h['age_min']} min ago"
                f"  -> check the node, then devloop-state {project} release {h['id']}"
                for h in held
            ]
            or ["- none"]
        )
    )
    sections.append(
        [
            "## Free slots and memory",
            "- " + ", ".join(f"{k} {v}" for k, v in sorted(free.items())),
            f"- memory available: {mem_gb:.1f} GB (floor {cfg.get('min_free_gb', 2.5)} GB)",
        ]
    )
    closable = slices.closable_rows(project)
    sections.append(
        ["## Rows ready to close"]
        + (
            [
                f"- {r}: devloop-state {project} row-close {r} --confirm-last"
                "  (only when no roadmap slice remains for it)"
                for r in closable
            ]
            or ["- none"]
        )
    )
    sections.append(
        ["## Open questions and R3 waits"]
        + (
            [
                f"- {q['key']}: {scrub(q['question'], 200)} (recommended: {q['recommended']})"
                for q in qs
            ]
            + [f"- held: {s['key']}" for s in all_slices if s["phase"] == "HELD"]
            or ["- none"]
        )
    )
    sections.append(
        ["## Open known problems"]
        + (
            [
                f"- {e['key']} (x{e['count']}): {scrub(e['impact'], 200)}"
                for e in problems
            ]
            or ["- none"]
        )
    )
    sections.append(
        ["## Last tick-log entries"]
        + ([f"- {at}: {scrub(body, 400)}" for at, body in ticks] or ["- none"])
    )
    lessons = []
    for area in areas:
        for e in notes.lessons_for(project, area):
            lessons.append(
                f"- {area}/{e['key']}: wrong: {scrub(e['wrong'], 200)} Instead: {scrub(e['instead'], 200)}"
            )
    sections.append(["## Lessons for work in flight"] + (lessons or ["- none"]))
    node = _node_summary(cfg)
    if node:
        sections.append(["## Nodes (summary)", node])

    head = "\n".join("\n".join(s) for s in sections)
    # Command outputs go last so the cap trims the last-listed first.
    keep = list(outputs)
    while True:
        tail = "".join(f"\n\n## {name}\n{text}" for name, text, _ok in keep)
        md = head + tail
        if len(md) <= CAP or not keep:
            break
        keep.pop()
    md = md[:CAP]

    stable = {
        "slices": [
            [s["key"], s["phase"], s["head"], s["round"], s["run"] is not None]
            for s in all_slices
        ],
        "runs": sorted(
            (rd.name, (rd / "exit").is_file(), runs.is_done(rd))
            for rd, _ in runs.iter_runs(project)
        ),
        "closable": closable,
        "held_tests": sorted((h["id"], h["reason"], h["code"]) for h in held),
        "questions": sorted(q["key"] for q in qs),
        "problems": sorted((e["key"], e["count"]) for e in problems),
        "outputs": [(n, DIGITS.sub("", t)) for n, t, _ok in outputs],
    }
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    return md, digest
