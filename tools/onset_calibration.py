"""#80 audio onset calibration analysis: capture WAV + logged requests -> record.

Generalises the O5.1.6 spike detector (spikes/O5.1.6/audio_onsets.py) from test
clicks to stored stimuli played through the app's playback path. One run analyses
one measured capture channel (acoustic coupler or electrical loopback), with an
optional second loopback reference channel reported as a diagnostic only.

This tool never measures anything. Hardware records the capture; the operator
declares device settings and independent clock-synchronisation evidence. A record
written here is always ``status: provisional``. See docs/unity/audio-calibration.md.

Fixed rules (detector ``onset-threshold/1``, target ``onset-target/1``):

* Onset is the first sample of the first run of ``consecutive_samples`` samples
  with ``|x| >= threshold_fs * full_scale`` that starts inside
  ``[ref + search_start_ms, ref + search_end_ms]`` (host clock, mapped to the
  capture clock by the affine sync fit). The threshold is frozen in settings.
* A play is ``clipped`` if any rail sample occurs from the quiet pre-window
  through the end of the search window, ``contaminated`` if the quiet pre-window
  ``[search_start - quiet_pre_ms, search_start)`` already crosses the threshold,
  ``outside_capture`` if its windows are not wholly inside the capture, else
  ``matched`` or ``missing``. Only matched plays contribute offsets.
* The reference time ref_i is always the logged ``scheduled_onset_mono_ms``
  (``reference_field`` is fixed by the schemas). Unity's ``AudioTiming.Schedule``
  starts output at the requested onset minus ``route_offset_ms`` and logs that
  start as ``scheduled_onset_mono_ms``, so the app's estimate is that field plus
  the measured offset. ``audio_request_mono_ms`` is never a reference.
* offset_i = onset_i - ref_i (host ms); route_offset_ms = median(offset);
  residual_i = offset_i - route_offset_ms; residual_abs_p95_ms is the type-7
  (linear interpolation) 95th percentile of |residual|.
* onset_uncertainty_ms = residual_abs_p95_ms + held-out sync max error +
  operator external sync bound. target met iff every requested play is matched,
  requested >= 200, every profile x stimulus-kind cell has a matched play and
  onset_uncertainty_ms <= 20.
"""
from __future__ import annotations

import argparse
import array
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
RECORD_SCHEMA = ROOT / "apparatus/schemas/audio-onset-calibration.schema.json"
SETTINGS_SCHEMA = ROOT / "apparatus/schemas/audio-onset-calibration-settings.schema.json"

TOOL = "tools/onset_calibration.py"
DETECTOR_VERSION = "onset-threshold/1"
TARGET_RULE = "onset-target/1"
SYNC_MODEL = "affine_capture_s_from_host_ms/1"
QUANTILE_METHOD = "linear_interpolation_hyndman_fan_7"
TARGET_P95_MS = 20
MIN_PLAYS = 200
PROFILES = ("P1", "P2", "P3")
KINDS = ("atom", "message")
REFERENCE_FIELD = "scheduled_onset_mono_ms"  # maintainer decision for #80; see the runbook
EVENT_COLUMNS = ["play_id", "stimulus_id", "profile", "stimulus_kind",
                 "audio_request_mono_ms", "scheduled_onset_mono_ms"]
SYNC_COLUMNS = ["host_mono_ms", "capture_s", "role"]
PLAY_COLUMNS = ["play_id", "stimulus_id", "profile", "stimulus_kind", "reference_mono_ms", "status",
                "onset_capture_sample", "onset_mono_ms", "offset_ms", "residual_ms",
                "reference_channel_status", "measure_minus_reference_ms"]
RECORD_NAME = "audio-onset-calibration.json"
PLAYS_NAME = "plays.csv"
HISTOGRAM_NAME = "residual-histogram.csv"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


class CalibrationInputError(ValueError):
    """Malformed or unsafe input; no output is written."""


def require(condition, message):
    if not condition:
        raise CalibrationInputError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def quantile(values, q):
    """Hyndman-Fan type 7 (linear interpolation between order statistics)."""
    data = sorted(values)
    require(data, "No observations")
    position = (len(data) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return data[lo] + (data[hi] - data[lo]) * (position - lo)


def _number(text, field):
    require(isinstance(text, str) and NUMBER.fullmatch(text), f"{field} must be a plain decimal number")
    value = float(text)
    require(math.isfinite(value), f"{field} must be finite")
    return value


def _validator(path):
    from jsonschema import Draft202012Validator, FormatChecker
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), format_checker=FormatChecker())


def _schema_errors(validator, instance):
    return sorted(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                  for e in validator.iter_errors(instance))


def _strict_json(data: bytes, what):
    def no_constants(token):
        raise CalibrationInputError(f"{what} contains non-finite number {token}")

    def unique(pairs):
        keys = [k for k, _ in pairs]
        require(len(keys) == len(set(keys)), f"{what} has duplicate keys")
        return dict(pairs)
    try:
        return json.loads(data.decode("utf-8"), parse_constant=no_constants, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CalibrationInputError(f"{what} is not valid UTF-8 JSON: {error}") from None


def load_settings(data: bytes):
    settings = _strict_json(data, "settings")
    errors = _schema_errors(_validator(SETTINGS_SCHEMA), settings)
    require(not errors, "settings invalid: " + "; ".join(errors))
    det = settings["detector"]
    require(settings["volume"]["step"] <= settings["volume"]["max_step"], "volume step exceeds max_step")
    require(det["search_end_ms"] > det["search_start_ms"], "search_end_ms must exceed search_start_ms")
    require(det["reference_channel"] != det["measure_channel"], "reference_channel must differ from measure_channel")
    try:
        dt.datetime.fromisoformat(settings["measured_at"].replace("Z", "+00:00"))
    except ValueError:
        raise CalibrationInputError("measured_at is not a valid timestamp") from None
    return settings


def _csv_rows(data: bytes, columns, what):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CalibrationInputError(f"{what} is not UTF-8") from None
    require(not text.startswith("﻿"), f"{what} must not have a byte-order mark")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        rows = list(reader)
    except csv.Error as error:
        raise CalibrationInputError(f"{what} is not valid CSV: {error}") from None
    require(rows and rows[0] == columns, f"{what} header must be exactly {','.join(columns)}")
    body = rows[1:]
    require(body, f"{what} has no rows")
    for line, row in enumerate(body, start=2):
        require(len(row) == len(columns), f"{what} line {line} has {len(row)} fields, expected {len(columns)}")
    return [dict(zip(columns, row)) for row in body]


def load_events(data: bytes, reference_field):
    require(reference_field == REFERENCE_FIELD, f"reference_field must be {REFERENCE_FIELD}")
    events, seen, previous = [], set(), -math.inf
    for index, row in enumerate(_csv_rows(data, EVENT_COLUMNS, "events"), start=2):
        require(ID.fullmatch(row["play_id"]), f"events line {index}: invalid play_id")
        require(row["play_id"] not in seen, f"events line {index}: duplicate play_id {row['play_id']}")
        seen.add(row["play_id"])
        require(ID.fullmatch(row["stimulus_id"]), f"events line {index}: invalid stimulus_id")
        require(row["profile"] in PROFILES, f"events line {index}: profile must be one of {PROFILES}")
        require(row["stimulus_kind"] in KINDS, f"events line {index}: stimulus_kind must be one of {KINDS}")
        request = _number(row["audio_request_mono_ms"], f"events line {index} audio_request_mono_ms")
        scheduled = None
        if row["scheduled_onset_mono_ms"] != "":
            scheduled = _number(row["scheduled_onset_mono_ms"], f"events line {index} scheduled_onset_mono_ms")
            require(scheduled >= request, f"events line {index}: scheduled onset precedes request")
        reference = scheduled
        require(reference is not None, f"events line {index}: {REFERENCE_FIELD} is blank")
        require(reference > previous, f"events line {index}: reference times must strictly increase")
        previous = reference
        events.append({**row, "reference_mono_ms": reference})
    return events


def fit_sync(data: bytes, external_bound_ms, evidence):
    """Affine capture_s = intercept + slope * host_ms / 1000 from independent anchors."""
    fit, check, hosts = [], [], set()
    for index, row in enumerate(_csv_rows(data, SYNC_COLUMNS, "sync"), start=2):
        host = _number(row["host_mono_ms"], f"sync line {index} host_mono_ms")
        capture = _number(row["capture_s"], f"sync line {index} capture_s")
        require(row["role"] in ("fit", "check"), f"sync line {index}: role must be fit or check")
        require(host not in hosts, "sync anchor host times must be unique; fit/check anchors cannot be reused")
        hosts.add(host)
        (fit if row["role"] == "fit" else check).append((host / 1000, capture))
    require(len(fit) >= 3 and len(check) >= 3, "need >=3 fit and >=3 held-out check sync anchors")
    x0 = statistics.fmean(x for x, _ in fit)
    y0 = statistics.fmean(y for _, y in fit)
    denominator = sum((x - x0) ** 2 for x, _ in fit)
    require(denominator > 0, "sync fit anchors need a time span")
    slope = sum((x - x0) * (y - y0) for x, y in fit) / denominator
    require(0.99 <= slope <= 1.01, "implausible capture/host clock ratio; verify units and timebase")
    intercept = y0 - slope * x0
    errors = [1000 * abs(y - (intercept + slope * x)) / slope for x, y in check]
    heldout_max = max(errors)
    return {"model": SYNC_MODEL, "fit_n": len(fit), "check_n": len(check), "slope": slope,
            "intercept_s": intercept, "heldout_p95_ms": quantile(errors, .95), "heldout_max_ms": heldout_max,
            "external_bound_ms": external_bound_ms, "bound_ms": heldout_max + external_bound_ms,
            "evidence": evidence, "host_min_ms": 1000 * min(x for x, _ in fit + check),
            "host_max_ms": 1000 * max(x for x, _ in fit + check)}


def read_capture(data: bytes, expected_rate):
    """Uncompressed little-endian 16/24-bit PCM WAV -> (rate, channel arrays, rails)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            require(source.getcomptype() == "NONE", "capture must be uncompressed PCM")
            width, rate, channels = source.getsampwidth(), source.getframerate(), source.getnchannels()
            frames = source.getnframes()
            raw = source.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise CalibrationInputError(f"capture is not a readable PCM WAV: {error}") from None
    require(width in (2, 3), "capture must be signed 16- or 24-bit PCM")
    require(rate == expected_rate, f"capture sample rate {rate} Hz does not match declared {expected_rate} Hz")
    require(frames > 0 and len(raw) == frames * width * channels, "capture is empty or truncated")
    if width == 2:
        samples = array.array("h")
        samples.frombytes(raw)
        rails = (32767, -32768)
    else:  # widen 24-bit to 32-bit by placing each sample in the top three bytes
        wide = bytearray(len(raw) // 3 * 4)
        wide[1::4], wide[2::4], wide[3::4] = raw[0::3], raw[1::3], raw[2::3]
        samples = array.array("i")
        samples.frombytes(bytes(wide))
        rails = (0x7FFFFF << 8, -(1 << 31))
    if sys.byteorder != "little":
        samples.byteswap()
    full_scale = -rails[1]
    return rate, [samples[c::channels] for c in range(channels)], rails, full_scale


def _onset(x, start, end, threshold, consecutive):
    """First index i in [start, end) starting a run of `consecutive` samples with |x| >= threshold."""
    run = 0
    for j in range(start, min(end + consecutive - 1, len(x))):
        value = x[j]
        if value >= threshold or value <= -threshold:
            run += 1
            if run == consecutive:
                first = j - consecutive + 1
                return first if first < end else None
        else:
            run = 0
            if j >= end:
                return None
    return None


def _classify(x, window, threshold, consecutive, rails):
    pre, start, end = window
    if pre < 0 or end + consecutive - 1 > len(x):
        return "outside_capture", None
    span = x[pre:end + consecutive - 1]
    if max(span) >= rails[0] or min(span) <= rails[1]:
        return "clipped", None
    quiet = x[pre:start]
    if quiet and (max(quiet) >= threshold or min(quiet) <= -threshold):
        return "contaminated", None
    onset = _onset(x, start, end, threshold, consecutive)
    return ("missing", None) if onset is None else ("matched", onset)


def detect_plays(events, channels, rate, rails, full_scale, sync, detector):
    """Per-play classification on the measured (and optional reference) channel."""
    measure = detector["measure_channel"]
    reference = detector["reference_channel"]
    require(measure < len(channels), f"measure_channel {measure} not in {len(channels)}-channel capture")
    require(reference is None or reference < len(channels),
            f"reference_channel {reference} not in {len(channels)}-channel capture")
    threshold = detector["threshold_fs"] * full_scale
    consecutive = detector["consecutive_samples"]
    slope, intercept = sync["slope"], sync["intercept_s"]

    def to_sample(host_ms):
        return (intercept + slope * host_ms / 1000) * rate

    plays, previous_end = [], -math.inf
    for event in events:
        ref = event["reference_mono_ms"]
        pre_ms = ref + detector["search_start_ms"] - detector["quiet_pre_ms"]
        end_ms = ref + detector["search_end_ms"]
        require(pre_ms > previous_end, f"play {event['play_id']}: analysis windows overlap the previous play")
        previous_end = end_ms
        require(sync["host_min_ms"] <= pre_ms and end_ms <= sync["host_max_ms"],
                f"play {event['play_id']}: outside sync anchor span; extrapolation refused")
        window = (math.ceil(to_sample(pre_ms)), math.ceil(to_sample(ref + detector["search_start_ms"])),
                  math.floor(to_sample(end_ms)) + 1)
        status, onset = _classify(channels[measure], window, threshold, consecutive, rails)
        play = {"event": event, "status": status, "onset": onset, "offset_ms": None,
                "onset_mono_ms": None, "reference_status": "", "delta_ms": None}
        if onset is not None:
            play["onset_mono_ms"] = (onset / rate - intercept) / slope * 1000
            play["offset_ms"] = play["onset_mono_ms"] - ref
        if reference is not None:
            play["reference_status"], ref_onset = _classify(channels[reference], window, threshold, consecutive, rails)
            if onset is not None and ref_onset is not None:
                play["delta_ms"] = (onset - ref_onset) / rate / slope * 1000
        plays.append(play)
    return plays


def summarize(plays, sync):
    """Median offset, residuals, p95 and the fixed target decision."""
    counts = {key: sum(p["status"] == key for p in plays)
              for key in ("matched", "missing", "clipped", "contaminated", "outside_capture")}
    coverage = {profile: {kind: sum(p["status"] == "matched" and p["event"]["profile"] == profile
                                    and p["event"]["stimulus_kind"] == kind for p in plays)
                          for kind in KINDS} for profile in PROFILES}
    offsets = [p["offset_ms"] for p in plays if p["status"] == "matched"]
    summary = {"plays": {"requested": len(plays), **counts, "coverage": coverage},
               "route_offset_ms": None, "offset_sd_ms": None, "residual_abs_p95_ms": None,
               "residual_abs_max_ms": None, "onset_uncertainty_ms": None, "residuals": []}
    if offsets:
        median = statistics.median(offsets)
        residuals = [o - median for o in offsets]
        p95 = quantile([abs(r) for r in residuals], .95)
        summary.update(route_offset_ms=median, offset_sd_ms=statistics.stdev(offsets) if len(offsets) > 1 else 0.0,
                       residual_abs_p95_ms=p95, residual_abs_max_ms=max(abs(r) for r in residuals),
                       onset_uncertainty_ms=p95 + sync["bound_ms"], residuals=residuals)
        for play in plays:
            if play["status"] == "matched":
                play["residual_ms"] = play["offset_ms"] - median
    complete = (len(plays) >= MIN_PLAYS and counts["matched"] == len(plays)
                and all(coverage[p][k] > 0 for p in PROFILES for k in KINDS))
    summary["target_met"] = bool(complete and summary["onset_uncertainty_ms"] is not None
                                 and summary["onset_uncertainty_ms"] <= TARGET_P95_MS)
    return summary


def _round(value, digits=6):
    if value is None:
        return None
    value = round(value, digits)
    return 0.0 if value == 0 else value


def _fmt(value, digits=6):
    return "" if value is None else f"{_round(value, digits):.{digits}f}"


def _csv_bytes(rows, columns):
    handle = io.StringIO(newline="")
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def plays_csv(plays):
    return _csv_bytes([[p["event"][k] for k in ("play_id", "stimulus_id", "profile", "stimulus_kind")] +
                       [_fmt(p["event"]["reference_mono_ms"]), p["status"],
                        "" if p["onset"] is None else str(p["onset"]), _fmt(p["onset_mono_ms"]),
                        _fmt(p["offset_ms"]), _fmt(p.get("residual_ms")), p["reference_status"],
                        _fmt(p["delta_ms"])] for p in plays], PLAY_COLUMNS)


def histogram_csv(residuals, bin_ms=1.0):
    """1 ms half-open bins [start, end) spanning every residual, empty bins included."""
    counts = {}
    for residual in residuals:
        key = math.floor(_round(residual) / bin_ms)
        counts[key] = counts.get(key, 0) + 1
    rows = [] if not counts else [[_fmt(k * bin_ms, 3), _fmt((k + 1) * bin_ms, 3), str(counts.get(k, 0))]
                                  for k in range(min(counts), max(counts) + 1)]
    return _csv_bytes(rows, ["bin_start_ms", "bin_end_ms", "count"])


def analyze_bytes(capture, events, sync_csv, settings_json):
    """Pure analysis: input bytes -> (record dict, plays CSV bytes, histogram CSV bytes)."""
    settings = load_settings(settings_json)
    detector = settings["detector"]
    event_rows = load_events(events, settings["reference_field"])
    sync = fit_sync(sync_csv, float(settings["sync"]["external_bound_ms"]), settings["sync"]["evidence"])
    rate, channels, rails, full_scale = read_capture(capture, settings["capture_sample_rate_hz"])
    plays = detect_plays(event_rows, channels, rate, rails, full_scale, sync, detector)
    summary = summarize(plays, sync)
    plays_bytes, histogram_bytes = plays_csv(plays), histogram_csv(summary["residuals"])
    loopback = None
    if detector["reference_channel"] is not None:
        deltas = [p["delta_ms"] for p in plays if p["delta_ms"] is not None]
        median = statistics.median(deltas) if deltas else None
        loopback = {"channel": detector["reference_channel"], "matched": len(deltas),
                    "median_delta_ms": _round(median),
                    "delta_abs_deviation_p95_ms": _round(quantile([abs(d - median) for d in deltas], .95))
                    if deltas else None}
    copied = ("evidence_kind", "station_id", "route", "connection_mode", "play_mode", "app_build_id",
              "output_device", "capture_interface", "capture_kind", "app_sample_rate_hz",
              "capture_sample_rate_hz", "buffer_frames", "buffer_count", "volume", "full_scene_loaded",
              "reference_field", "measured_at")
    record = {"schema_version": 1, "record_kind": "audio_onset_calibration", "status": "provisional",
              **{key: settings[key] for key in copied},
              "detector": {"version": DETECTOR_VERSION, "tool": TOOL, **detector, "quantile_method": QUANTILE_METHOD},
              "sync": {k: (_round(v, 9) if isinstance(v, float) else v) for k, v in sync.items()
                       if k not in ("host_min_ms", "host_max_ms")},
              "plays": summary["plays"],
              **{k: _round(summary[k]) for k in ("route_offset_ms", "offset_sd_ms", "residual_abs_p95_ms",
                                                 "residual_abs_max_ms", "onset_uncertainty_ms")},
              "target": {"rule": TARGET_RULE, "p95_limit_ms": TARGET_P95_MS, "min_plays": MIN_PLAYS,
                         "met": summary["target_met"]},
              "loopback_reference": loopback,
              "inputs": {"capture_wav_sha256": sha256(capture), "events_csv_sha256": sha256(events),
                         "sync_csv_sha256": sha256(sync_csv), "settings_json_sha256": sha256(settings_json)},
              "outputs": {"plays_csv_sha256": sha256(plays_bytes), "histogram_csv_sha256": sha256(histogram_bytes)},
              "response_time_qualification": None, "review": None}
    errors = _schema_errors(_validator(RECORD_SCHEMA), record)
    require(not errors, "internal error: record failed its schema: " + "; ".join(errors))
    return record, plays_bytes, histogram_bytes


def record_bytes(record):
    return (json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def analyze(capture: Path, events: Path, sync: Path, settings: Path, output: Path):
    record, plays_bytes, histogram_bytes = analyze_bytes(capture.read_bytes(), events.read_bytes(),
                                                         sync.read_bytes(), settings.read_bytes())
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
            f"output directory {output} must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    (output / PLAYS_NAME).write_bytes(plays_bytes)
    (output / HISTOGRAM_NAME).write_bytes(histogram_bytes)
    (output / RECORD_NAME).write_bytes(record_bytes(record))  # written last: its presence marks completion
    return record


def check(directory: Path):
    """Validate a stored record against its schema and its sibling output hashes."""
    data = (directory / RECORD_NAME).read_bytes()
    record = _strict_json(data, "record")
    errors = _schema_errors(_validator(RECORD_SCHEMA), record)
    require(not errors, "record invalid: " + "; ".join(errors))
    for name, key in ((PLAYS_NAME, "plays_csv_sha256"), (HISTOGRAM_NAME, "histogram_csv_sha256")):
        path = directory / name
        require(path.is_file() and sha256(path.read_bytes()) == record["outputs"][key], f"{name} hash mismatch")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("analyze", help="analyse one capture channel and write a provisional record")
    for name in ("capture", "events", "sync", "settings", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    verify = sub.add_parser("check", help="validate a record directory")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            record = check(args.directory)
        else:
            record = analyze(args.capture, args.events, args.sync, args.settings, args.output)
    except (CalibrationInputError, OSError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    plays = record["plays"]
    print(f"{record['status']} {record['evidence_kind']} {record['station_id']}/{record['route']} "
          f"{record['capture_kind']}: matched {plays['matched']}/{plays['requested']}, "
          f"route_offset_ms={record['route_offset_ms']}, residual_abs_p95_ms={record['residual_abs_p95_ms']}, "
          f"onset_uncertainty_ms={record['onset_uncertainty_ms']}, target_met={record['target']['met']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
