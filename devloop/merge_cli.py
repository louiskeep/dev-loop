"""Command line for `devloop-merge <project> <slice key>`."""

import sys

from . import merge, slices, store
from . import project as projmod


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: devloop-merge PROJECT SLICE-KEY", file=sys.stderr)
        return 2
    try:
        result = merge.merge(*args)
    except (slices.SliceError, projmod.ConfigError, store.StoreError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(f"{result.outcome}: {result.reason}")
    return {"merged": 0, "refused": 3, "unknown": 4}[result.outcome]
