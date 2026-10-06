"""Command line: `banks build`, `banks verify`, `banks amend`, `banks hash`.

```
banks build --bank-id bank-P001 --permutation <unit>/permutation.json \\
    [--bank-id bank-P002 --permutation ...] --runs-root <restricted dir> --run-id P-banks-01 \\
    --generation-config generation-config.json --meanings <dir> --prompts <dir> \\
    --decoding-schema decoding-schema.json --llm-url http://<llm-host>:8000 \\
    [--bank-version 1.0.0] [--seed-namespace NS] [--workers 3] [--parallel-banks 1] \\
    [--freeze-manifest freeze-manifest.json] [--kind demo|synthetic]
banks verify <run>/banks/<bank_id> [--json]
banks amend <run>/banks/<bank_id> --profile P1 --atom K-a1 --rank 2 --reason TEXT \\
    --unheard-confirmed [--date YYYY-MM-DD]
banks hash <run>/banks/<bank_id>
```

`build` prints one JSON summary (bank hashes, statuses, attempts) and exits 0 when every
bank is complete, 3 when a bank is unavailable. `verify` exits 0 when the bank verifies,
1 otherwise. Errors exit 2. Nothing needs the network except the LLM host given by
`--llm-url` (the station network).
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
from av_generation.jsonio import file_sha256, read_json
from av_generation.llm import OpenAICompatibleClient
from av_generation.meanings import load_meanings
from av_generation.prompts import load_prompt_set
from av_generation.records import RecordWriter
from av_generation.rundir import RunLayout

from av_banks.amend import amend_bank
from av_banks.builder import bank_spec
from av_banks.manifest import read_manifest
from av_banks.permutation import load_permutation
from av_banks.proposer import LlmSlotProposer, SlotProposer, check_prompt_inputs
from av_banks.run import build_banks
from av_banks.verify import verify_bank


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="banks", description="Study B bank builder (#26)")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build one or more banks in a new run directory")
    build.add_argument("--bank-id", action="append", required=True, dest="bank_ids")
    build.add_argument("--permutation", action="append", required=True, dest="permutations")
    build.add_argument("--runs-root", required=True, type=Path)
    build.add_argument("--run-id", required=True)
    build.add_argument("--generation-config", required=True, type=Path)
    build.add_argument("--meanings", required=True, type=Path)
    build.add_argument("--prompts", required=True, type=Path)
    build.add_argument("--decoding-schema", required=True, type=Path)
    build.add_argument("--llm-url", required=True)
    build.add_argument("--llm-runtime", default="vllm")
    build.add_argument("--bank-version", default="1.0.0")
    build.add_argument("--seed-namespace", default=None)
    build.add_argument("--workers", type=int, default=3)
    build.add_argument("--parallel-banks", type=int, default=1)
    build.add_argument("--freeze-manifest", type=Path, default=None)
    build.add_argument("--kind", choices=("demo", "synthetic"), default=None)

    verify = sub.add_parser("verify", help="re-render a bank and recheck hashes and pairs")
    verify.add_argument("bank_dir", type=Path)
    verify.add_argument("--json", action="store_true")

    amend = sub.add_parser("amend", help="replace an unheard menu option with the reserve")
    amend.add_argument("bank_dir", type=Path)
    amend.add_argument("--profile", required=True, choices=("P1", "P2", "P3"))
    amend.add_argument("--atom", required=True)
    amend.add_argument("--rank", required=True, type=int, choices=(1, 2, 3))
    amend.add_argument("--reason", required=True)
    amend.add_argument("--date", default=None)
    amend.add_argument("--unheard-confirmed", action="store_true")

    digest = sub.add_parser("hash", help="print the bank hash of a manifest")
    digest.add_argument("bank_dir", type=Path)
    return parser


def make_proposer(args: argparse.Namespace, config: GenerationConfig) -> Any:  # noqa: ANN401
    """The model proposer for `build`: a factory over the new run's layout, so the #16
    client logs every call to the run's `logs/llm-requests.jsonl`."""
    meanings = load_meanings(args.meanings, expected_sha256=config.meanings_sha256)
    prompt_set = load_prompt_set(args.prompts, meanings=meanings)  # #17 checks its hash file
    schema = read_json(args.decoding_schema)
    if not isinstance(schema, dict):
        raise ValueError(f"{args.decoding_schema}: a JSON Schema is an object")
    check_prompt_inputs(prompt_set, schema, config)
    clock = SystemClock()

    def factory(layout: RunLayout) -> SlotProposer:
        client = OpenAICompatibleClient(
            args.llm_url,
            config.model.model_id,
            run_id=layout.run_id,
            clock=clock,
            request_log=RecordWriter(layout.log("llm_request")),
            runtime=args.llm_runtime,
            model_revision=config.model.revision,
        )
        return LlmSlotProposer(client, prompt_set, schema, threshold=config.separation_threshold)

    return factory


def _build(args: argparse.Namespace) -> int:
    if len(args.bank_ids) != len(args.permutations):
        raise ValueError("give one --permutation per --bank-id")
    if args.seed_namespace is not None and len(args.bank_ids) != 1:
        raise ValueError("--seed-namespace names one bank's namespace; build one bank")
    config = GenerationConfig.read(args.generation_config)
    specs = [
        bank_spec(
            bank_id,
            load_permutation(path),
            bank_version=args.bank_version,
            seed_namespace=args.seed_namespace,
        )
        for bank_id, path in zip(args.bank_ids, args.permutations, strict=True)
    ]
    freeze = None
    freeze_sha = None
    if args.freeze_manifest is not None:
        freeze = read_json(args.freeze_manifest)
        freeze_sha = file_sha256(args.freeze_manifest)
    result = build_banks(
        specs,
        runs_root=args.runs_root,
        run_id=args.run_id,
        config=config,
        proposer=make_proposer(args, config),
        clock=SystemClock(),
        kind=args.kind,
        freeze_manifest=freeze,
        freeze_manifest_sha256=freeze_sha,
        llm_runtime=args.llm_runtime,
        workers=args.workers,
        parallel_banks=args.parallel_banks,
    )
    summary = {
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "run_manifest_sha256": result.run_manifest_sha256,
        "banks": [
            {
                "bank_id": bank.bank_id,
                "bank_dir": str(bank.bank_dir),
                "status": bank.status,
                "attempt_used": bank.attempt_used,
                "attempts": len(bank.attempts),
                "slots_used": bank.slots_used,
                "bank_sha256": bank.bank_sha256,
            }
            for bank in result.banks
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(bank.status == "complete" for bank in result.banks) else 3


def _verify(args: argparse.Namespace) -> int:
    report = verify_bank(args.bank_dir)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"bank {report.bank_id}: {report.status}; bank hash {report.bank_sha256}")
        print(
            f"attempts {report.attempts_checked}, slots {report.slots_checked}, options "
            f"{report.options_checked}, pairs {dict(report.pairs_checked)}, failed pairs "
            f"{dict(report.pairs_failed)}, amendments {report.amendments_checked}"
        )
        for problem in report.problems:
            print(f"PROBLEM: {problem}")
        print("OK" if report.ok else f"FAILED ({len(report.problems)} problems)")
    return 0 if report.ok else 1


def _amend(args: argparse.Namespace) -> int:
    result = amend_bank(
        args.bank_dir,
        profile=args.profile,
        atom_id=args.atom,
        rank=args.rank,
        reason=args.reason,
        unheard_confirmed=args.unheard_confirmed,
        date=args.date,
    )
    print(json.dumps({"amendment": result.entry, "menu": list(result.menu)}, indent=2))
    return 0


def _hash(args: argparse.Namespace) -> int:
    print(read_manifest(args.bank_dir).bank_sha256())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands = {"build": _build, "verify": _verify, "amend": _amend, "hash": _hash}
    try:
        return commands[args.command](args)
    except (OSError, ValueError, RuntimeError) as err:
        print(f"banks {args.command}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
