"""A bank run: one run directory holding one or more banks built in parallel.

```
<runs_root>/<run_id>/
  run-manifest.json        RunManifest (purpose "bank", study B, bank IDs, config hash)
  generation-config.json   the GenerationConfig of the run
  logs/timing.jsonl        run_start / run_end
  logs/llm-requests.jsonl  the model calls (written by the #16 client the caller made)
  banks/<bank_id>/         one bank directory per bank (av_banks.layout)
```

`build_banks` checks the run before any slot (`genconfig.check_run_config`: code pins,
demo/real config, and the frozen G4 config for confirmatory banks; the proposer's prompt
set, meanings and decoding schema), creates the run directory under the public/restricted
policy (`rundir.create_run_dir`: pilot and confirmatory runs are refused inside a git
work tree), builds the banks (`parallel_banks` at a time, each with `workers` profile
streams) and closes the run manifest with the SHA-256 of every file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal

import av_generation
from av_generation.clock import Clock, ScaledClock, SystemClock, utc_text
from av_generation.genconfig import GenerationConfig, check_run_config
from av_generation.ids import PUBLIC_RUN_KINDS, RunKind, Study
from av_generation.jsonio import file_sha256
from av_generation.records import RecordWriter, RunCode, RunManifest, TimingEvent
from av_generation.rundir import RunLayout, create_run_dir, relative_files

from av_banks.builder import (
    RUN_KIND_OF_SET,
    BankBuilder,
    BankBuildError,
    BankSpec,
    BuildResult,
    LedgerFactory,
    slot_ledger,
)
from av_banks.proposer import SlotProposer

E_RUN: Final = "E_RUN"


@dataclass(frozen=True, slots=True)
class RunResult:
    """The banks of one run and its run manifest."""

    run_id: str
    run_dir: Path
    banks: tuple[BuildResult, ...]
    run_manifest_sha256: str


def clock_kind(clock: Clock) -> Literal["real", "scaled", "manual"]:
    """The run manifest's `clock` value for a clock (test clocks count as manual)."""
    if isinstance(clock, SystemClock):
        return "real"
    if isinstance(clock, ScaledClock):
        return "scaled"
    return "manual"


def run_kind(specs: Sequence[BankSpec], kind: RunKind | str | None = None) -> RunKind:
    """The run kind of a set of banks: all banks of a run belong to one set."""
    sets = {spec.set for spec in specs}
    if len(sets) != 1:
        raise BankBuildError(E_RUN, f"one run builds banks of one set, got {sorted(sets)}")
    set_name = sets.pop()
    result = RunKind(kind) if kind is not None else RUN_KIND_OF_SET[set_name]
    if (result in PUBLIC_RUN_KINDS) != (set_name == "demo"):
        raise BankBuildError(E_RUN, f"{set_name} banks cannot be built in a {result} run")
    return result


def build_banks(
    specs: Sequence[BankSpec],
    *,
    runs_root: str | os.PathLike[str],
    run_id: str,
    config: GenerationConfig,
    proposer: SlotProposer | Callable[[RunLayout], SlotProposer],
    clock: Clock,
    kind: RunKind | str | None = None,
    freeze_manifest: Mapping[str, Any] | None = None,
    freeze_manifest_sha256: str | None = None,
    llm_runtime: str | None = None,
    workers: int = 3,
    parallel_banks: int = 1,
    ledger_factory: LedgerFactory = slot_ledger,
    fsync: bool = True,
) -> RunResult:
    """Build `specs` in a new run directory `<runs_root>/<run_id>` (module docstring).

    `proposer` is the `SlotProposer` all banks share, or a factory called with the new
    run's layout (so the model client can log to `logs/llm-requests.jsonl`). Bank IDs
    must be unique. The run ends with the run manifest's `files`.
    """
    if not specs:
        raise BankBuildError(E_RUN, "nothing to build")
    ids = [spec.bank_id for spec in specs]
    if len(set(ids)) != len(ids):
        raise BankBuildError(E_RUN, f"bank IDs repeat: {ids}")
    if parallel_banks < 1:
        raise BankBuildError(E_RUN, "parallel_banks must be at least 1")
    run_kind_ = run_kind(specs, kind)
    freeze = dict(freeze_manifest) if freeze_manifest is not None else None
    check_run_config(config, kind=run_kind_, freeze_manifest=freeze)
    if isinstance(proposer, SlotProposer):
        proposer.check_config(config)
    layout = create_run_dir(runs_root, run_id, run_kind_)
    shared = proposer if isinstance(proposer, SlotProposer) else proposer(layout)
    builders = []
    for spec in specs:
        builder = BankBuilder(
            spec,
            layout.bank_dir(spec.bank_id),
            config=config,
            proposer=shared,
            clock=clock,
            run_id=run_id,
            kind=run_kind_,
            freeze_manifest=freeze,
            ledger_factory=ledger_factory,
            workers=workers,
            fsync=fsync,
        )
        builder.check()
        builders.append(builder)
    config.write(layout.generation_config)
    code = config.code
    manifest = RunManifest(
        run_id=run_id,
        kind=run_kind_,
        study=Study.B,
        purpose="bank",
        clock=clock_kind(clock),
        created_utc=utc_text(clock.utc_now()),
        code=RunCode(
            av_generation=av_generation.__version__,
            renderer_version=code.renderer_version,
            renderer_hash=code.renderer_hash,
            validator_version=code.validator_version,
            validator_hash=code.validator_hash,
        ),
        threshold=config.separation_threshold,
        seed_namespace=specs[0].seed_namespace if len(specs) == 1 else None,
        bank_ids=tuple(ids),
        llm_manifest_sha256=config.llm_manifest_sha256,
        llm_runtime=llm_runtime,
        generation_config_sha256=config.frozen_sha256(),
        meanings_sha256=config.meanings_sha256,
        freeze_manifest_sha256=freeze_manifest_sha256,
    )
    manifest.write(layout.manifest)
    timing = RecordWriter(layout.log("timing"), types=(TimingEvent,), fsync=fsync)
    timing.append(
        TimingEvent(
            run_id=run_id,
            event="run_start",
            t_ms=clock.now_ms(),
            wall_utc=utc_text(clock.utc_now()),
            component="bank",
            detail=f"{len(specs)} banks; {parallel_banks} in parallel; {workers} streams each",
        )
    )
    t0 = clock.now_ms()
    if parallel_banks == 1 or len(builders) == 1:
        results = [b.build() for b in builders]
    else:
        with ThreadPoolExecutor(max_workers=parallel_banks, thread_name_prefix="banks") as pool:
            results = list(pool.map(lambda b: b.build(), builders))
    timing.append(
        TimingEvent(
            run_id=run_id,
            event="run_end",
            t_ms=clock.now_ms(),
            wall_utc=utc_text(clock.utc_now()),
            component="bank",
            duration_ms=clock.now_ms() - t0,
            detail="; ".join(f"{r.bank_id} {r.status}" for r in results)[:400],
        )
    )
    files = {
        name: file_sha256(layout.root / name)
        for name in relative_files(layout.root)
        if name != layout.manifest.name
    }
    closed = replace(manifest, closed_utc=utc_text(clock.utc_now()), files=files)
    digest = closed.write(layout.manifest, exclusive=False)
    return RunResult(run_id, layout.root, tuple(results), digest)
