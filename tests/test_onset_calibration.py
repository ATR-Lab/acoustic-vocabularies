"""Synthetic captures exercise tools/onset_calibration.py; none is a route measurement.

Bursts are integer square waves (no libm) and jitter is integer-sample, so the
generated WAV bytes, and therefore the committed example record, are reproducible.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import random
import struct
import sys
import wave

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import onset_calibration as oc  # noqa: E402

SETTINGS_EXAMPLE = ROOT / "apparatus/examples/audio-onset-calibration-settings.example.json"
RECORD_EXAMPLE = ROOT / "apparatus/examples/audio-onset-calibration.example.json"
HOST0 = 1_000_000.0  # host monotonic ms at capture sample 1.0 s
SLOPE = 1.00002      # capture clock runs 20 ppm fast
AMPLITUDE = 16000
BURST = 960          # 20 ms at 48 kHz


def settings(**changes):
    value = json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))
    for key, item in changes.items():
        if key in value["detector"]:
            value["detector"][key] = item
        else:
            value[key] = item
    return value


def capture_s(host_ms):
    return 1.0 + SLOPE * (host_ms - HOST0) / 1000


def synth(n=200, offset_ms=37.5, jitter=96, seed=80, rate=48000, channels=2, loopback_lead_ms=4.0,
          dropped=(), clipped=(), noisy=(), width=2, silent=False):
    """Return (wav, events, sync, injected offsets in host ms keyed by play_id).

    Each ref is the logged scheduled_onset_mono_ms (the fixed reference); the
    request is logged 200 ms earlier and plays no part in the offset.
    """
    rng = random.Random(seed)
    refs = [HOST0 + 500 + 300 * i + 0.25 * (i % 4) for i in range(n)]
    frames = round(capture_s(refs[-1] + 1000) * rate)
    data = [bytearray(frames * width) for _ in range(channels)]
    full = 32767 if width == 2 else 0x7FFFFF

    def put(channel, start, length, level):
        for k in range(length):
            value = level if (k // 24) % 2 == 0 else -level
            data[channel][(start + k) * width:(start + k + 1) * width] = value.to_bytes(width, "little", signed=True)

    injected, rows = {}, []
    for i, ref in enumerate(refs):
        play = f"play-{i:04d}"
        profile, kind = oc.PROFILES[i % 3], oc.KINDS[(i // 3) % 2]
        rows.append([play, f"stim-{profile}-{kind}-{i % 4}", profile, kind, f"{ref - 200:.2f}", f"{ref:.2f}"])
        onset = round(capture_s(ref + offset_ms) * rate) + sum(rng.randint(-jitter, jitter) for _ in range(3))
        injected[play] = ((onset / rate - (1.0 - SLOPE * HOST0 / 1000)) / SLOPE * 1000) - ref
        if silent:
            continue
        scale = 1 if width == 2 else 256
        level = full if i in clipped else AMPLITUDE * scale
        if i not in dropped:  # acoustic dropout; the loopback reference still carries the play
            put(0, onset, BURST, level)
        if i in noisy:
            put(0, round(capture_s(ref - 20) * rate), 10, AMPLITUDE * scale)  # inside the quiet pre-window
        if channels > 1:
            put(1, onset - round(loopback_lead_ms * rate / 1000), BURST, AMPLITUDE * scale)
    interleaved = bytearray(frames * width * channels)
    stride = width * channels
    for c in range(channels):
        for b in range(width):
            interleaved[c * width + b::stride] = data[c][b::width]
    wav = io.BytesIO()
    with wave.open(wav, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(bytes(interleaved))
    events = "\n".join([",".join(oc.EVENT_COLUMNS)] + [",".join(r) for r in rows]) + "\n"
    end = refs[-1] + 1000
    anchors = [HOST0 + (end - HOST0) * k / 7 for k in range(8)]
    sync = "\n".join(["host_mono_ms,capture_s,role"] + [f"{h:.3f},{capture_s(h):.9f},{'fit' if k % 2 == 0 else 'check'}"
                                                         for k, h in enumerate(anchors)]) + "\n"
    return wav.getvalue(), events.encode(), sync.encode(), injected


def encode(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def run(inputs, settings_value=None):
    wav, events, sync, _ = inputs
    blob = SETTINGS_EXAMPLE.read_bytes() if settings_value is None else encode(settings_value)
    return oc.analyze_bytes(wav, events, sync, blob)


def plays_by_id(plays_bytes):
    import csv
    return {row["play_id"]: row for row in csv.DictReader(io.StringIO(plays_bytes.decode()))}


@pytest.fixture(scope="module")
def nominal():
    inputs = synth()
    return inputs, run(inputs)


def test_recovers_injected_offsets_and_meets_target(nominal):
    (_, _, _, injected), (record, plays_bytes, _) = nominal
    rows = plays_by_id(plays_bytes)
    assert record["plays"]["matched"] == record["plays"]["requested"] == 200
    for play, truth in injected.items():
        assert rows[play]["status"] == "matched"
        assert abs(float(rows[play]["offset_ms"]) - truth) < 0.01
    median = sorted(injected.values())
    median = (median[99] + median[100]) / 2
    assert abs(record["route_offset_ms"] - median) < 0.01
    p95 = oc.quantile([abs(v - median) for v in injected.values()], .95)
    assert abs(record["residual_abs_p95_ms"] - p95) < 0.01
    assert 3 < record["residual_abs_p95_ms"] < 6
    assert record["sync"]["heldout_max_ms"] < 0.001 and record["sync"]["external_bound_ms"] == 1.0
    assert record["onset_uncertainty_ms"] == pytest.approx(p95 + record["sync"]["bound_ms"], abs=0.01)
    assert record["target"]["met"] is True and record["status"] == "provisional"
    cells = [n for cell in record["plays"]["coverage"].values() for n in cell.values()]
    assert len(cells) == 6 and min(cells) > 0 and sum(cells) == 200


def test_loopback_reference_delta_is_diagnostic(nominal):
    _, (record, plays_bytes, _) = nominal
    assert record["loopback_reference"]["matched"] == 200
    assert record["loopback_reference"]["median_delta_ms"] == pytest.approx(4.0 / SLOPE, abs=0.03)
    assert record["loopback_reference"]["delta_abs_deviation_p95_ms"] < 0.03
    assert all(r["reference_channel_status"] == "matched" for r in plays_by_id(plays_bytes).values())


def test_histogram_counts_every_matched_residual(nominal):
    _, (_, _, histogram) = nominal
    lines = histogram.decode().splitlines()
    assert lines[0] == "bin_start_ms,bin_end_ms,count"
    assert sum(int(line.split(",")[2]) for line in lines[1:]) == 200
    starts = [float(line.split(",")[0]) for line in lines[1:]]
    assert starts == [starts[0] + k for k in range(len(starts))]


def test_record_validates_and_matches_committed_example(nominal):
    _, (record, plays_bytes, histogram) = nominal
    schema = json.loads(oc.RECORD_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)
    assert oc.record_bytes(record) == RECORD_EXAMPLE.read_bytes(), (
        "example record drifted; regenerate it from this synthetic run")
    assert record["outputs"]["plays_csv_sha256"] == oc.sha256(plays_bytes)
    assert record["outputs"]["histogram_csv_sha256"] == oc.sha256(histogram)


def test_outputs_are_deterministic_and_check_round_trips(nominal, tmp_path):
    inputs, (record, _, _) = nominal
    paths = {}
    for name, blob in zip(("capture.wav", "events.csv", "sync.csv"), inputs[:3]):
        paths[name] = tmp_path / name
        paths[name].write_bytes(blob)
    outputs = []
    for label in ("a", "b"):
        assert oc.main(["analyze", "--capture", str(paths["capture.wav"]), "--events", str(paths["events.csv"]),
                        "--sync", str(paths["sync.csv"]), "--settings", str(SETTINGS_EXAMPLE),
                        "--output", str(tmp_path / label)]) == 0
        outputs.append({p.name: p.read_bytes() for p in sorted((tmp_path / label).iterdir())})
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0][oc.RECORD_NAME]) == record
    assert oc.main(["check", str(tmp_path / "a")]) == 0
    (tmp_path / "a" / oc.PLAYS_NAME).write_bytes(outputs[0][oc.PLAYS_NAME] + b"\n")
    assert oc.main(["check", str(tmp_path / "a")]) == 2
    # Refuses to mix with an existing analysis.
    assert oc.main(["analyze", "--capture", str(paths["capture.wav"]), "--events", str(paths["events.csv"]),
                    "--sync", str(paths["sync.csv"]), "--settings", str(SETTINGS_EXAMPLE),
                    "--output", str(tmp_path / "b")]) == 2


def test_large_jitter_fails_target():
    record, _, _ = run(synth(jitter=600, seed=3))
    assert record["plays"]["matched"] == 200
    assert record["residual_abs_p95_ms"] > 20 and record["target"]["met"] is False


def test_target_boundary_and_completeness_rules():
    sync = {"bound_ms": 1.0}

    def plays(offsets, profiles=oc.PROFILES):
        return [{"status": "matched", "offset_ms": o, "event": {"profile": profiles[i % len(profiles)],
                                                                "stimulus_kind": oc.KINDS[(i // 3) % 2]}}
                for i, o in enumerate(offsets)]
    # Offsets spread about 50 ms by up to 19 ms; the decision uses p95(|residual|) + 1 ms sync bound.
    offsets = [50.0 + (i % 20) * (1 if i % 2 else -1) for i in range(200)]
    summary = oc.summarize(plays(offsets), sync)
    expected = oc.quantile([abs(o - summary["route_offset_ms"]) for o in offsets], .95)
    assert summary["residual_abs_p95_ms"] == pytest.approx(expected)
    assert summary["onset_uncertainty_ms"] == pytest.approx(expected + 1.0)
    assert summary["target_met"] is (expected + 1.0 <= 20)
    assert oc.summarize(plays([o * 1.06 - 3 for o in offsets]), sync)["target_met"] is False
    tight = [50.0 + (i % 5) for i in range(200)]
    assert oc.summarize(plays(tight), sync)["target_met"] is True
    assert oc.summarize(plays(tight[:199]), sync)["target_met"] is False          # < 200 plays
    assert oc.summarize(plays(tight, ("P1", "P2")), sync)["target_met"] is False  # P3 not covered
    assert oc.summarize(plays(tight), {"bound_ms": 18.5})["target_met"] is False   # sync bound counts
    one_missing = plays(tight) + [{"status": "missing", "offset_ms": None,
                                   "event": {"profile": "P1", "stimulus_kind": "atom"}}]
    assert oc.summarize(one_missing, sync)["target_met"] is False


def test_quantile_type7():
    assert oc.quantile([1, 2, 3, 4], .5) == 2.5
    assert oc.quantile(list(range(1, 101)), .95) == pytest.approx(95.05)
    with pytest.raises(oc.CalibrationInputError):
        oc.quantile([], .95)


def test_dropouts_clipping_and_contamination_are_classified():
    inputs = synth(n=12, dropped={2, 7}, clipped={4}, noisy={9})
    record, plays_bytes, _ = run(inputs)
    rows = plays_by_id(plays_bytes)
    assert [rows[f"play-{i:04d}"]["status"] for i in (2, 7, 4, 9, 0)] == [
        "missing", "missing", "clipped", "contaminated", "matched"]
    assert rows["play-0002"]["offset_ms"] == "" and rows["play-0002"]["reference_channel_status"] == "matched"
    p = record["plays"]
    assert (p["requested"], p["matched"], p["missing"], p["clipped"], p["contaminated"]) == (12, 8, 2, 1, 1)
    assert record["target"]["met"] is False and record["route_offset_ms"] is not None


def test_silent_capture_is_recorded_not_qualified():
    record, plays_bytes, histogram = run(synth(n=6, silent=True))
    assert record["plays"]["missing"] == 6 and record["plays"]["matched"] == 0
    assert record["route_offset_ms"] is None and record["onset_uncertainty_ms"] is None
    assert record["loopback_reference"]["matched"] == 0 and record["target"]["met"] is False
    assert histogram == b"bin_start_ms,bin_end_ms,count\n"


def test_truncated_capture_marks_plays_outside_capture():
    wav, events, sync, _ = synth(n=6)
    with wave.open(io.BytesIO(wav)) as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setparams(params)
        target.writeframes(frames[:len(frames) // 2])
    record, _, _ = run((out.getvalue(), events, sync, None))
    assert record["plays"]["outside_capture"] > 0 and record["target"]["met"] is False


def test_24_bit_capture_and_single_channel():
    record, _, _ = run(synth(n=6, width=3, channels=1), settings(reference_channel=None))
    assert record["plays"]["matched"] == 6 and record["loopback_reference"] is None


def test_wrong_sample_rate_is_refused():
    with pytest.raises(oc.CalibrationInputError, match="sample rate"):
        run(synth(n=4, rate=44100))
    with pytest.raises(oc.CalibrationInputError, match="settings invalid"):
        run(synth(n=4), settings(app_sample_rate_hz=44100))


def mutate_events(text, line, column, value):
    rows = [r.split(",") for r in text.decode().splitlines()]
    rows[line][column] = value
    return ("\n".join(",".join(r) for r in rows) + "\n").encode()


@pytest.mark.parametrize("change, message", [
    (lambda e: mutate_events(e, 2, 0, "play-0000"), "duplicate play_id"),
    (lambda e: mutate_events(mutate_events(e, 2, 4, "1000000.00"), 2, 5, "1000000.00"), "strictly increase"),
    (lambda e: mutate_events(e, 1, 4, "nan"), "plain decimal"),
    (lambda e: mutate_events(e, 1, 4, "1e6"), "plain decimal"),
    (lambda e: mutate_events(e, 1, 2, "P4"), "profile"),
    (lambda e: mutate_events(e, 1, 3, "word"), "stimulus_kind"),
    (lambda e: mutate_events(e, 1, 5, "1.00"), "precedes request"),
    (lambda e: e.replace(b"scheduled_onset_mono_ms", b"scheduled_onset_mono_ms,extra", 1), "header"),
    (lambda e: b"\xef\xbb\xbf" + e, "byte-order mark"),
    (lambda e: e.splitlines(keepends=True)[0], "no rows"),
])
def test_malformed_events_refused(change, message):
    wav, events, sync, _ = synth(n=4)
    with pytest.raises(oc.CalibrationInputError, match=message):
        run((wav, change(events), sync, None))


def test_blank_scheduled_reference_refused():
    wav, events, sync, _ = synth(n=4)
    with pytest.raises(oc.CalibrationInputError, match="scheduled_onset_mono_ms is blank"):
        run((wav, mutate_events(events, 1, 5, ""), sync, None))


def test_reference_is_scheduled_onset_not_request():
    """Maintainer decision (#80): offsets are measured from scheduled_onset_mono_ms."""
    assert json.loads(SETTINGS_EXAMPLE.read_bytes())["reference_field"] == oc.REFERENCE_FIELD == "scheduled_onset_mono_ms"
    with pytest.raises(oc.CalibrationInputError, match="settings invalid"):
        run(synth(n=4), settings(reference_field="audio_request_mono_ms"))
    with pytest.raises(oc.CalibrationInputError, match="reference_field must be"):
        oc.load_events(synth(n=4)[1], "audio_request_mono_ms")
    # Moving every request earlier leaves the offsets unchanged; moving the scheduled onset shifts them.
    wav, events, sync, _ = synth(n=6)
    rows = [r.split(",") for r in events.decode().splitlines()]
    earlier = [rows[0]] + [r[:4] + [f"{float(r[4]) - 50:.2f}", r[5]] for r in rows[1:]]
    base, _, _ = run((wav, events, sync, None))
    moved, _, _ = run((wav, ("\n".join(",".join(r) for r in earlier) + "\n").encode(), sync, None))
    assert moved["route_offset_ms"] == base["route_offset_ms"]
    later = [rows[0]] + [r[:5] + [f"{float(r[5]) + 10:.2f}"] for r in rows[1:]]
    shifted, _, _ = run((wav, ("\n".join(",".join(r) for r in later) + "\n").encode(), sync, None))
    assert shifted["route_offset_ms"] == pytest.approx(base["route_offset_ms"] - 10, abs=1e-6)


@pytest.mark.parametrize("changes, message", [
    ({"threshold_fs": 1.0}, "settings invalid"),
    ({"threshold_fs": 0}, "settings invalid"),
    ({"evidence_kind": "guess"}, "settings invalid"),
    ({"connection_mode": "bluetooth"}, "settings invalid"),
    ({"measured_at": "2026-13-40T00:00:00Z"}, "measured_at|settings invalid"),
    ({"measured_at": "yesterday"}, "settings invalid"),
    ({"search_end_ms": -10.0}, "search_end_ms"),
    ({"reference_channel": 0}, "reference_channel must differ"),
    ({"measure_channel": 5}, "not in 2-channel"),
    ({"volume": {"step": 9, "max_step": 8}}, "volume step"),
    ({"unknown": True}, "settings invalid"),
])
def test_malformed_settings_refused(changes, message):
    with pytest.raises(oc.CalibrationInputError, match=message):
        run(synth(n=4), settings(**changes))


def test_settings_json_must_be_strict():
    wav, events, sync, _ = synth(n=4)
    for blob in (b'{"schema_version": NaN}', b'{"a": 1, "a": 2}', b"\xff"):
        with pytest.raises(oc.CalibrationInputError):
            oc.analyze_bytes(wav, events, sync, blob)


def test_sync_and_window_refusals():
    wav, events, sync, _ = synth(n=4)
    lines = sync.decode().splitlines()
    few = ("\n".join(lines[:5]) + "\n").encode()
    with pytest.raises(oc.CalibrationInputError, match=">=3 fit"):
        run((wav, events, few, None))
    reused = lines[:]
    reused[2] = reused[1].rsplit(",", 1)[0] + ",check"
    with pytest.raises(oc.CalibrationInputError, match="unique"):
        run((wav, events, ("\n".join(reused) + "\n").encode(), None))
    short = [lines[0]] + [f"{HOST0 + k * 100:.3f},{capture_s(HOST0 + k * 100):.9f},{'fit' if k % 2 else 'check'}"
                          for k in range(6)]
    with pytest.raises(oc.CalibrationInputError, match="extrapolation"):
        run((wav, events, ("\n".join(short) + "\n").encode(), None))
    bad_ratio = [lines[0]] + [f"{float(l.split(',')[0]):.3f},{float(l.split(',')[1]) * 1.5:.9f},{l.split(',')[2]}"
                              for l in lines[1:]]
    with pytest.raises(oc.CalibrationInputError, match="clock ratio"):
        run((wav, events, ("\n".join(bad_ratio) + "\n").encode(), None))
    with pytest.raises(oc.CalibrationInputError, match="overlap"):
        run((wav, events, sync, None), settings(search_end_ms=400.0))


def test_non_pcm_captures_refused():
    wav, events, sync, _ = synth(n=4)
    with pytest.raises(oc.CalibrationInputError, match="WAV"):
        run((b"RIFF0000WAVEjunk", events, sync, None))
    eight = io.BytesIO()
    with wave.open(eight, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(1)
        out.setframerate(48000)
        out.writeframes(bytes(4800))
    with pytest.raises(oc.CalibrationInputError, match="16- or 24-bit"):
        run((eight.getvalue(), events, sync, None))
    float_wav = bytearray(wav)
    float_wav[20:22] = struct.pack("<H", 3)  # WAVE_FORMAT_IEEE_FLOAT
    with pytest.raises(oc.CalibrationInputError):
        run((bytes(float_wav), events, sync, None))


VALIDATOR = Draft202012Validator(json.loads(oc.RECORD_SCHEMA.read_text(encoding="utf-8")),
                                 format_checker=FormatChecker())


def test_schema_keeps_synthetic_records_provisional_and_gates_qualified():
    example = json.loads(RECORD_EXAMPLE.read_text(encoding="utf-8"))
    assert VALIDATOR.is_valid(example)
    synthetic_qualified = deepcopy(example)
    synthetic_qualified["status"] = "qualified"
    synthetic_qualified["review"] = {"reviewed_by": "Reviewer", "reviewed_at": "2027-04-07T10:00:00Z"}
    assert not VALIDATOR.is_valid(synthetic_qualified)
    physical = deepcopy(synthetic_qualified)
    physical["evidence_kind"] = "physical_measurement"
    physical["full_scene_loaded"] = True
    assert VALIDATOR.is_valid(physical)
    for key, value in (("review", None), ("capture_kind", "electrical_loopback"), ("full_scene_loaded", False),
                       ("play_mode", "plain"), ("reference_field", "audio_request_mono_ms")):
        broken = deepcopy(physical)
        broken[key] = value
        assert not VALIDATOR.is_valid(broken), key
    failed = deepcopy(physical)
    failed["target"]["met"] = False
    assert not VALIDATOR.is_valid(failed)
    failed["response_time_qualification"] = {"statement": "RT analyses qualified by +/-25 ms onset uncertainty.",
                                             "signed_by": "Protocol owner", "signed_at": "2027-04-07T11:00:00Z"}
    assert VALIDATOR.is_valid(failed)


def test_schema_rejects_inconsistent_pass_and_bad_values():
    example = json.loads(RECORD_EXAMPLE.read_text(encoding="utf-8"))
    for path, value in ((("onset_uncertainty_ms",), 20.5), (("plays", "matched"), 150),
                        (("route_offset_ms",), None), (("app_sample_rate_hz",), 44100),
                        (("inputs", "capture_wav_sha256"), "0" * 63), (("station_id",), "bad id\n"),
                        (("detector", "version"), "onset-threshold/2"), (("target", "p95_limit_ms"), 25),
                        (("measured_at",), "2026-10-07")):
        broken = deepcopy(example)
        node = broken
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        assert not VALIDATOR.is_valid(broken), path
    extra = deepcopy(example)
    extra["unexpected"] = 1
    assert not VALIDATOR.is_valid(extra)
