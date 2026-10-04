"""Separate measured RTT/gaps from symmetry-dependent one-way estimates."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    x = (len(values) - 1) * q
    a, b = math.floor(x), math.ceil(x)
    return values[a] + (values[b] - values[a]) * (x - a)


def echo_statistics(c0, s1_ns, s2_ns, c3):
    s1, s2 = int(s1_ns) / 1e9, int(s2_ns) / 1e9
    c0, c3 = float(c0), float(c3)
    if not all(math.isfinite(x) for x in (c0, s1, s2, c3)) or s2 < s1 or c3 < c0:
        raise ValueError("Non-monotonic echo timestamps")
    rtt = c3 - c0 - (s2 - s1)
    if rtt < 0:
        raise ValueError("Negative network RTT; check clocks/units")
    return {"client_s": c3, "network_rtt_ms": rtt * 1000,
            "offset_server_minus_client_s": ((s1 - c0) + (s2 - c3)) / 2,
            "symmetry_uncertainty_ms": rtt * 500}


def summarize(rows, rate, requested_seconds=1800):
    states = sorted([r for r in rows if r["event"] == "state"], key=lambda r: float(r["recv_client_s"]))
    echoes, bad_echoes = [], 0
    for row in rows:
        if row["event"] != "echo":
            continue
        try:
            echoes.append(echo_statistics(row["c0_s"], row["s1_ns"], row["s2_ns"], row["c3_s"]))
        except (ValueError, TypeError):
            bad_echoes += 1
    starts = [float(r["recv_client_s"]) for r in rows if r["event"] == "run_start"]
    ends = [float(r["recv_client_s"]) for r in rows if r["event"] == "run_end"]
    boundaries = len(starts) == 1 and len(ends) == 1 and ends[0] >= starts[0]
    clock_times = starts + ends
    duration = ends[0] - starts[0] if boundaries else None
    times = [float(r["recv_client_s"]) for r in states]
    intervals = [b - a for a, b in zip(times, times[1:])]
    gaps = list(intervals)
    if times and clock_times:
        gaps.extend([max(0, times[0] - min(clock_times)), max(0, max(clock_times) - times[-1])])
    elif clock_times:
        gaps.append(max(clock_times) - min(clock_times))
    previous, missing, reordered, first_last = {}, 0, 0, {}
    progression, progression_failures = {}, 0
    latency, uncertainty, no_echo = [], [], 0
    for row in states:
        session, seq = row["session_id"], int(row["seq"])
        first_last.setdefault(session, [seq, seq])
        if session in previous:
            if seq <= previous[session]:
                reordered += 1
            else:
                missing += seq - previous[session] - 1
        previous[session] = max(seq, previous.get(session, seq))
        first_last[session][1] = max(first_last[session][1], seq)
        current_progress = (int(row["publish_host_ns"]), int(row.get("sim_step", seq)), float(row.get("sim_time", seq)))
        if session in progression and any(a <= b for a, b in zip(current_progress, progression[session])):
            progression_failures += 1
        else:
            progression[session] = current_progress
        received = float(row["recv_client_s"])
        nearby = [e for e in echoes if abs(e["client_s"] - received) <= 5]
        if nearby:
            estimate = min(nearby, key=lambda e: e["network_rtt_ms"])
            latency.append((received + estimate["offset_server_minus_client_s"] - int(row["publish_host_ns"]) / 1e9) * 1000)
            uncertainty.append(estimate["symmetry_uncertainty_ms"])
        else:
            no_echo += 1
    expected = sum(high - low + 1 for low, high in first_last.values())
    costs = [float(r["apply_ms"]) for r in states if r["apply_ms"]]
    detection_reconnect, previous_disconnect = [], None
    for row in sorted(rows, key=lambda r: float(r["recv_client_s"])):
        if row["event"] == "disconnected":
            previous_disconnect = float(row["recv_client_s"])
        elif row["event"] == "state" and previous_disconnect is not None:
            detection_reconnect.append(float(row["recv_client_s"]) - previous_disconnect)
            previous_disconnect = None
    jitter = [abs(interval - 1 / rate) * 1000 for interval in intervals]
    invalid = sum(r["event"] == "invalid_frame" for r in rows)
    bad_echoes += sum(r["event"] == "invalid_echo" for r in rows)
    freshness_events = {event: sum(r["event"] == event for r in rows) for event in
                        ("stale_source", "stale_queued_frame", "unknown_source_clock", "future_source_timestamp", "nonprogressing_state")}
    source_qualified = bool(states) and all(r.get("source_fresh") == "true" for r in states)
    renderer_applied = bool(states) and all(r.get("applied") == "true" for r in states)
    queue_drops = max([int(r["queue_drops"] or 0) for r in rows] or [0])
    live = bool(states) and all(r["source_kind"] == "live" for r in states)
    complete = (duration is not None and duration >= requested_seconds and
                any(r["event"] == "run_end" for r in rows) and live and invalid == 0)
    return {"frames": len(states), "source_is_live": live, "run_duration_s": duration,
        "complete_duration": complete, "rate_hz": rate, "start_end_boundaries_valid": boundaries,
        "start_markers": len(starts), "end_markers": len(ends),
        "rtt_median_ms": statistics.median([e["network_rtt_ms"] for e in echoes]) if echoes else None,
        "rtt_p95_ms": percentile([e["network_rtt_ms"] for e in echoes], .95),
        "one_way_estimate_median_ms": statistics.median(latency) if latency else None,
        "one_way_estimate_p95_ms": percentile(latency, .95),
        "one_way_assumption": "four-timestamp offset; symmetric path; minimum RTT sample within 5 s; not ground truth",
        "offset_symmetry_bound_p95_ms": percentile(uncertainty, .95),
        "frames_without_nearby_echo": no_echo, "invalid_echoes": bad_echoes,
        "interarrival_sd_ms": statistics.stdev(intervals) * 1000 if len(intervals) > 1 else None,
        "absolute_period_error_p95_ms": percentile(jitter, .95), "absolute_period_error_p99_ms": percentile(jitter, .99),
        "max_gap_ms": max(gaps) * 1000 if gaps else None, "gaps_over_250ms": sum(g > .25 for g in gaps),
        "missing_sequences_within_sessions": missing, "sequence_loss_rate": missing / expected if expected else None,
        "duplicate_or_reordered_frames": reordered, "session_count": len(previous),
        "client_queue_drops": queue_drops, "invalid_frames": invalid,
        "source_freshness_qualified": source_qualified, "all_frames_applied": renderer_applied,
        "freshness_rejections": freshness_events, "progression_violations": progression_failures,
        "main_apply_p95_ms": percentile(costs, .95), "main_apply_max_ms": max(costs) if costs else None,
        "disconnect_detection_to_first_state_s": detection_reconnect,
        "unrecovered_disconnect": previous_disconnect is not None,
        "steady_state_screen_met": (complete and bool(states) and not any(g > .25 for g in gaps) and reordered == 0 and queue_drops == 0
                                    and source_qualified and renderer_applied and no_echo == 0 and bad_echoes == 0
                                    and progression_failures == 0 and not any(freshness_events.values())
                                    and len(states) >= .95 * requested_seconds * rate),
        "recommendation": "pending topology/device validity, fault runs and total host CPU comparison"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--rate", type=int, choices=(30, 60), required=True)
    parser.add_argument("--seconds", type=float, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.input.open(newline="", encoding="utf-8-sig") as source:
        result = summarize(list(csv.DictReader(source)), args.rate, args.seconds)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))
