"""``python -m av_analysis.power_check run --config X --out DIR [--replicates N]``.

Exit codes as ``av-analysis``: 0 success, 2 usage, invalid configuration or refused
output directory. The outputs may land inside a committable path of a git work tree
(``paths.committable``: inside a work tree and not git-ignored) only when the
configuration is the committed planning example; any other configuration (above all a
pilot-informed one) is written outside the repository or into an ignored folder.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from ..paths import committable
from .config import ConfigError, is_planning_example, load_document, parse_config
from .run import Cell, run_config, verdict, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_analysis.power_check",
        description="Simulated power of the primary contrasts over a size grid (#309).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="simulate the grid of a configuration and write the outputs")
    run.add_argument("--config", required=True, type=Path, help="power-check JSON configuration")
    run.add_argument("--out", required=True, type=Path, help="output directory")
    run.add_argument(
        "--replicates", type=int, default=None, help="replicates per cell (overrides the config)"
    )
    run.add_argument("--quiet", action="store_true", help="no progress lines")
    return parser


def _progress(cell: Cell, i: int, total: int) -> None:
    print(
        f"[{i}/{total}] {cell.grid.study} {cell.heterogeneity.name} {cell.scenario.name} "
        f"loss {cell.loss:g} n {cell.size}",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        doc = load_document(args.config)
        config = parse_config(doc)
        if args.replicates is not None:
            config = config.with_replicates(args.replicates)
    except ConfigError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    out: Path = args.out
    if committable(out) and not is_planning_example(doc):
        print(
            "refusing: only the committed planning example may write inside a git work tree; "
            "write other configurations outside the repository (restricted storage)",
            file=sys.stderr,
        )
        return 2
    start = time.perf_counter()
    rows = run_config(config, None if args.quiet else _progress)
    for path in write_outputs(out, config, rows):
        print(f"wrote {path}")
    for c in verdict(config, rows)["contrasts"]:
        smallest = c["all_cases"]["smallest_size"]
        reached = "not reached in the grid" if smallest is None else f"smallest size {smallest}"
        print(f"Study {c['study']} {c['contrast']}: {reached} (all cases, point estimate)")
    print(f"{len(rows)} rows in {time.perf_counter() - start:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
