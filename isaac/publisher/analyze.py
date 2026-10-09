"""Analyze measured publish deadlines; requires explicit complete-run metadata."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered)-1)*fraction
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high]-ordered[low])*(index-low)


def analyze(csv_path, metadata, required_seconds=3600):
    rows = list(csv.DictReader(Path(csv_path).open(encoding="utf-8", newline="")))
    rate = metadata["rate_hz"]
    if rate not in (30, 60) or required_seconds <= 0:
        raise ValueError("Invalid rate/duration")
    start, end = int(metadata["start_host_ns"]), int(metadata["end_host_ns"])
    if end <= start:
        raise ValueError("Invalid run clock interval")
    stamps = [int(row["host_monotonic_ns"]) for row in rows]
    if any(b <= a for a, b in zip(stamps, stamps[1:])) or any(t < start or t > end for t in stamps):
        raise ValueError("Publish stamps must be strictly increasing within run bounds")
    sequences = [int(row["seq"]) for row in rows]
    contiguous = sequences == list(range(len(rows)))
    sim_steps = [int(row["sim_step"]) for row in rows]
    progressing = all(b > a for a, b in zip(sim_steps, sim_steps[1:]))
    intervals = [(b-a)/1e6 for a, b in zip(stamps, stamps[1:])]
    gaps = intervals + ([(stamps[0]-start)/1e6, (end-stamps[-1])/1e6] if stamps else [(end-start)/1e6])
    nominal = 1000/rate
    jitter = [abs(x-nominal) for x in intervals]
    duration = (end-start)/1e9
    p99 = percentile(jitter, .99)
    max_gap = max(gaps)
    complete = metadata.get("completed") is True and duration >= required_seconds
    sufficient = len(rows) >= math.floor(required_seconds*rate)-1
    drops = max((int(row["queue_overwrites"]) for row in rows), default=0)
    missed = sum(int(row["missed_deadlines"]) for row in rows)
    timing_ok = len(rows) >= 2 and p99 <= nominal*.1 and max_gap <= 250 and drops == 0 and missed == 0
    passed = (complete and sufficient and duration >= 3600 and contiguous and progressing and metadata.get("source_kind") == "live"
              and metadata.get("schema_validated_frames") == len(rows) and not metadata.get("fault")
              and timing_ok)
    # Diagnostic attribution only (newer logs): stamp minus its scheduled tick.
    # It never enters the screen, which is computed from stamps alone.
    lags = [float(row["deadline_lag_ms"]) for row in rows if row.get("deadline_lag_ms") not in (None, "")]
    attribution = {}
    if lags and len(lags) == len(rows):
        attribution = dict(deadline_lag_median_ms=percentile(lags, .5), deadline_lag_p99_ms=percentile(lags, .99),
                           deadline_lag_max_ms=max(lags))
    return dict(rate_hz=rate, duration_s=duration, required_duration_s=required_seconds, frames=len(rows), source_kind=metadata.get("source_kind"),
                complete=complete, contiguous=contiguous, simulation_progressing=progressing,
                interval_median_ms=percentile(intervals,.5), interval_p99_ms=percentile(intervals,.99),
                absolute_period_error_p99_ms=p99, nominal_period_ms=nominal, max_gap_ms=max_gap,
                gaps_over_250_ms=sum(x > 250 for x in gaps), queue_overwrites=drops, missed_deadlines=missed,
                timing_screen=bool(timing_ok), rate_screen=bool(passed), **attribution,
                raw_csv_sha256=hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
                limitations=["Publisher timing only; does not qualify network, clocks or headset rendering",
                             "Disconnect/reconnect simulation-cost comparison is a separate check"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--required-seconds", type=float, default=3600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.csv, json.loads(args.metadata.read_text()), args.required_seconds)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))
    return 0 if result["rate_screen"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
