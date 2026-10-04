"""Offline non-study click generation, PCM onset detection and honest timing analysis.

No device/capture packages are required. Hardware records the WAV; this tool never
substitutes DSP time or a visual flash for acoustic onset.
"""
import argparse
import array
import csv
import json
import math
from pathlib import Path
import statistics
import sys
import wave


def quantile(values, q):
    data = sorted(values)
    if not data:
        raise ValueError("No observations")
    position = (len(data) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    return data[lo] + (data[hi] - data[lo]) * (position - lo)


def generate_click(path):
    """A 10 ms, 1 kHz Hann pulse; 48 kHz mono, signed 16-bit PCM, -18 dBFS peak."""
    samples = array.array("h", [round(32767 * 10 ** (-18 / 20) *
        math.sin(2 * math.pi * 1000 * i / 48000) *
        (0.5 - 0.5 * math.cos(2 * math.pi * i / 479))) for i in range(480)])
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def read_pcm(path):
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("Capture must be uncompressed signed 16-bit PCM WAV")
        rate, channels = source.getframerate(), source.getnchannels()
        data = array.array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        data.byteswap()
    return rate, [[v / 32768.0 for v in data[c::channels]] for c in range(channels)]


def detect(samples, rate, threshold, consecutive=3, refractory_s=0.1):
    """First sample of >= consecutive above-threshold absolute samples.

    Fixed amplitude criterion (not optimized per trial). Refractory prevents
    counting the same pulse twice; quiet interval must exceed it.
    """
    if not 0 < threshold < 1 or consecutive < 1 or rate <= 0:
        raise ValueError("Invalid onset detector parameters")
    onsets, count, eligible = [], 0, 0
    for i, value in enumerate(samples):
        if i < eligible:
            continue
        count = count + 1 if abs(value) >= threshold else 0
        if count == consecutive:
            start = i - consecutive + 1
            onsets.append(start / rate)
            eligible, count = i + round(refractory_s * rate), 0
    return onsets


def fit_sync(rows, external_bound_ms):
    """Affine capture_s = intercept + slope * host_s, from independent anchors.

    Held-out anchors test drift/model error; their error is NOT evidence about
    unmeasured display latency. external_bound_ms includes instrumentation and
    independent anchor uncertainty, established separately by the operator.
    """
    fit = [(float(r["host_s"]), float(r["capture_s"])) for r in rows if r["role"] == "fit"]
    check = [(float(r["host_s"]), float(r["capture_s"])) for r in rows if r["role"] == "check"]
    if len(fit) < 3 or len(check) < 3 or external_bound_ms < 0:
        raise ValueError("Need >=3 fit and >=3 independent check anchors and measured bound")
    x0, y0 = statistics.mean(x for x, _ in fit), statistics.mean(y for _, y in fit)
    denominator = sum((x - x0) ** 2 for x, _ in fit)
    if denominator <= 0:
        raise ValueError("Sync anchors need time span")
    slope = sum((x - x0) * (y - y0) for x, y in fit) / denominator
    if not 0.99 <= slope <= 1.01:
        raise ValueError("Implausible clock ratio; verify units/timebase")
    intercept = y0 - slope * x0
    errors = [1000 * abs(y - (intercept + slope * x)) for x, y in check]
    return {"intercept_s": intercept, "slope": slope,
            "host_min_s": min(x for x, _ in fit + check),
            "host_max_s": max(x for x, _ in fit + check),
            "heldout_p95_ms": quantile(errors, .95), "heldout_max_ms": max(errors),
            "external_bound_ms": external_bound_ms,
            "screening_bound_ms": max(errors) + external_bound_ms,
            "fit_n": len(fit), "check_n": len(check)}


def pair_events(events, onsets, sync, window_start_s=0, window_end_s=.8):
    """Non-overlapping event windows; missing/extra pulses never shift trial IDs."""
    if window_start_s >= window_end_s:
        raise ValueError("Invalid pairing window")
    ordered = sorted(events, key=lambda r: float(r["request_host_s"]))
    if len({r["trial_id"] for r in ordered}) != len(ordered):
        raise ValueError("Duplicate trial_id")
    output, used, previous_end = [], set(), -math.inf
    for event in ordered:
        host = float(event["request_host_s"])
        if not sync["host_min_s"] <= host <= sync["host_max_s"]:
            raise ValueError("Run falls outside sync anchors; extrapolation refused")
        request = sync["intercept_s"] + sync["slope"] * host
        low, high = request + window_start_s, request + window_end_s
        if low <= previous_end:
            raise ValueError("Pairing windows overlap; use a narrower declared window")
        previous_end = high
        hits = [(i, onset) for i, onset in enumerate(onsets) if low <= onset <= high]
        used.update(i for i, _ in hits)
        status = "matched" if len(hits) == 1 else "missing" if not hits else "ambiguous"
        output.append({**event, "status": status, "detected_pulses": len(hits),
            "onset_capture_s": hits[0][1] if status == "matched" else "",
            "offset_ms": 1000 * (hits[0][1] - request) / sync["slope"] if status == "matched" else ""})
    return output, len(onsets) - len(used)


def summarize(pairs, sync, settings, unmatched):
    values = [r["offset_ms"] for r in pairs if r["status"] == "matched"]
    result = {"settings": settings, "sync": sync, "requested": len(pairs),
              "matched": len(values), "missing": sum(r["status"] == "missing" for r in pairs),
              "ambiguous": sum(r["status"] == "ambiguous" for r in pairs), "unmatched_pulses": unmatched,
              "timing_claim": "pending", "observed_underruns": settings.get("observed_underruns")}
    if not values:
        return result
    mean = statistics.mean(values)
    residual = [abs(v - mean) for v in values]
    p95 = quantile(residual, .95)
    screening = p95 + sync["screening_bound_ms"]
    complete = len(values) >= 200 and len(values) == len(pairs) and unmatched == 0
    result.update(mean_offset_ms=mean, residual_sd_ms=statistics.stdev(values) if len(values) > 1 else None,
                  residual_abs_p95_ms=p95, residual_abs_max_ms=max(residual),
                  conservative_screening_ms=screening,
                  screening_target_met=complete and screening <= 20,
                  timing_claim="engineering screen only; confidence/coverage requires review")
    return result


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    click = sub.add_parser("click")
    click.add_argument("output", type=Path)
    analyze = sub.add_parser("analyze")
    for name in ("capture", "events", "sync", "settings", "output"):
        analyze.add_argument("--" + name, type=Path, required=True)
    analyze.add_argument("--channel", type=int, required=True, help="0-based acoustic or electrical channel")
    analyze.add_argument("--threshold", type=float, required=True)
    analyze.add_argument("--consecutive", type=int, default=3)
    analyze.add_argument("--window-start", type=float, default=0)
    analyze.add_argument("--window-end", type=float, default=.8)
    args = parser.parse_args()
    if args.command == "click":
        generate_click(args.output)
        return
    settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
    required = ("route", "mode", "capture_kind", "volume_step", "app_sample_rate_hz", "buffer_frames",
                "buffer_count", "sync_reference", "sync_external_bound_ms", "sync_calibration_evidence")
    if any(settings.get(key) is None or settings.get(key) == "" for key in required):
        raise ValueError("Complete settings and independently measured sync evidence required")
    if settings["sync_reference"] != "independent_hardware":
        raise ValueError("Photodiode command times alone cannot establish audible-onset timebase")
    if settings["capture_kind"] not in ("acoustic", "electrical"):
        raise ValueError("capture_kind must be acoustic or electrical")
    sync = fit_sync(read_csv(args.sync), float(settings["sync_external_bound_ms"]))
    rate, channels = read_pcm(args.capture)
    if not 0 <= args.channel < len(channels):
        raise ValueError("Requested channel not in capture")
    onsets = detect(channels[args.channel], rate, args.threshold, args.consecutive)
    pairs, unmatched = pair_events(read_csv(args.events), onsets, sync, args.window_start, args.window_end)
    args.output.mkdir(parents=True, exist_ok=True)
    if pairs:
        with (args.output / "onsets.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(pairs[0]))
            writer.writeheader()
            writer.writerows(pairs)
    result = summarize(pairs, sync, settings, unmatched)
    result["detector"] = {"sample_rate_hz": rate, "channel": args.channel, "threshold": args.threshold,
                          "consecutive": args.consecutive, "refractory_s": .1,
                          "window_start_s": args.window_start, "window_end_s": args.window_end}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
