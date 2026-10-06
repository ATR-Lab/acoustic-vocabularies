"""Command line for the A1 designer interface (#19): practice sessions for training.

    python -m av_generation._a1_cli practice --runs-root <restricted dir> --run-id <id>
        --designer D1 [--meanings <practice meaning set dir>] [--atoms K-a1,K-r1]
        [--rounds 4] [--profile P2] [--host 127.0.0.1] [--port 8741] [--station S9]
        [--demo]

Creates the practice run (`_a1_practice.open_practice_session`), runs the practice
rounds in a background thread and serves the A1 app until interrupted (Ctrl-C). The
kiosk browser opens `http://<host>:<port>/a1/` (generation/docs/a1-interface.md). Study
sessions are not started here: the round orchestrator (#20) owns the batch run and
serves `create_a1_app(service)` for its A1 service.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable, Sequence
from typing import Any

from av_generation._a1_practice import (
    PRACTICE_ATOMS,
    LedgerFactory,
    PracticeSession,
    demo_practice_meanings,
    open_practice_session,
    practice_atoms,
)
from av_generation.a1 import create_a1_app
from av_generation.clock import SystemClock
from av_generation.constants import ROUNDS_PER_ATOM
from av_generation.ids import RunKind
from av_generation.meanings import load_meanings

DEFAULT_PORT = 8741

Serve = Callable[[Any, str, int, PracticeSession], None]


def _serve_uvicorn(app: Any, host: str, port: int, session: PracticeSession) -> None:  # noqa: ANN401
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation._a1_cli",
        description="A1 designer interface: practice sessions (training; never a book).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    practice = sub.add_parser("practice", help="run a practice session and serve the A1 page")
    practice.add_argument("--runs-root", required=True, help="restricted runs directory")
    practice.add_argument("--run-id", required=True, help="new practice run ID")
    practice.add_argument("--designer", required=True, help="coded designer ID, e.g. D1")
    practice.add_argument(
        "--meanings",
        default=None,
        help="practice meaning set directory (non-study texts; default: the DEMO set)",
    )
    practice.add_argument("--atoms", default=",".join(PRACTICE_ATOMS))
    practice.add_argument("--rounds", type=int, default=ROUNDS_PER_ATOM)
    practice.add_argument("--profile", default="P2", choices=("P1", "P2", "P3"))
    practice.add_argument("--host", default="127.0.0.1")
    practice.add_argument("--port", type=int, default=DEFAULT_PORT)
    practice.add_argument("--station", default=None, help="kiosk station ID, e.g. S9")
    practice.add_argument(
        "--demo", action="store_true", help="synthetic trial run (DEMO- run ID, kind demo)"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    serve: Serve | None = None,
    ledger_factory: LedgerFactory | None = None,
) -> int:
    """Entry point; `serve` and `ledger_factory` are injectable for tests."""
    args = build_parser().parse_args(argv)
    meanings = load_meanings(args.meanings) if args.meanings else demo_practice_meanings()
    session = open_practice_session(
        args.runs_root,
        args.run_id,
        designer_id=args.designer,
        meanings=meanings,
        clock=SystemClock(),
        profile=args.profile,
        kind=RunKind.DEMO if args.demo else RunKind.PRACTICE,
        station=args.station,
        ledger_factory=ledger_factory,
    )
    atoms = practice_atoms(args.atoms)
    stop = threading.Event()
    driver = threading.Thread(
        target=session.run,
        args=(atoms,),
        kwargs={"rounds": args.rounds, "stop": stop},
        name="a1-practice",
        daemon=True,
    )
    driver.start()
    print(
        f"A1 practice run {args.run_id} at {session.layout.root}; "
        f"open http://{args.host}:{args.port}/a1/ in the kiosk browser",
        file=sys.stderr,
    )
    try:
        (serve or _serve_uvicorn)(create_a1_app(session.service), args.host, args.port, session)
    finally:
        stop.set()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
