"""Command line: ``av-analysis <command>`` (also ``python -m av_analysis``).

Shared commands (implemented in the skeleton): ``init-root``, ``schemas``,
``check-templates`` and ``refresh`` (the operator sequence after a visit: ``reconcile
--all``, ``derive``, ``dashboard`` on one data root; a step whose command is not
implemented yet is skipped). Issue commands are registered from their owning modules,
each of which defines ``add_arguments(parser)`` and ``main(args) -> int``, so an issue
edits only its own module:

=============  ========================  =====
Command        Module                    Owner
=============  ========================  =====
``synth-logs`` ``synthetic_logs``        #33
``reconcile``  ``reconcile``             #33
``derive``     ``derive``                #33
``run``        ``pipeline``              #34
``simulate``   ``simulate``              #34
``dashboard``  ``monitoring``            #35
=============  ========================  =====

Exit codes: 0 success, 1 findings (failed checks, drift), 2 usage or refused input,
3 command not implemented yet.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, cast

from . import __version__
from .paths import DataRoot, WatermarkError
from .schemas import check_schema_files, write_schema_files
from .templates import check_external

ISSUE_COMMANDS: Final[dict[str, tuple[str, str, str]]] = {
    "synth-logs": (
        "synthetic_logs",
        "#33",
        "write a SYNTHETIC data root with raw logs for every visit type",
    ),
    "reconcile": ("reconcile", "#33", "reconcile visits (checks C1-C8) and write reports"),
    "derive": ("derive", "#33", "write the reconciled and derived tables of a data root"),
    "run": ("pipeline", "#34", "produce the analysis plan section 9 outputs"),
    "simulate": ("simulate", "#34", "synthetic datasets and operating characteristics"),
    "dashboard": ("monitoring", "#35", "regenerate the integrity monitoring dashboard"),
}
# ``refresh``: the issue commands run in order, each with ``--root`` (and these arguments).
REFRESH_STEPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("reconcile", ("--all",)),
    ("derive", ()),
    ("dashboard", ()),
)
ENV_TEMPLATES: Final = "AV_TEMPLATES_DIR"
ENV_PLANNING: Final = "AV_PLANNING_DIR"

Handler = Callable[[argparse.Namespace], int]


def templates_dir_from_env() -> Path | None:
    """``$AV_TEMPLATES_DIR``, else ``templates/`` next to ``$AV_PLANNING_DIR``, else None."""
    explicit = os.environ.get(ENV_TEMPLATES)
    if explicit:
        return Path(explicit)
    planning = os.environ.get(ENV_PLANNING)
    return Path(planning).parent / "templates" if planning else None


def _init_root(args: argparse.Namespace) -> int:
    try:
        root = DataRoot.create(
            Path(args.path),
            args.kind,
            study=args.study,
            set_name=args.set,
            label=args.label,
        )
    except (ValueError, WatermarkError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(f"{root.data_kind} data root {root.path} ({root.label})")
    return 0


def _schemas(args: argparse.Namespace) -> int:
    if args.write:
        for name in write_schema_files():
            print(f"wrote {name}")
        return 0
    problems = check_schema_files()
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def _check_templates(args: argparse.Namespace) -> int:
    directory = Path(args.dir) if args.dir else templates_dir_from_env()
    if directory is None:
        print(f"give a folder or set {ENV_TEMPLATES} / {ENV_PLANNING}", file=sys.stderr)
        return 2
    result = check_external(directory)
    for name in result.checked:
        print(f"checked {name}")
    for p in result.problems:
        print(f"problem: {p}", file=sys.stderr)
    for d in result.drift:
        print(f"drift: {d}", file=sys.stderr)
    return 0 if result.ok and not result.drift else 1


def _refresh(args: argparse.Namespace) -> int:
    """Run :data:`REFRESH_STEPS`: continue after findings (exit 1) so the tables and the
    dashboard show them; stop at a refused input (exit 2 or more). Exit 3 if no step ran."""
    parser, handlers = build_parser()
    worst, ran = 0, 0
    for step, extra in REFRESH_STEPS:
        step_args = parser.parse_args([step, "--root", args.root, *extra])
        try:
            code = handlers[step](step_args)
        except NotImplementedError as exc:
            print(f"refresh: {step} skipped (not implemented yet: {exc})", file=sys.stderr)
            continue
        ran += 1
        print(f"refresh: {step} exit {code}")
        if code >= 2:
            return code
        worst = max(worst, code)
    return worst if ran else 3


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Handler]]:
    """The argument parser and the handler of every command."""
    parser = argparse.ArgumentParser(
        prog="av-analysis",
        description="Reconciliation, analysis and integrity monitoring (synthetic data only "
        "in this repository).",
    )
    parser.add_argument("--version", action="version", version=f"av-analysis {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    handlers: dict[str, Handler] = {}

    p = sub.add_parser("init-root", help="create a data root marker (SYNTHETIC or REAL)")
    p.add_argument("path")
    p.add_argument("--kind", choices=("SYNTHETIC", "REAL"), required=True)
    p.add_argument("--study", choices=("A", "B", "both"), default="both")
    p.add_argument("--set", choices=("pilot", "confirmatory", "both"), default="both")
    p.add_argument("--label", required=True, help="DEMO-... for SYNTHETIC roots")
    handlers["init-root"] = _init_root

    p = sub.add_parser("schemas", help="check (default) or rewrite analysis/schema/*.json")
    p.add_argument("--write", action="store_true")
    handlers["schemas"] = _schemas

    p = sub.add_parser(
        "check-templates", help="compare external methodology templates with the oracles"
    )
    p.add_argument("dir", nargs="?", help=f"templates folder (default ${ENV_TEMPLATES})")
    handlers["check-templates"] = _check_templates

    p = sub.add_parser(
        "refresh", help="after a visit: reconcile --all, derive, dashboard (skips missing steps)"
    )
    p.add_argument("--root", required=True, help="data root (av-data-root.json)")
    handlers["refresh"] = _refresh

    for name, (module_name, owner, help_text) in ISSUE_COMMANDS.items():
        module = importlib.import_module(f"{__package__}.{module_name}")
        p = sub.add_parser(name, help=f"{help_text} ({owner})")
        module.add_arguments(p)
        handlers[name] = cast(Handler, module.main)
    return parser, handlers


def main(argv: Sequence[str] | None = None) -> int:
    parser, handlers = build_parser()
    args = parser.parse_args(argv)
    try:
        return handlers[args.command](args)
    except NotImplementedError as exc:
        print(f"av-analysis {args.command}: not implemented yet ({exc})", file=sys.stderr)
        return 3
