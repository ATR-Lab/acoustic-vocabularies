"""Command line: `python -m av_banks.unattended run|retry|status|check|demo-job|rehearsal-job`.

```
run            JOB.json [JOB.json ...] [--retry] [--allow-job-change] [--lock-wait S]
               [--quiet]
retry          JOB.json [--allow-job-change]
status         JOB.json|JOB_ROOT [--json]
check          JOB.json [--no-probe]
demo-job       OUT --llm-url URL [--job-id DEMO-...] [--dyads 2] [--spares 1] [--banks 3]
rehearsal-job  OUT --llm-url URL [--job-id DEMO-rehearsal-01] [--dyads 64] [--spares 8]
               [--parallel-banks 6] [--workers 3]
```

`run` exits 0 when the job is done, 2 when refused, 3 when it stopped for a person
(`status` shows why and what to do), 4 when a signal interrupted it (run it again) and 1
on an unexpected error. With several job files it runs them in order, each to its end,
and starts the next only when one is done (exit 0); otherwise it exits with that job's
code (the December pilot job, then the rehearsal job: banks/docs/unattended-run.md).
`retry` accepts the retry of a stopped job without running it (for the service: `retry`,
then `systemctl restart`). `check` validates the job file, the data policy and the
inputs without writing anything (exit 0 or 2). `demo-job` writes DEMO inputs and a DEMO
job file for a rehearsal against a model server (banks/docs/unattended-run.md, section
2). `rehearsal-job` writes the inputs and the job file of a full-size rehearsal of the
confirmatory campaign (`rehearsal` module): its purpose is in the job file, so `run`,
`retry`, `status`, `check` and the service need no flag, and a restart after a reboot
cannot drop it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from av_generation.jsonio import CodecError

from .demo import write_demo_job
from .job import RehearsalSize, SupervisorError
from .rehearsal import DEFAULT_JOB_ID, FULL_SIZE, write_rehearsal_job
from .state import STATE_NAME, read_state
from .supervisor import (
    EXIT_DONE,
    EXIT_ERROR,
    EXIT_REFUSED,
    _status_root,
    main_check,
    retry_job,
    run_job,
    status_text,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_banks.unattended",
        description="Unattended pilot and confirmatory bank runs with checkpoint and resume",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run or resume the job (several: in order)")
    run.add_argument("job", type=Path, nargs="+")
    run.add_argument("--retry", action="store_true", help="re-evaluate the step that stopped")
    run.add_argument("--allow-job-change", action="store_true")
    run.add_argument("--lock-wait", type=float, default=None, help="seconds (job default)")
    run.add_argument("--quiet", action="store_true", help="no event lines on stderr")
    retry = sub.add_parser("retry", help="accept the retry of a stopped job (no run)")
    retry.add_argument("job", type=Path)
    retry.add_argument("--allow-job-change", action="store_true")
    status = sub.add_parser("status", help="state, progress and summary of a job")
    status.add_argument("job", type=Path, help="the job file or the job root")
    status.add_argument("--json", action="store_true")
    check = sub.add_parser("check", help="validate the job file, data policy and inputs")
    check.add_argument("job", type=Path)
    check.add_argument("--no-probe", action="store_true", help="do not ask the model server")
    demo = sub.add_parser("demo-job", help="write DEMO inputs and a DEMO job file")
    demo.add_argument("out", type=Path)
    demo.add_argument("--llm-url", required=True)
    demo.add_argument("--job-id", default="DEMO-unattended-01")
    demo.add_argument("--dyads", type=int, default=2)
    demo.add_argument("--spares", type=int, default=1)
    demo.add_argument("--banks", type=int, default=3, help="DEMO confirmatory banks to build")
    rehearsal = sub.add_parser(
        "rehearsal-job", help="write the inputs and job file of a full-size DEMO rehearsal"
    )
    rehearsal.add_argument("out", type=Path)
    rehearsal.add_argument("--llm-url", required=True)
    rehearsal.add_argument("--job-id", default=DEFAULT_JOB_ID)
    rehearsal.add_argument("--dyads", type=int, default=FULL_SIZE.dyads, help="main banks")
    rehearsal.add_argument("--spares", type=int, default=FULL_SIZE.spares, help="spare banks")
    rehearsal.add_argument("--parallel-banks", type=int, default=6)
    rehearsal.add_argument("--workers", type=int, default=3)
    return parser


def _run_jobs(args: argparse.Namespace) -> int:
    for job in args.job:
        code = run_job(
            job,
            retry=args.retry,
            allow_job_change=args.allow_job_change,
            echo=not args.quiet,
            lock_wait_s=args.lock_wait,
        )
        if code != EXIT_DONE:
            return code
    return EXIT_DONE


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run_jobs(args)
        if args.command == "retry":
            return retry_job(args.job, allow_job_change=args.allow_job_change)
        if args.command == "status":
            if args.json:
                root = _status_root(args.job)[0]
                state = read_state(root / STATE_NAME)
                print(json.dumps(None if state is None else state.to_dict(), indent=2))
            else:
                print(status_text(args.job), end="")
            return 0
        if args.command == "check":
            return main_check(str(args.job), probe=not args.no_probe, out=print)
        if args.command == "rehearsal-job":
            print(
                write_rehearsal_job(
                    args.out,
                    llm_url=args.llm_url,
                    job_id=args.job_id,
                    size=RehearsalSize(args.dyads, args.spares),
                    parallel_banks=args.parallel_banks,
                    workers=args.workers,
                )
            )
            return 0
        path = write_demo_job(
            args.out,
            llm_url=args.llm_url,
            job_id=args.job_id,
            dyads=args.dyads,
            spares=args.spares,
            banks=args.banks,
        )
        print(path)
        return 0
    except (SupervisorError, CodecError, OSError, ValueError) as err:
        print(f"unattended {args.command}: {err}", file=sys.stderr)
        return EXIT_REFUSED if isinstance(err, SupervisorError) else EXIT_ERROR
