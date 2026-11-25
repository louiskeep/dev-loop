"""Command line for `devloop-dispatch`. It takes no argv: commands come from config."""

import argparse
import sys

from . import project as projmod
from . import runs, slices, store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devloop-dispatch")
    ap.add_argument("project")
    ap.add_argument("kind")
    ap.add_argument("key")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--worktree")
    ap.add_argument("--reviewer")
    ap.add_argument("--level")
    ap.add_argument("--select", nargs="*", default=[])
    ap.add_argument("--covers", nargs="*", default=[])
    ap.add_argument("--retry-of")
    args = ap.parse_args(argv)
    try:
        run_id = runs.dispatch(
            args.project,
            args.kind,
            args.key,
            brief=args.brief,
            worktree=args.worktree,
            reviewer=args.reviewer,
            level=args.level,
            select=args.select,
            covers=args.covers,
            retry_of=args.retry_of,
        )
    except (
        runs.Refused,
        runs.RunError,
        slices.SliceError,
        projmod.ConfigError,
        store.StoreError,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(run_id)
    return 0
