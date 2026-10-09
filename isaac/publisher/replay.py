"""Counterfactual replay of a pre-sampled hour log (estimate, not evidence of a pass).

In the ``presample`` loop, tick k starts when frame k-1 has been handed off and
is ready after physics P_k plus sample/encode S_k (``serialize_ms``). When a
tick overruns, its deadline lag satisfies

    lag_k = lag_{k-1} + handoff_{k-1} + P_k + S_k - T * (1 + missed_k)

so P_k is recoverable on overrun ticks. On on-time ticks only an upper bound
is known. The replay keeps every recorded S_k, handoff and on-time wake lag,
uses recovered P_k where known and min(median recovered P, bound) elsewhere,
replaces S_k with a counterfactual value and re-applies the publisher's
deadline rule (late frames publish once; skipped ticks count as missed).
The identity replay (unchanged S) is reported so the model's fidelity can be
judged. The output is an estimate for planning only; it never substitutes for
a measured hour.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .analyze import percentile

LATE_MS = .5  # lag above this is treated as an overrun rather than wake-up


def load(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or "deadline_lag_ms" not in rows[0]:
        raise ValueError("A pre-sampled publish log with deadline_lag_ms is required")
    return [dict(lag=float(r["deadline_lag_ms"]), serialize=float(r["serialize_ms"]),
                 handoff=float(r["handoff_ms"]), missed=int(r["missed_deadlines"])) for r in rows]


def infer_physics(rows, rate_hz=30):
    """Recovered P_k on overrun ticks, bounds elsewhere, and the median."""
    period = 1000/rate_hz
    known, bounds = {}, {}
    for k in range(1, len(rows)):
        previous, current = rows[k-1], rows[k]
        budget = period*(1 + current["missed"]) - previous["lag"] - previous["handoff"]
        if current["lag"] > LATE_MS:
            known[k] = current["lag"] + budget - current["serialize"]
        else:
            bounds[k] = budget + current["lag"] - current["serialize"]
    values = sorted(known.values())
    median = values[len(values)//2] if values else None
    return known, bounds, median


def replay(rows, serialize, *, rate_hz=30, physics=None):
    """Return lag/missed/interval-error statistics for counterfactual S values."""
    period = 1000/rate_hz
    known, bounds, median = physics or infer_physics(rows, rate_hz)
    wakes = sorted(r["lag"] for r in rows if r["lag"] <= LATE_MS)
    wake_default = wakes[len(wakes)//2] if wakes else 0.
    lags, missed, ticks = [rows[0]["lag"]], 0, [0]
    for k in range(1, len(rows)):
        p = known.get(k)
        if p is None:
            p = min(median, bounds[k]) if median is not None else bounds[k]
        wake = rows[k]["lag"] if rows[k]["lag"] <= LATE_MS else wake_default
        late = lags[-1] + rows[k-1]["handoff"] + p + serialize[k] - period
        skipped = 0
        while late >= period:  # the publisher claims the latest due tick
            late -= period
            skipped += 1
        missed += skipped
        lags.append(max(wake, late))
        ticks.append(ticks[-1] + 1 + skipped)
    stamps = [t*period + lag for t, lag in zip(ticks, lags)]
    errors = [abs(b - a - period) for a, b in zip(stamps, stamps[1:])]
    return dict(missed_deadlines=missed, interval_error_p99_ms=percentile(errors, .99),
                interval_error_max_ms=max(errors), intervals_over_limit=sum(e > period*.1 for e in errors),
                lag_p99_ms=percentile(lags, .99), lag_max_ms=max(lags),
                overrun_ticks=sum(lag > LATE_MS for lag in lags))


def counterfactuals(rows, *, saving_ms, object_share, object_factor, rate_hz=30):
    physics = infer_physics(rows, rate_hz)
    s = [r["serialize"] for r in rows]
    known, _, median = physics
    return dict(
        recovered_physics_ticks=len(known), recovered_physics_median_ms=median,
        identity=replay(rows, s, rate_hz=rate_hz, physics=physics),
        additive=dict(saving_ms=saving_ms, **replay(rows, [max(0., x - saving_ms) for x in s], rate_hz=rate_hz, physics=physics)),
        proportional=dict(object_share=object_share, object_factor=object_factor,
                          **replay(rows, [x*(1 - object_share*(1 - object_factor)) for x in s],
                                   rate_hz=rate_hz, physics=physics)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--saving-ms", type=float, default=3.146, help="Additive S reduction (cached read median saving)")
    parser.add_argument("--object-share", type=float, default=.745, help="Object-read share of S for the proportional case")
    parser.add_argument("--object-factor", type=float, default=.673, help="Cached/uncached object-read ratio")
    args = parser.parse_args(argv)
    result = counterfactuals(load(args.csv), saving_ms=args.saving_ms, object_share=args.object_share,
                             object_factor=args.object_factor)
    print(json.dumps(dict(estimate_only=True, **result), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
