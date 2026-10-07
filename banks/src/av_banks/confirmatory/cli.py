"""Command line of the confirmatory campaign: `python -m av_banks.confirmatory <command>`.

```
plan         --campaign-root R --campaign-id ID --generation-config G --freeze-manifest F
             --units U (--pilot PATH | --pilot-namespace NS)... [--repo DIR]
             [--freeze-repo-path REL] [--parallel-banks N] [--bank-version 1.0.0]
run          R --meanings M --prompts P --decoding-schema D --llm-url URL
             [--parallel-banks N] [--workers 3] [--only ID]... [--breaker 3]
             [--monitor-interval 60] [--continue-on-error] [--break-lock]
status       R [--json]
rebuild      R --bank-id ID --reason TEXT
verify-all   R [--jobs N] [--only ID]...
register     R
escalate     R --reference URL --date YYYY-MM-DD
publish      R --dest DIR
commit-check --repo DIR --path REL --campaign-root R --first-screening ISO [--ref HEAD]
rehearse     --out DIR [--campaign-id DEMO-...] [--unavailable ID]... [--parallel-banks N]
             [--jobs N] [--only ID]...
```

`plan` of a confirmatory campaign needs `--repo` (the checkout whose freeze tag is
checked). `commit-check --path` names `register.csv` or `register.json` of the campaign.

Exit codes: 0 done; 1 a check failed (verify, blocked register, commit check, crashed or
halted run); 2 refused or an error; 3 the register needs an advisor escalation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from av_generation.clock import SystemClock
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import file_sha256
from av_generation.rundir import RunLayout

from av_banks.proposer import SlotProposer

from .common import E_INPUT, CampaignError, CampaignLayout
from .plan import PlannedBank, create_plan
from .register import (
    PUBLIC_FILES,
    check_register_commit,
    compile_register,
    publish_register,
    record_escalation,
    verify_all,
)
from .rehearsal import DEFAULT_UNAVAILABLE, rehearse
from .runner import (
    DEFAULT_BREAKER,
    ProposerFactory,
    campaign_status,
    rebuild_bank,
    run_campaign,
)
from .seed_check import read_pilot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_banks.confirmatory", description="Confirmatory Study B banks (#28)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="check the G4 freeze, create the 72 banks and seeds")
    plan.add_argument("--campaign-root", required=True, type=Path)
    plan.add_argument("--campaign-id", required=True)
    plan.add_argument("--generation-config", required=True, type=Path)
    plan.add_argument("--freeze-manifest", required=True, type=Path)
    plan.add_argument("--units", required=True, type=Path)
    plan.add_argument("--pilot", action="append", default=[], type=Path)
    plan.add_argument("--pilot-namespace", action="append", default=[])
    plan.add_argument(
        "--repo", type=Path, default=None, help="git checkout to check the freeze tag in"
    )
    plan.add_argument(
        "--freeze-repo-path",
        default=None,
        help="the manifest's path at the tag (default generation/FREEZE-v<version>.json)",
    )
    plan.add_argument("--parallel-banks", type=int, default=1)
    plan.add_argument("--bank-version", default="1.0.0")

    run = sub.add_parser("run", help="build the pending banks in parallel")
    run.add_argument("root", type=Path)
    run.add_argument("--meanings", required=True, type=Path)
    run.add_argument("--prompts", required=True, type=Path)
    run.add_argument("--decoding-schema", required=True, type=Path)
    run.add_argument("--llm-url", required=True)
    run.add_argument("--llm-runtime", default="vllm")
    run.add_argument("--parallel-banks", type=int, default=1)
    run.add_argument("--workers", type=int, default=3)
    run.add_argument("--only", action="append", default=None)
    run.add_argument("--breaker", type=int, default=DEFAULT_BREAKER)
    run.add_argument("--monitor-interval", type=float, default=60.0)
    run.add_argument("--continue-on-error", action="store_true")
    run.add_argument("--break-lock", action="store_true")

    status = sub.add_parser("status", help="progress of a campaign (from any process)")
    status.add_argument("root", type=Path)
    status.add_argument("--json", action="store_true")

    rebuild = sub.add_parser("rebuild", help="rebuild a crashed bank under the next version")
    rebuild.add_argument("root", type=Path)
    rebuild.add_argument("--bank-id", required=True)
    rebuild.add_argument("--reason", required=True)

    verify = sub.add_parser("verify-all", help="banks verify on every finished bank")
    verify.add_argument("root", type=Path)
    verify.add_argument("--jobs", type=int, default=1)
    verify.add_argument("--only", action="append", default=None)

    register = sub.add_parser("register", help="compile the register, archive and G5B report")
    register.add_argument("root", type=Path)

    escalate = sub.add_parser("escalate", help="record the advisor escalation (link, date)")
    escalate.add_argument("root", type=Path)
    escalate.add_argument("--reference", required=True)
    escalate.add_argument("--date", required=True)

    publish = sub.add_parser("publish", help="copy register.csv and register.json to DEST")
    publish.add_argument("root", type=Path)
    publish.add_argument("--dest", required=True, type=Path)

    check = sub.add_parser("commit-check", help="check the timestamped register commit")
    check.add_argument("--repo", required=True, type=Path)
    check.add_argument("--path", required=True)
    check.add_argument("--campaign-root", required=True, type=Path)
    check.add_argument("--first-screening", required=True)
    check.add_argument("--ref", default="HEAD")

    demo = sub.add_parser("rehearse", help="the whole procedure on DEMO IDs (no model)")
    demo.add_argument("--out", required=True, type=Path)
    demo.add_argument("--campaign-id", default="DEMO-cbanks-01")
    demo.add_argument("--unavailable", action="append", default=None)
    demo.add_argument("--parallel-banks", type=int, default=4)
    demo.add_argument("--jobs", type=int, default=1)
    demo.add_argument("--only", action="append", default=None)
    return parser


def _print(data: Any) -> None:  # noqa: ANN401
    print(json.dumps(data, indent=2, sort_keys=True))


def _progress(_status: Any, line: str) -> None:  # noqa: ANN401
    print(line, file=sys.stderr, flush=True)


def real_proposer(args: argparse.Namespace, config: GenerationConfig) -> ProposerFactory:
    """The model proposer of `run` (#26's `banks build` proposer over the #16 client and
    the #17 prompt set; every call is logged in its bank run's `llm-requests.jsonl`)."""
    from av_banks.cli import make_proposer

    factory = make_proposer(args, config)

    def per_bank(layout: RunLayout, _bank: PlannedBank) -> SlotProposer:
        proposer: SlotProposer = factory(layout)
        return proposer

    return per_bank


def _plan(args: argparse.Namespace) -> int:
    pilot = read_pilot(args.pilot, namespaces=args.pilot_namespace)
    plan = create_plan(
        args.campaign_root,
        campaign_id=args.campaign_id,
        config=GenerationConfig.read(args.generation_config),
        freeze_manifest=args.freeze_manifest,
        units=args.units,
        pilot=pilot,
        clock=SystemClock(),
        bank_version=args.bank_version,
        repo=args.repo,
        freeze_repo_path=args.freeze_repo_path,
        parallel_banks=args.parallel_banks,
    )
    layout = CampaignLayout.at(args.campaign_root)
    _print(
        {
            "campaign_id": plan.campaign_id,
            "banks": len(plan.banks),
            "first": plan.banks[0].bank_id,
            "last": plan.banks[-1].bank_id,
            "generation_config_sha256": plan.generation_config_sha256,
            "freeze": {
                "tag": plan.freeze.tag,
                "tag_checked": plan.freeze.tag_checked,
                "guard_checked": plan.freeze.guard_checked,
                "manifest_sha256": plan.freeze.manifest_sha256,
            },
            "seed_keys": plan.seeds.keys,
            "distinct_seeds": plan.seeds.distinct_seeds,
            "pilot_namespaces": len(plan.pilot.namespaces),
            "sizing": None
            if plan.sizing is None
            else {
                "expected_hours": plan.sizing.expected_hours,
                "worst_hours": plan.sizing.worst_hours,
            },
            "plan_sha256": file_sha256(layout.plan),
        }
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    config = GenerationConfig.read(CampaignLayout.at(args.root).generation_config)
    result = run_campaign(
        args.root,
        proposer_factory=real_proposer(args, config),
        clock=SystemClock(),
        config=config,
        parallel_banks=args.parallel_banks,
        workers=args.workers,
        only=args.only,
        breaker_threshold=args.breaker,
        monitor_interval_s=args.monitor_interval,
        on_progress=_progress,
        stop_on_error=not args.continue_on_error,
        break_lock=args.break_lock,
        llm_runtime=args.llm_runtime,
    )
    _print(
        {
            "halted": result.halted,
            "stopped": result.stopped,
            "counts": dict(result.status.counts),
            "banks": [
                {
                    "bank_id": b.bank_id,
                    "outcome": b.outcome,
                    "bank_sha256": b.bank_sha256,
                    "error": b.error,
                }
                for b in result.banks
            ],
        }
    )
    crashed = any(b.outcome == "crashed" for b in result.banks)
    return 1 if result.halted or result.stopped or crashed else 0


def _status(args: argparse.Namespace) -> int:
    status = campaign_status(args.root)
    if args.json:
        _print(status.to_dict())
    else:
        print(status.line())
    return 0


def _rebuild(args: argparse.Namespace) -> int:
    bank = rebuild_bank(args.root, args.bank_id, reason=args.reason, clock=SystemClock())
    _print({"bank_id": bank.bank_id, "bank_version": bank.bank_version, "run_id": bank.run_id})
    return 0


def _verify(args: argparse.Namespace) -> int:
    result = verify_all(args.root, jobs=args.jobs, only=args.only)
    print(CampaignLayout.at(args.root).verification_log.read_text(encoding="utf-8"), end="")
    return 0 if not result.failed else 1


def _register(args: argparse.Namespace) -> int:
    result = compile_register(args.root, clock=SystemClock())
    print(CampaignLayout.at(args.root).g5b_report.read_text(encoding="utf-8"), end="")
    return {"ready": 0, "escalated": 0, "escalation_required": 3}.get(result.decision, 1)


def _escalate(args: argparse.Namespace) -> int:
    path = record_escalation(
        args.root, reference=args.reference, date=args.date, clock=SystemClock()
    )
    print(f"recorded {path.name}; compile the register again")
    return 0


def _publish(args: argparse.Namespace) -> int:
    paths = publish_register(args.root, args.dest)
    for path in paths:
        print(path.as_posix())
    print("next: git add, commit and push these files; then run commit-check")
    return 0


def _commit_check(args: argparse.Namespace) -> int:
    layout = CampaignLayout.at(args.campaign_root)
    name = Path(args.path.replace("\\", "/")).name
    if name not in PUBLIC_FILES:
        raise CampaignError(E_INPUT, f"--path names {name!r}, not one of {list(PUBLIC_FILES)}")
    local = layout.root / name
    if not local.is_file():
        raise CampaignError(E_INPUT, f"the campaign has no {name} (compile the register)")
    expected = file_sha256(local)
    result = check_register_commit(
        args.repo,
        args.path,
        expected_sha256=expected,
        first_screening_utc=args.first_screening,
        ref=args.ref,
    )
    _print(result.to_dict())
    return 0 if result.ok else 1


def _rehearse(args: argparse.Namespace) -> int:
    result = rehearse(
        args.out,
        campaign_id=args.campaign_id,
        unavailable=tuple(args.unavailable)
        if args.unavailable is not None
        else DEFAULT_UNAVAILABLE,
        parallel_banks=args.parallel_banks,
        jobs=args.jobs,
        only=args.only,
        on_progress=_progress,
    )
    register = result.register
    _print(
        {
            "campaign_root": result.root.as_posix(),
            "counts": dict(result.run.status.counts),
            "decision": None if register is None else register.decision,
            "register_csv_sha256": None if register is None else register.register_csv_sha256,
            "archive_sha256": None if register is None else register.archive.sha256,
        }
    )
    return 0 if register is not None and register.decision != "blocked" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands = {
        "plan": _plan,
        "run": _run,
        "status": _status,
        "rebuild": _rebuild,
        "verify-all": _verify,
        "register": _register,
        "escalate": _escalate,
        "publish": _publish,
        "commit-check": _commit_check,
        "rehearse": _rehearse,
    }
    try:
        return commands[args.command](args)
    except (CampaignError, OSError, ValueError, RuntimeError) as err:
        print(f"confirmatory {args.command}: {err}", file=sys.stderr)
        return 2
