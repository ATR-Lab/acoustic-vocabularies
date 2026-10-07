"""Throughput and failure metrics of a set of built banks (#27; sizes the #28 run).

Everything is read back from the stored files of each bank directory (`manifest.json`,
`attempts/<n>/attempt.json`, `attempts/<n>/slots.jsonl`) and, for the run rate, from the
`run_end` event of each run's `logs/timing.jsonl`. Definitions:

- **attempts per bank**: attempts on disk per bank (1-4); a complete bank used its last;
- **slots per hour** (`slots_per_hour`): all slots / the summed wall time of all attempts,
  i.e. the rate of one bank builder with its profile streams (`workers`);
  `slots_per_hour_run`: all slots / the summed wall time of the runs, which includes the
  banks built in parallel (`parallel_banks`) and the time between attempts;
- **cell failure rate**: failed cells / concluded cells. A cell is complete when it
  retained 4 options and failed when it used its 12 slots with fewer; cells a parallel
  stream left unfinished when another profile failed (`stopped`) are not concluded;
- **attempt failure rate**: failed attempts / attempts;
- model latency and open-to-close slot time: nearest-rank p50, p95 and max over every
  slot; `slots_over_cap` counts slots longer than the 40-s slot cap;
- **projection** for `project_banks` banks (default 72, the #28 run: 64 dyad slots and 8
  spares): mean attempts and slots per bank times the bank count, the hours at both
  rates and the expected number of unavailable banks. With 8-10 pilot banks these are
  rough planning figures, not estimates with known precision.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_generation.constants import (
    B_MAX_ATTEMPTS,
    B_OPTIONS_PER_CELL,
    B_SLOTS_PER_CELL,
    PROFILES,
    SLOT_CAP_MS,
)
from av_generation.jsonio import to_json_value
from av_generation.outcomes import OUTCOME_CODES, LlmStatus
from av_generation.records import SlotRecord, TimingEvent, read_records

from av_banks.layout import BankLayout
from av_banks.manifest import AttemptSummary, Stats, read_manifest
from av_banks.throughput import stats

CONFIRMATORY_BANKS: Final = 72
"""Banks of the confirmatory run (#28): 64 main dyad slots and 8 spares."""
MS_PER_HOUR: Final = 3_600_000
RUN_TIMING: Final = "logs/timing.jsonl"


@dataclass(frozen=True, slots=True)
class Spread:
    """Count, minimum, mean and maximum of a list of numbers (`None` when empty)."""

    n: int
    min: float | None
    mean: float | None
    max: float | None


def spread(values: Iterable[float]) -> Spread:
    data = list(values)
    if not data:
        return Spread(0, None, None, None)
    return Spread(len(data), min(data), round(sum(data) / len(data), 3), max(data))


def ratio(numerator: float, denominator: float, digits: int = 3) -> float | None:
    """`numerator / denominator` rounded, or `None` when the denominator is 0."""
    return round(numerator / denominator, digits) if denominator else None


@dataclass(frozen=True, slots=True)
class Projection:
    """Expected effort of building `banks` banks at the measured rates."""

    banks: int
    attempts: float | None
    slots: float | None
    hours_one_bank_at_a_time: float | None
    """`slots / slots_per_hour`: banks built one after another."""
    hours_at_run_rate: float | None
    """`slots / slots_per_hour_run`: at the measured parallelism."""
    unavailable: float | None


@dataclass(frozen=True, slots=True)
class ThroughputSummary:
    """Throughput and failure metrics of a set of banks (module docstring)."""

    banks: int
    banks_complete: int
    banks_unavailable: int
    attempts: int
    attempts_failed: int
    attempts_per_bank: Spread
    attempts_per_complete_bank: Spread
    attempts_histogram: Mapping[str, int]
    """Number of banks by attempts on disk (`"1"`..`"4"`)."""
    attempt_failure_rate: float | None
    slots: int
    slots_per_bank: Spread
    slots_per_complete_attempt: Spread
    slots_per_failed_attempt: Spread
    attempt_hours: float
    slots_per_hour: float | None
    run_hours: float | None
    slots_per_hour_run: float | None
    latency_ms: Stats
    slot_ms: Stats
    slots_over_cap: int
    cells_complete: int
    cells_failed: int
    cell_failure_rate: float | None
    cell_failure_rate_by_profile: Mapping[str, float | None]
    slots_per_complete_cell: Spread
    outcomes: Mapping[str, int]
    """Slots by outcome code (every code, in #17's order)."""
    llm_status: Mapping[str, int]
    """Model calls by status (slots without a call are not counted)."""
    projection: Projection

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


def run_wall_ms(run_dir: str | os.PathLike[str]) -> int | None:
    """`duration_ms` of the `run_end` event of a bank run, or `None` if it has none."""
    path = Path(run_dir) / RUN_TIMING
    if not path.is_file():
        return None
    ends = [e for e in read_records(path, TimingEvent) if e.event == "run_end"]
    if not ends or ends[-1].duration_ms is None:
        return None
    return ends[-1].duration_ms


def summarize_banks(
    bank_dirs: Sequence[str | os.PathLike[str]],
    *,
    run_dirs: Sequence[str | os.PathLike[str]] = (),
    project_banks: int = CONFIRMATORY_BANKS,
) -> ThroughputSummary:
    """Read the banks' files and compute the summary (module docstring)."""
    statuses: list[str] = []
    attempts_of: list[int] = []
    slots_of: list[int] = []
    complete_attempt_slots: list[int] = []
    failed_attempt_slots: list[int] = []
    attempt_ms = 0
    latency: list[int] = []
    slot_ms: list[int] = []
    outcomes: Counter[str] = Counter()
    llm: Counter[str] = Counter()
    cells_complete = 0
    cells_failed = 0
    by_profile: dict[str, list[int]] = {p: [0, 0] for p in PROFILES}  # complete, failed
    complete_cell_slots: list[int] = []
    for bank_dir in bank_dirs:
        layout = BankLayout(Path(bank_dir))
        manifest = read_manifest(layout.manifest)
        statuses.append(manifest.status)
        attempts_of.append(len(manifest.attempts))
        bank_slots = 0
        for entry in manifest.attempts:
            summary = AttemptSummary.read(layout.attempt_summary(entry.attempt))
            records = read_records(layout.slots(entry.attempt), SlotRecord)
            bank_slots += len(records)
            attempt_ms += summary.wall_ms
            target = (
                complete_attempt_slots if summary.status == "complete" else failed_attempt_slots
            )
            target.append(len(records))
            for record in records:
                outcomes[record.outcome.value] += 1
                if record.llm_status is not None:
                    llm[record.llm_status.value] += 1
                if record.latency_ms is not None:
                    latency.append(record.latency_ms)
                slot_ms.append(record.t_ms - record.t_open_ms)
            for cell in summary.cells:
                if cell.retained >= B_OPTIONS_PER_CELL:
                    cells_complete += 1
                    by_profile[cell.profile][0] += 1
                    complete_cell_slots.append(cell.slots_used)
                elif cell.slots_used >= B_SLOTS_PER_CELL:
                    cells_failed += 1
                    by_profile[cell.profile][1] += 1
        slots_of.append(bank_slots)
    run_ms = [run_wall_ms(r) for r in run_dirs]
    known_run_ms = [ms for ms in run_ms if ms is not None]
    run_total = sum(known_run_ms) if known_run_ms and len(known_run_ms) == len(run_ms) else None
    total_slots = sum(slots_of)
    n_attempts = sum(attempts_of)
    n_failed = len(failed_attempt_slots)
    per_hour = ratio(total_slots * MS_PER_HOUR, attempt_ms, 1)
    per_hour_run = ratio(total_slots * MS_PER_HOUR, run_total, 1) if run_total else None
    n_banks = len(statuses)
    mean_attempts = ratio(n_attempts, n_banks)
    mean_slots = ratio(total_slots, n_banks)
    projected_slots = round(mean_slots * project_banks, 1) if mean_slots is not None else None
    return ThroughputSummary(
        banks=n_banks,
        banks_complete=statuses.count("complete"),
        banks_unavailable=statuses.count("unavailable"),
        attempts=n_attempts,
        attempts_failed=n_failed,
        attempts_per_bank=spread(attempts_of),
        attempts_per_complete_bank=spread(
            a for a, s in zip(attempts_of, statuses, strict=True) if s == "complete"
        ),
        attempts_histogram={str(n): attempts_of.count(n) for n in range(1, B_MAX_ATTEMPTS + 1)},
        attempt_failure_rate=ratio(n_failed, n_attempts),
        slots=total_slots,
        slots_per_bank=spread(slots_of),
        slots_per_complete_attempt=spread(complete_attempt_slots),
        slots_per_failed_attempt=spread(failed_attempt_slots),
        attempt_hours=round(attempt_ms / MS_PER_HOUR, 4),
        slots_per_hour=per_hour,
        run_hours=round(run_total / MS_PER_HOUR, 4) if run_total is not None else None,
        slots_per_hour_run=per_hour_run,
        latency_ms=stats(latency),
        slot_ms=stats(slot_ms),
        slots_over_cap=sum(1 for ms in slot_ms if ms > SLOT_CAP_MS),
        cells_complete=cells_complete,
        cells_failed=cells_failed,
        cell_failure_rate=ratio(cells_failed, cells_complete + cells_failed, 4),
        cell_failure_rate_by_profile={
            p: ratio(failed, complete + failed, 4) for p, (complete, failed) in by_profile.items()
        },
        slots_per_complete_cell=spread(complete_cell_slots),
        outcomes={code: outcomes[code] for code in OUTCOME_CODES},
        llm_status={s.value: llm[s.value] for s in LlmStatus},
        projection=Projection(
            banks=project_banks,
            attempts=round(mean_attempts * project_banks, 1) if mean_attempts is not None else None,
            slots=projected_slots,
            hours_one_bank_at_a_time=(
                ratio(projected_slots, per_hour, 1)
                if projected_slots is not None and per_hour is not None
                else None
            ),
            hours_at_run_rate=(
                ratio(projected_slots, per_hour_run, 1)
                if projected_slots is not None and per_hour_run is not None
                else None
            ),
            unavailable=(
                round(statuses.count("unavailable") / n_banks * project_banks, 1)
                if n_banks
                else None
            ),
        ),
    )


def _num(value: float | None, unit: str = "") -> str:
    if value is None:
        return "n/a"
    text = f"{value:,.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
    return f"{text}{unit}"


def _spread(value: Spread) -> str:
    if value.n == 0:
        return "none"
    return f"{_num(value.mean)} (min {_num(value.min)}, max {_num(value.max)}, n {value.n})"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def summary_markdown(
    summary: ThroughputSummary, *, title: str, preamble: Sequence[str] = ()
) -> str:
    """A short note (Markdown) of the summary, for the run notes and #28 planning."""
    s = summary
    hist = ", ".join(f"{k}: {v}" for k, v in s.attempts_histogram.items())
    by_profile = ", ".join(f"{p} {_pct(v)}" for p, v in s.cell_failure_rate_by_profile.items())
    total = s.slots or 1
    mix = ", ".join(f"{code} {n} ({n / total:.1%})" for code, n in s.outcomes.items() if n)
    calls = ", ".join(f"{k} {v}" for k, v in s.llm_status.items() if v) or "none"
    p = s.projection
    lines = [
        f"# {title}",
        "",
        *preamble,
        *([""] if preamble else []),
        "## Banks and attempts",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Banks | {s.banks} ({s.banks_complete} complete, {s.banks_unavailable} unavailable) |",
        f"| Attempts per bank | {_spread(s.attempts_per_bank)}; banks by attempts: {hist} |",
        f"| Attempts per complete bank | {_spread(s.attempts_per_complete_bank)} |",
        f"| Attempt failure rate | {_pct(s.attempt_failure_rate)} "
        f"({s.attempts_failed} of {s.attempts}) |",
        "",
        "## Throughput",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Slots | {s.slots:,}; per bank {_spread(s.slots_per_bank)} |",
        f"| Slots per complete attempt | {_spread(s.slots_per_complete_attempt)} |",
        f"| Slots per failed attempt | {_spread(s.slots_per_failed_attempt)} |",
        f"| Slots per hour (bank builder) | {_num(s.slots_per_hour)} "
        f"({_num(s.attempt_hours)} attempt hours) |",
        f"| Slots per hour (runs) | {_num(s.slots_per_hour_run)} ({_num(s.run_hours)} run hours) |",
        f"| Model latency | p50 {_num(s.latency_ms.p50, ' ms')}, p95 "
        f"{_num(s.latency_ms.p95, ' ms')}, max {_num(s.latency_ms.max, ' ms')} |",
        f"| Slot time (open to close) | p50 {_num(s.slot_ms.p50, ' ms')}, p95 "
        f"{_num(s.slot_ms.p95, ' ms')}, max {_num(s.slot_ms.max, ' ms')}; over the 40-s cap: "
        f"{s.slots_over_cap} |",
        "",
        "## Failures",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Cell failure rate | {_pct(s.cell_failure_rate)} ({s.cells_failed} failed, "
        f"{s.cells_complete} complete); by profile: {by_profile} |",
        f"| Slots per complete cell | {_spread(s.slots_per_complete_cell)} |",
        f"| Slot outcomes | {mix or 'none'} |",
        f"| Model calls by status | {calls} |",
        "",
        f"## Projection for {p.banks} banks",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Attempts | {_num(p.attempts)} |",
        f"| Slots | {_num(p.slots)} |",
        f"| Hours, one bank at a time | {_num(p.hours_one_bank_at_a_time)} |",
        f"| Hours at the measured run rate | {_num(p.hours_at_run_rate)} |",
        f"| Unavailable banks | {_num(p.unavailable)} |",
        "",
        "Rough planning figures from a small number of banks; definitions in `av_banks.metrics`.",
        "",
    ]
    return "\n".join(lines)
