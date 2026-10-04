"""Command line: ``python -m av_schedules {curriculum,check-planning,demo-examples}``."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from ._paths import default_out_dir, examples_dir
from .design import B_DEFAULT_SPARES, SET_NAMES, SetName
from .matrix import Study
from .output import demo_example_files, existing_seed_label, generate, write_files
from .planning import check_planning
from .seeds import MasterSeed, demo_seed, load_master_seed


def _committable(path: Path) -> bool:
    """True if ``path`` lies in a git work tree and is not git-ignored there.

    Used to refuse private seeds or private outputs in a place where they could be
    committed. Paths outside any work tree (or without git installed) are fine.
    """
    target = path.resolve()
    start = target if target.is_dir() else target.parent
    while not start.exists():
        start = start.parent
    try:
        top = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if top.returncode != 0:
        return False
    root = Path(top.stdout.strip()).resolve()
    probe = target if target.is_file() else target / "probe"
    try:
        rel = probe.relative_to(root).as_posix()
    except ValueError:
        return True  # inside a work tree but not mappable: be conservative
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", rel],
        capture_output=True,
        check=False,
    )
    return ignored.returncode != 0


def _cmd_curriculum(args: argparse.Namespace) -> int:
    master: MasterSeed
    if args.demo_seed is not None:
        master = demo_seed(args.demo_seed)
    else:
        seed_file = Path(args.master_seed_file)
        if _committable(seed_file):
            print(
                f"refusing: {seed_file} is inside a git work tree and not ignored", file=sys.stderr
            )
            return 2
        master = load_master_seed(seed_file)
    out = Path(args.out) if args.out else default_out_dir()
    if not master.demo and _committable(out):
        print(f"refusing: output {out} is inside a git work tree and not ignored", file=sys.stderr)
        return 2
    studies: list[Study] = ["A", "B"] if args.study == "both" else [args.study]
    sets: list[SetName] = list(SET_NAMES) if args.set == "both" else [args.set]
    for study in studies:
        for set_name in sets:
            study_dir = out / study
            previous = existing_seed_label(study_dir, set_name)
            if previous is not None and previous != master.label and not args.force:
                print(
                    f"refusing: {study_dir} already holds a {set_name} set from another seed "
                    "(use --force to overwrite)",
                    file=sys.stderr,
                )
                return 2
            files = generate(master, study, set_name, spares=args.spares)
            write_files(study_dir, files)
            manifest = files[f"{set_name}-manifest.json"]
            units = sum(1 for p in files if p.endswith("/curriculum.csv"))
            print(
                f"{study} {set_name}: {units} units -> {study_dir} "
                f"manifest sha256 {hashlib.sha256(manifest).hexdigest()}"
            )
    if master.demo:
        print(f"DEMO seed {master.value}: outputs are public examples, not study material")
    return 0


def _cmd_check_planning(args: argparse.Namespace) -> int:
    result = check_planning(Path(args.directory))
    for name in result.checked:
        print(f"checked {name}")
    for line in result.drift:
        print(f"DRIFT {line}")
    for line in result.problems:
        print(f"MISMATCH {line}")
    if result.ok:
        print("PASS: planning materials match the matrix constant")
        return 0
    return 1


def _cmd_demo_examples(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else examples_dir()
    write_files(out, demo_example_files())
    print(f"wrote DEMO examples to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="av-schedules", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cur = sub.add_parser("curriculum", help="generate per-unit curricula and permutations")
    seed = cur.add_mutually_exclusive_group(required=True)
    seed.add_argument("--master-seed-file", help="private master seed file (never commit it)")
    seed.add_argument("--demo-seed", help="public demonstration seed starting with DEMO-")
    cur.add_argument("--study", choices=("A", "B", "both"), default="both")
    cur.add_argument("--set", choices=("pilot", "confirmatory", "both"), default="both")
    cur.add_argument("--spares", type=int, default=B_DEFAULT_SPARES, help="Study B spare slots")
    cur.add_argument("--out", help="output root (default: schedules/out)")
    cur.add_argument("--force", action="store_true", help="overwrite a set from another seed")
    cur.set_defaults(func=_cmd_curriculum)

    chk = sub.add_parser("check-planning", help="compare external planning materials")
    chk.add_argument("directory", help="folder holding curriculum.csv and ontology.csv")
    chk.set_defaults(func=_cmd_check_planning)

    dem = sub.add_parser("demo-examples", help="regenerate schedules/examples/demo")
    dem.add_argument("--out", help="output folder (default: schedules/examples/demo)")
    dem.set_defaults(func=_cmd_demo_examples)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return code
