"""Command line for `devloop-state`: the only way the tick model changes slice state."""

import argparse
import json
import sys

from . import check, merge, notes, runs, slices, store
from . import project as projmod

REFUSALS = (
    slices.SliceError,
    notes.NotesError,
    runs.RunError,
    projmod.ConfigError,
    store.StoreError,
    store.HeadlessRefused,
)


def _out(obj):
    print(json.dumps(obj, indent=2, sort_keys=True))


def _line(s):
    return f"{s['key']} {s['phase']} head={(s['head'] or '-')[:8]} round={s['round']}"


def _show(args, s):
    """One short line by default so a tick does not read a whole slice; --json for all."""
    if args.json:
        _out(s)
    else:
        print(_line(s))


def _slice(args):
    p = args.project
    if args.action == "new":
        _show(
            args,
            slices.new_slice(p, args.row, args.slice, repo=args.repo, risk=args.risk),
        )
    elif args.action == "set":
        fields = {}
        for name in (
            "worktree",
            "branch",
            "pr",
            "head",
            "plan_path",
            "purpose",
            "next_action",
        ):
            value = getattr(args, name)
            if value is not None:
                fields[name] = value
        for name in ("waiting_on", "risk"):
            value = getattr(args, name)
            if value is not None:
                fields[name] = value
        if args.rulebook:
            fields["rulebooks"] = args.rulebook
        if args.full_suite_required:
            fields["full_suite_required"] = True
        _show(args, slices.set_fields(p, args.key, **fields))
    elif args.action == "move":
        _show(args, slices.move(p, args.key, args.phase, args.reason))
    elif args.action == "show":
        _out(slices.load(p, args.key))
    elif args.action == "list":
        for s in slices.list_slices(p):
            print(f"{s['key']} {s['phase']}")
    elif args.action == "import":
        _show(
            args,
            slices.import_slice(
                p,
                args.row,
                args.slice,
                repo=args.repo,
                risk=args.risk,
                rulebooks=args.rulebook or [],
                worktree=args.worktree,
                branch=args.branch,
                pr=args.pr,
                head=args.head,
                plan_path=args.plan_path,
                purpose=args.purpose,
                phase=args.phase,
                plan_approved=args.plan_approved,
            ),
        )


def _event(args):
    fields = {}
    for pair in args.pairs:
        name, sep, value = pair.partition("=")
        if not sep or store.TOKEN.fullmatch(name) is None or name in ("at", "kind"):
            raise store.StoreError("event fields are k=v with a plain key")
        fields[name] = value
    store.append_event(args.project, args.kind, **fields)


def _notes(args):
    p = args.project
    if args.cmd == "ticklog":
        notes.ticklog(p, sys.stdin.read() if args.text == "-" else args.text)
    elif args.cmd == "problem":
        notes.problem(p, args.key, impact=args.impact, fix=args.fix, detail=args.detail)
    elif args.cmd == "problem-close":
        notes.problem_close(p, args.key)
    elif args.cmd == "lesson":
        notes.lesson(
            p, args.key, area=args.area, wrong=args.wrong, instead=args.instead
        )
    elif args.cmd == "ask":
        q = notes.ask(
            p,
            args.key,
            question=args.question,
            options=args.option,
            recommended=args.recommended,
            wait=args.wait,
        )
        _out(q)
    elif args.cmd == "answer":
        notes.answer(p, args.key, args.choice)
    elif args.cmd == "answers-sync":
        for key in notes.read_answers(p):
            print(key)


def _runs(args):
    p = args.project
    if args.cmd == "applied":
        print(runs.apply_run(p, args.run))
    elif args.cmd == "release":
        runs.release(p, args.run)
    elif args.cmd == "discard":
        runs.discard(p, args.run)
    elif args.cmd == "list":
        if args.live:
            for run_id, req in runs.live_runs(p):
                print(run_id, req["kind"], req["key"])
        if args.writers:
            for run_id, req in runs.writer_runs(p):
                print(run_id, req["kind"], req.get("root"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="devloop-state")
    ap.add_argument("project")
    sub = ap.add_subparsers(dest="cmd", required=True)

    jp = argparse.ArgumentParser(add_help=False)
    jp.add_argument("--json", action="store_true", help="print the full slice as JSON")
    sl = sub.add_parser("slice").add_subparsers(dest="action", required=True)
    n = sl.add_parser("new", parents=[jp])
    n.add_argument("row")
    n.add_argument("slice")
    n.add_argument("--repo", required=True)
    n.add_argument("--risk")
    st = sl.add_parser("set", parents=[jp])
    st.add_argument("key")
    for flag in ("worktree", "branch", "head", "plan-path", "purpose", "next-action"):
        st.add_argument(f"--{flag}")
    st.add_argument("--pr", type=int)
    st.add_argument("--waiting-on")
    st.add_argument("--risk")
    st.add_argument("--rulebook", action="append")
    st.add_argument("--full-suite-required", action="store_true")
    mv = sl.add_parser("move", parents=[jp])
    mv.add_argument("key")
    mv.add_argument("phase")
    mv.add_argument("--reason", required=True)
    sl.add_parser("show").add_argument("key")
    sl.add_parser("list")
    im = sl.add_parser("import", parents=[jp])
    im.add_argument("row")
    im.add_argument("slice")
    im.add_argument("--repo", required=True)
    im.add_argument("--risk")
    im.add_argument("--rulebook", action="append")
    for flag in ("worktree", "branch", "head", "plan-path", "purpose"):
        im.add_argument(f"--{flag}")
    im.add_argument("--pr", type=int)
    im.add_argument("--phase", required=True)
    im.add_argument("--plan-approved", action="store_true")

    for name, verdicts in (("selfcheck", ("GO", "NO-GO")), ("docs", ("GO",))):
        c = sub.add_parser(name)
        c.add_argument("key")
        c.add_argument("verdict", choices=verdicts)
        c.add_argument("--head", required=True)
    ev = sub.add_parser("event")
    ev.add_argument("kind")
    ev.add_argument("pairs", nargs="*")

    sub.add_parser("ticklog").add_argument("text")
    pr = sub.add_parser("problem")
    pr.add_argument("key")
    pr.add_argument("--impact", required=True)
    pr.add_argument("--fix", required=True)
    pr.add_argument("--detail", default="")
    sub.add_parser("problem-close").add_argument("key")
    le = sub.add_parser("lesson")
    le.add_argument("key")
    for flag in ("area", "wrong", "instead"):
        le.add_argument(f"--{flag}", required=True)
    aq = sub.add_parser("ask")
    aq.add_argument("key")
    aq.add_argument("--question", required=True)
    aq.add_argument("--option", action="append", required=True)
    aq.add_argument("--recommended", required=True)
    aq.add_argument("--wait", action="store_true")
    an = sub.add_parser("answer")
    an.add_argument("key")
    an.add_argument("choice")
    sub.add_parser("answers-sync")
    sub.add_parser("check")
    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("applied").add_argument("run")
    sub.add_parser("release").add_argument("run")
    sub.add_parser("discard").add_argument("run")
    sub.add_parser("merge-reset").add_argument("key")
    mg = sub.add_parser("merged")
    mg.add_argument("key")
    mg.add_argument("--pr", type=int, required=True)
    mg.add_argument("--head", required=True)
    pu = sub.add_parser("pushed")
    pu.add_argument("key")
    pu.add_argument("run")
    rc = sub.add_parser("row-close")
    rc.add_argument("row")
    rc.add_argument("--confirm-last", action="store_true")
    sub.add_parser("notify").add_argument("text")
    ls = sub.add_parser("list")
    ls.add_argument("--live", action="store_true")
    ls.add_argument("--writers", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "slice":
            _slice(args)
        elif args.cmd in ("selfcheck", "docs"):
            getattr(slices, args.cmd)(
                args.project, args.key, args.verdict, head=args.head
            )
        elif args.cmd in ("applied", "release", "discard", "list"):
            _runs(args)
        elif args.cmd == "row-close":
            ok = slices.close_row_checked(
                args.project, args.row, confirm_last=args.confirm_last
            )
            print(f"{args.row} {'closed' if ok else 'not closed'}")
            return 0 if ok else 3
        elif args.cmd == "pushed":
            slices.pushed(args.project, args.key, args.run)
            print(f"{args.key} head credited to {args.run}")
        elif args.cmd == "merged":
            res = merge.record_direct(args.project, args.key, args.pr, args.head)
            if res.outcome != "merged":
                print(f"REFUSED: {res.reason}", file=sys.stderr)
                return 3
            print(f"{args.key} merge recorded")
        elif args.cmd == "merge-reset":
            slices.merge_reset(args.project, args.key)
        elif args.cmd in ("pause", "resume"):
            flag = store.state_dir(args.project) / "PAUSE"
            if args.cmd == "pause":
                flag.touch()
            else:
                flag.unlink(missing_ok=True)
        elif args.cmd == "check":
            results = check.run_checks(args.project)
            for ok, msg in results:
                print(f"{'ok' if ok else 'FAIL'}: {msg}")
            return 0 if all(ok for ok, _ in results) else 3
        elif args.cmd == "event":
            _event(args)
        elif args.cmd == "notify":
            # Text from the argument, or stdin when it is "-". notes.notify scrubs it, keeps
            # line breaks and cuts it to 1500 characters before it sends.
            text = sys.stdin.read() if args.text == "-" else args.text
            notes.notify(
                args.project, text, limit=notes.MULTILINE_LIMIT, multiline=True
            )
        else:
            _notes(args)
    except REFUSALS as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    return 0
