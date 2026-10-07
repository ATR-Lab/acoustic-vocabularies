"""Throughput of an attempt from its slot records (written to `attempt.json`).

Profiles run as independent streams, and several banks may run at once, as long as the
per-slot time stays under the 40-s slot cap (#26 proposed throughput). The summary
reports slots per minute, the model latency and the open-to-close slot time (nearest-
rank p50/p95 and max) and how many slots exceeded the cap, so a run on the LLM host can
show whether parallel streams are still within budget.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from av_generation.constants import SLOT_CAP_MS
from av_generation.records import SlotRecord

from av_banks.manifest import Stats, Throughput


def nearest_rank(values: Sequence[int], q: float) -> int | None:
    """The nearest-rank `q` quantile (0 < q <= 1) of `values`, or `None` when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def stats(values: Iterable[int]) -> Stats:
    data = [int(v) for v in values]
    return Stats(
        n=len(data),
        p50=nearest_rank(data, 0.5),
        p95=nearest_rank(data, 0.95),
        max=max(data) if data else None,
    )


def throughput(records: Sequence[SlotRecord], wall_ms: int) -> Throughput:
    """Throughput of the slot `records` of one attempt that took `wall_ms`."""
    slot_ms = [r.t_ms - r.t_open_ms for r in records]
    return Throughput(
        slots=len(records),
        wall_ms=wall_ms,
        slots_per_minute=round(len(records) * 60_000 / wall_ms, 3) if wall_ms > 0 else None,
        latency_ms=stats(r.latency_ms for r in records if r.latency_ms is not None),
        slot_ms=stats(slot_ms),
        slots_over_cap=sum(1 for ms in slot_ms if ms > SLOT_CAP_MS),
    )
