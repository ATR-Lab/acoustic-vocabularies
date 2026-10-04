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

_GIT = "git"  # executable name; tests replace it to simulate a machine without git


def _committable(path: Path) -> bool:
    """True if ``path`` could be committed: inside a git work tree and not git-ignored.

    Used to refuse private seeds or private outputs where they could be committed. Fails
    closed: if a ``.git`` entry exists in ``path`` or a parent directory but git cannot
    be run or cannot answer, the path counts as committable.
    """
    target = path.resolve()
    start = target if target.is_dir() else target.parent
    while not start.exists():
        start = start.parent
    in_repo = any((p / ".git").exists() for p in (start, *start.parents))
    try:
        top = subprocess.run(
            [_GIT, "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return in_repo  # git unavailable: refuse whenever a repository is present
    if top.returncode != 0:
        return in_repo  # git ran but could not answer
    root = Path(top.stdout.strip()).resolve()
    probe = target if target.is_file() else target / "probe"
    try:
        rel = probe.relative_to(root).as_posix()
    except ValueError:
        return True  # inside a work tree but not mappable: be conservative
    try:
        ignored = subprocess.run(
            [_GIT, "-C", str(root), "check-ignore", "-q", "--", rel],
            capture_output=True,
            check=False,
        )
    except OSError:
        return True
    return ignored.returncode != 0


def _refuse(message: str) -> int:
    print(f"refusing: {message}", file=sys.stderr)
    return 2


def _cmd_curriculum(args: argparse.Namespace) -> int:
    master: MasterSeed
    studies: list[Study] = ["A", "B"] if args.study == "both" else [args.study]
    sets: list[SetName] = list(SET_NAMES) if args.set == "both" else [args.set]
    if args.demo_seed is not None:
        master = demo_seed(args.demo_seed)
    else:
        if len(sets) > 1:
            return _refuse(
                "a private master seed serves one set only; run --set pilot and "
                "--set confirmatory separately with different master seed files"
            )
        seed_file = Path(args.master_seed_file)
        if _committable(seed_file):
            return _refuse(f"{seed_file} is inside a git work tree and not ignored")
        master = load_master_seed(seed_file)
    out = Path(args.out) if args.out else default_out_dir()
    if not master.demo and _committable(out):
        return _refuse(f"output {out} is inside a git work tree and not ignored")
    # Check every requested set before writing any of them.
    for study in studies:
        study_dir = out / study
        for set_name in sets:
            previous = existing_seed_label(study_dir, set_name)
            if previous is not None and previous != master.label and not args.force:
                return _refuse(
                    f"{study_dir} already holds a {set_name} set from another seed "
                    "(use --force to overwrite)"
                )
            for other in SET_NAMES:
                if other == set_name or master.demo:
                    continue
                if existing_seed_label(study_dir, other) == master.label:
                    return _refuse(
                        f"the {other} set in {study_dir} was generated from this master seed; "
                        "pilot and confirmatory sets need different master seeds"
                    )
    for study in studies:
        study_dir = out / study
        for set_name in sets:
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
    cur.add_argument(
        "--set",
        choices=("pilot", "confirmatory", "both"),
        default="both",
        help="'both' only with --demo-seed: each private master seed serves one set",
    )
    cur.add_argument(
        "--spares",
        type=int,
        default=B_DEFAULT_SPARES,
        help="Study B spare slots (multiple of 4, at most 96)",
    )
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
