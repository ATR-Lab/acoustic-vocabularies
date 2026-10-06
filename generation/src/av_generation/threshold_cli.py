"""Command line of the separation-threshold listening tool (#23).

`uv run --project generation python -m av_generation.threshold_cli <command> ...`

| Command | Does |
| --- | --- |
| `stimuli` | build a stimulus set (`--set-id`, optional `--config`) and write it |
| `check` | check a stimulus set (every pair valid, in its bin, hashes, coverage) |
| `session` | open the run, plan and write a session, then serve the listener page |
| `export` | trials CSV, summary CSV, fits CSV, summary JSON and plot of a run |
| `summary` | the same summary from an exported trials CSV (the O6.2.2 script) |
| `demo` | a DEMO set, bot-listener sessions through the real server, export |

Operator guide: `generation/docs/threshold-tool.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
import uvicorn

from av_generation import threshold as th
from av_generation import threshold_runner as tr
from av_generation.clock import SystemClock, utc_text
from av_generation.ids import RunKind
from av_generation.jsonio import file_sha256
from av_generation.records import ThresholdConfig, ThresholdSession, ThresholdStimulusSet
from av_generation.rundir import run_layout
from av_generation.webserve import serve_in_thread

DEMO_SMALL_CONFIG = ThresholdConfig(
    profiles=("P1", "P2", "P3"),
    bin_centers=th.DEFAULT_CONFIG.bin_centers,
    bin_halfwidth=th.DEFAULT_CONFIG.bin_halfwidth,
    pairs_per_bin=2,
    same_pairs=12,
    gap_ms=th.DEFAULT_CONFIG.gap_ms,
    threshold_default=th.DEFAULT_CONFIG.threshold_default,
)
"""`demo --small`: 3 profiles x 7 bins x 2 pairs + 12 catch pairs = 54 trials."""


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cmd_stimuli(args: argparse.Namespace) -> int:
    config = th.load_config(args.config) if args.config else th.DEFAULT_CONFIG
    stimuli = th.generate_stimuli(args.set_id, config)
    file_sha = th.write_stimuli(stimuli, args.out)
    _print(
        {
            "set_id": stimuli.set_id,
            "set_sha256": stimuli.sha256(),
            "file_sha256": file_sha,
            "n_pairs": len(stimuli.pairs),
            "coverage": {f"{p}|{b}": n for (p, b), n in th.coverage(stimuli).items()},
        }
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    stimuli = ThresholdStimulusSet.read(args.stimuli)
    problems = th.check_stimuli(stimuli)
    _print(
        {
            "set_id": stimuli.set_id,
            "set_sha256": stimuli.sha256(),
            "n_pairs": len(stimuli.pairs),
            "problems": list(problems),
        }
    )
    return 1 if problems else 0


def _cmd_session(args: argparse.Namespace) -> int:
    clock = SystemClock()
    stimuli = ThresholdStimulusSet.read(args.stimuli)
    layout = tr.open_threshold_run(args.runs_root, args.run_id, args.kind, stimuli, clock=clock)
    path = layout.threshold_session(args.session_id)
    if path.exists():
        session = ThresholdSession.read(path)
        if (session.listener_id, session.station, session.tryout) != (
            args.listener,
            args.station,
            args.tryout,
        ):
            print(f"{path} exists for another listener/station/tryout flag", file=sys.stderr)
            return 2
    else:
        session = th.plan_session(
            stimuli,
            args.session_id,
            listener_id=args.listener,
            station=args.station,
            gain_db=args.gain_db,
            tryout=args.tryout,
            created_utc=utc_text(clock.utc_now()),
        )
    runner = tr.ThresholdRunner(layout, stimuli, session, clock=clock)
    _print(
        {
            "run": str(layout.root),
            "session": str(path),
            "session_sha256": session.sha256(),
            "n_trials": len(session.plan),
            "url": f"http://{args.host}:{args.port}/threshold/",
        }
    )
    if args.plan_only:
        return 0
    uvicorn.run(
        tr.create_threshold_app(runner), host=args.host, port=args.port, log_level="warning"
    )
    return 0


def _export_report(result: tr.ExportResult) -> dict[str, object]:
    return {
        "out_dir": str(result.out_dir),
        "files": result.files,
        "play_checks": [
            {
                "session_id": c.session_id,
                "ok": c.ok,
                "n_planned": c.n_planned,
                "n_played": c.n_played,
                "n_answered": c.n_answered,
                "n_refused": c.n_refused,
                "problems": list(c.problems),
            }
            for c in result.play_checks
        ],
    }


def _cmd_export(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    layout = run_layout(run_dir.parent, run_dir.name)
    result = tr.export_run(
        layout, args.out_dir, tryout=args.tryout, label=args.label, plot=not args.no_plot
    )
    _print(_export_report(result))
    return 0 if all(c.ok for c in result.play_checks) else 1


def _cmd_summary(args: argparse.Namespace) -> int:
    trials = th.read_trials_csv(args.trials)
    stimuli = ThresholdStimulusSet.read(args.stimuli) if args.stimuli else None
    default = (stimuli.config if stimuli else th.DEFAULT_CONFIG).threshold_default
    out = Path(args.out_dir)
    rows = th.summarize(trials)
    fits = th.fit_summary(trials, threshold_default=default)
    files = {
        "summary.csv": th.write_summary_csv(rows, out / "summary.csv"),
        "fits.csv": th.write_fits_csv(fits, out / "fits.csv"),
    }
    summary = th.build_summary(
        trials,
        label=args.label,
        stimuli=stimuli,
        trials_csv_sha256=file_sha256(args.trials),
    )
    files["summary.json"] = th.write_summary(summary, out / "summary.json")
    if not args.no_plot and trials:
        th.plot_summary(
            rows, fits, out / "summary.png", title=args.label, threshold_default=default
        )
    _print({"out_dir": str(out), "files": files})
    return 0


def run_demo(
    out_dir: str | Path, *, sessions: int = 2, small: bool = False, set_id: str = "DEMO-T1"
) -> tr.ExportResult:
    """Build a DEMO set, run `sessions` bot-listener sessions through the real server
    (uvicorn on 127.0.0.1) and export them to `<out_dir>/export`."""
    clock = SystemClock()
    out = Path(out_dir)
    stimuli = th.generate_stimuli(set_id, DEMO_SMALL_CONFIG if small else th.DEFAULT_CONFIG)
    run_id = f"{set_id}-run" if set_id.startswith("DEMO-") else "DEMO-threshold-run"
    layout = tr.open_threshold_run(out / "runs", run_id, RunKind.DEMO, stimuli, clock=clock)
    for i, session_id in enumerate(tr.bot_session_ids(sessions), start=1):
        if layout.threshold_session(session_id).exists():
            continue
        session = th.plan_session(
            stimuli,
            session_id,
            listener_id=f"BOT{i:02d}",
            station=f"S{i}",
            gain_db=-12.0,
            tryout=False,
            created_utc=utc_text(clock.utc_now()),
        )
        runner = tr.ThresholdRunner(layout, stimuli, session, clock=clock, fsync=False)
        with (
            serve_in_thread(tr.create_threshold_app(runner)) as base,
            httpx.Client(base_url=base, timeout=30) as client,
        ):
            tr.run_bot_session(client, session, stimuli)
    return tr.export_run(layout, out / "export", tryout=False)


def _cmd_demo(args: argparse.Namespace) -> int:
    result = run_demo(args.out_dir, sessions=args.sessions, small=args.small)
    _print(_export_report(result))
    return 0 if all(c.ok for c in result.play_checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.threshold_cli",
        description="Separation-threshold listening tool (#23); see threshold-tool.md",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stimuli", help="build and write a stimulus set")
    p.add_argument("--set-id", required=True)
    p.add_argument("--config", help="ThresholdConfig JSON (default: DEFAULT_CONFIG)")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_stimuli)

    p = sub.add_parser("check", help="check a stimulus set")
    p.add_argument("--stimuli", required=True)
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("session", help="plan a listener session and serve the page")
    p.add_argument("--runs-root", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--kind", required=True, choices=[k.value for k in RunKind])
    p.add_argument("--stimuli", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--listener", required=True, help="coded listener ID (never a name)")
    p.add_argument("--station", required=True, help="rater station, e.g. S1")
    p.add_argument("--gain-db", required=True, type=float, help="the station's fixed gain")
    p.add_argument("--tryout", action="store_true", help="internal tryout (team members)")
    p.add_argument("--plan-only", action="store_true", help="write the session, do not serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8023)
    p.set_defaults(func=_cmd_session)

    p = sub.add_parser("export", help="export a run (CSV, summary, plot)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--tryout", action="store_true", help="export the tryout sessions")
    p.add_argument("--label", help="plot and summary label")
    p.add_argument("--no-plot", action="store_true")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("summary", help="summary of an exported trials CSV")
    p.add_argument("--trials", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--stimuli", help="the stimulus set (adds its hash and config)")
    p.add_argument("--no-plot", action="store_true")
    p.set_defaults(func=_cmd_summary)

    p = sub.add_parser("demo", help="DEMO set + bot sessions + export (synthetic)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--sessions", type=int, default=2)
    p.add_argument("--small", action="store_true", help="54-trial config instead of 224")
    p.set_defaults(func=_cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; returns the exit status (1 when a check found problems)."""
    args = build_parser().parse_args(argv)
    status: int = args.func(args)
    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
