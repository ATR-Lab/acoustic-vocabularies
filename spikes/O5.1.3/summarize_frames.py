"""Summarize measured application frame intervals; never infer photon timing."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def summarize(rows):
    active = []
    previous = None
    for row in rows:
        tick = int(row["host_ticks"])
        if previous is not None and tick <= previous:
            raise ValueError("Host timestamps must be strictly increasing")
        previous = tick
        if row["measurement_active"] != "1":
            continue
        interval = float(row["interval_ms"])
        rate = float(row["refresh_hz"])
        if not math.isfinite(interval) or interval <= 0 or not math.isfinite(rate) or rate < 0:
            raise ValueError("Invalid frame interval or refresh rate")
        active.append((interval, rate, row["xr_running"] == "1"))
    if not active:
        raise ValueError("No active measurement frames; confirm XR reached ready")
    intervals = sorted(frame[0] for frame in active)
    percentile = lambda fraction: intervals[math.ceil(fraction * len(intervals)) - 1]
    rates = sorted(set(frame[1] for frame in active if frame[1] > 0))
    duration = sum(intervals) / 1000
    return {
        "measurement_kind": "application_update_intervals_not_photon_presentation",
        "samples": len(active),
        "covered_seconds": duration,
        "mean_ms": statistics.fmean(intervals),
        "p95_ms": percentile(.95),
        "p99_ms": percentile(.99),
        "max_ms": max(intervals),
        "observed_refresh_hz": rates,
        "frames_over_observed_budget": sum(dt > 1000 / hz for dt, hz, _ in active if hz > 0),
        "frames_with_unknown_budget": sum(hz == 0 for _, hz, _ in active),
        "application_gaps_above_250ms": sum(dt > 250 for dt in intervals),
        "frames_without_running_xr": sum(not running for _, _, running in active),
        "at_least_15_minutes": duration >= 900,
        "single_observed_refresh_rate": len(rates) == 1 and all(hz > 0 for _, hz, _ in active),
        "presentation_freezes_above_250ms": None,
        "presentation_evidence": "pending_external_runtime_metrics_or_capture",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.csv.open(newline="", encoding="utf-8-sig") as handle:
        result = summarize(csv.DictReader(handle))
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
