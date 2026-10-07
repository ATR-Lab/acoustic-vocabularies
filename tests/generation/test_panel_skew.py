"""Station onset tables (#21): WAV decoding, onset detection, alignment, criteria."""

import io
import json
import struct
import wave

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation.clock import ManualClock
from av_generation.panel_demo import ScriptedPanelHost, demo_session, schedule_document
from av_generation.panel_skew import (
    Capture,
    OnsetRow,
    Schedule,
    ScheduledOnset,
    SkewInputError,
    align,
    capture_onsets,
    detect_onsets,
    logged_onsets,
    longest_run,
    main,
    onset_table,
    parse_wav,
    read_schedule,
    schedule_from_document,
    schedule_from_plays,
    summarize,
)
from av_generation.records import PlayEvent

STATIONS = ("S1", "S2", "S3")


def schedule_for(rounds=4, positions=None, placeholders=()):
    session = demo_session(rounds=rounds, positions=positions, placeholders=placeholders)
    host = ScriptedPanelHost(session, clock=ManualClock())
    host.begin(0)
    return schedule_document(host)


def burst(rate, amplitude=0.8, ms=600, freq=300.0):
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    env = np.minimum(1.0, t / 0.010) * np.minimum(1.0, (ms / 1000 - t) / 0.030)
    return amplitude * env * np.sin(2 * np.pi * freq * t)


def synthetic_capture(
    schedule,
    *,
    rate=2000,
    skew_ms=None,
    jitter_ms=0.0,
    drift_ppm=0.0,
    delay_ms=1500.0,
    noise=1e-4,
    drop=(),
    seed=1,
):
    """Three channels; capture time = (1 + drift) * (server + skew + jitter) + delay."""
    rng = np.random.default_rng(seed)
    skew_ms = skew_ms or {}
    events = [o for o in schedule_from_document(schedule).onsets]
    end_ms = (1 + drift_ppm * 1e-6) * (events[-1].scheduled_ms + 3_000) + delay_ms
    out = rng.normal(0.0, noise, size=(len(STATIONS), int(end_ms * rate / 1000)))
    truth = {}
    for c, station in enumerate(STATIONS):
        for o in events:
            if (station, o.rating_slot_id, o.role) in drop:
                continue
            server = o.scheduled_ms + skew_ms.get(
                (station, o.rating_slot_id, o.role), skew_ms.get(station, 0.0)
            )
            server += rng.uniform(-jitter_ms, jitter_ms)
            truth[(station, o.rating_slot_id, o.role)] = server
            at = (1 + drift_ppm * 1e-6) * server + delay_ms
            start = int(round(at * rate / 1000))
            b = burst(rate)
            out[c, start : start + b.size] += b
    return Capture(rate, out), truth


def wav_pcm16(samples, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(samples.shape[0])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.round(samples.T * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def riff(fmt_chunk, data, extra=b""):
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk + extra
    body += b"data" + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def fmt(tag, channels, rate, bits, extensible_tag=None):
    align_ = channels * bits // 8
    head = struct.pack("<HHIIHH", tag, channels, rate, rate * align_, align_, bits)
    if extensible_tag is None:
        return head
    guid_tail = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    return head + struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", extensible_tag) + guid_tail


# ---------------------------------------------------------------------------


def test_wav_formats_decode_to_the_same_samples():
    x = np.array([[0.0, 0.5, -0.5, 0.25], [0.125, -0.25, 0.75, -1.0]])
    pcm16 = parse_wav(wav_pcm16(x, 8000))
    assert pcm16.rate == 8000 and pcm16.samples.shape == (2, 4)
    assert np.allclose(pcm16.samples, x, atol=1 / 32767)
    i24 = np.round(x.T * (2**23 - 1)).astype(np.int64).reshape(-1)
    data24 = b"".join(int(v & 0xFFFFFF).to_bytes(3, "little") for v in i24)
    for chunk in (fmt(1, 2, 8000, 24), fmt(0xFFFE, 2, 8000, 24, extensible_tag=1)):
        decoded = parse_wav(riff(chunk, data24, extra=b"LIST\x04\x00\x00\x00abcd"))
        assert np.allclose(decoded.samples, x, atol=2**-22)
    f32 = parse_wav(riff(fmt(3, 2, 8000, 32), x.T.astype("<f4").tobytes()))
    assert np.allclose(f32.samples, x)
    f64 = parse_wav(riff(fmt(0xFFFE, 2, 8000, 64, extensible_tag=3), x.T.astype("<f8").tobytes()))
    assert np.allclose(f64.samples, x)
    u8 = parse_wav(riff(fmt(1, 1, 8000, 8), bytes([128, 192, 64])))
    assert np.allclose(u8.samples[0], [0.0, 0.5, -0.5])
    i32 = parse_wav(riff(fmt(1, 1, 8000, 32), np.array([2**30], "<i4").tobytes()))
    assert np.allclose(i32.samples[0], [0.5])
    assert pcm16.sha256 is not None and len(pcm16.sha256) == 64


@pytest.mark.parametrize(
    "data",
    [
        b"not a wav",
        b"RIFF\x04\x00\x00\x00WAVE",
        riff(fmt(2, 1, 8000, 16), b"\0\0"),  # ADPCM
        riff(fmt(3, 1, 8000, 16), b"\0\0"),  # 16-bit float
        riff(fmt(0xFFFE, 1, 8000, 16), b"\0\0"),  # short extensible header
        riff(struct.pack("<HHIIHH", 1, 2, 8000, 32000, 3, 16), b"\0" * 6),  # bad block align
    ],
)
def test_unusable_wavs_are_refused(data):
    with pytest.raises(SkewInputError):
        parse_wav(data)


@settings(max_examples=40, deadline=None)
@given(
    gaps=st.lists(st.integers(800, 3_000), min_size=1, max_size=8),
    amplitude=st.floats(0.5, 1.0),
    rate=st.sampled_from([4_000, 8_000, 48_000]),
)
def test_detected_onsets_are_within_a_millisecond(gaps, amplitude, rate):
    onsets = np.cumsum(gaps).astype(float)
    x = np.random.default_rng(0).normal(0, 1e-4, int((onsets[-1] + 1_500) * rate / 1000))
    for t in onsets:
        start = int(round(t * rate / 1000))
        b = burst(rate, amplitude=amplitude, ms=400)
        x[start : start + b.size] += b
    found = detect_onsets(x, rate)
    assert found.size == onsets.size
    assert np.all(np.abs(found - onsets) <= 1.0 + 1000 / rate)


def test_detection_edge_cases():
    assert detect_onsets([], 8000).size == 0
    assert detect_onsets(np.zeros(100), 8000).size == 0
    assert detect_onsets([0.0, np.nan], 8000).size == 0
    assert detect_onsets(np.ones(10) * 0.01, 1000, threshold=0.5).size == 0
    # an atom with inner gaps of 60 ms is one onset
    rate = 8000
    x = np.zeros(rate)
    for start_ms in (100, 260, 420):
        b = burst(rate, ms=100)
        s = start_ms * rate // 1000
        x[s : s + b.size] += b
    assert detect_onsets(x, rate).tolist() == pytest.approx([100.0], abs=1.0)


def test_alignment_recovers_delay_and_clock_rate():
    schedule = schedule_for()
    capture, _ = synthetic_capture(schedule, drift_ppm=120.0, delay_ms=2_345.0)
    detected = {s: detect_onsets(capture.samples[i], capture.rate) for i, s in enumerate(STATIONS)}
    detected["S1"] = np.concatenate([[300.0], detected["S1"]])  # a stray click before the session
    sched = [o.scheduled_ms for o in schedule_from_document(schedule).onsets]
    alignment = align(sched, detected)
    assert alignment.rate_ppm == pytest.approx(120.0, abs=5.0)
    assert alignment.offset_ms == pytest.approx(2_345.0, abs=2.0)
    assert alignment.matched == 3 * 72 and alignment.detections == 3 * 72 + 1
    assert alignment.to_server(2_345.0) == pytest.approx(0.0, abs=2.0)
    with pytest.raises(SkewInputError):
        align([], detected)


def test_loopback_capture_meets_the_skew_criterion_over_36_slots():
    schedule = schedule_for()
    skews = {"S1": 0.0, "S2": 30.0, "S3": -20.0}
    capture, truth = synthetic_capture(schedule, skew_ms=skews, jitter_ms=4.0, drift_ppm=-40.0)
    parsed = schedule_from_document(schedule)
    onsets, alignment = capture_onsets(capture, parsed, {s: i for i, s in enumerate(STATIONS)})
    rows = onset_table(parsed, onsets, stations=STATIONS)
    assert len(rows) == 72 and all(r.complete for r in rows)
    for r in rows:
        pairwise = [truth[(s, r.rating_slot_id, r.role)] for s in STATIONS]
        assert r.skew_ms == pytest.approx(max(pairwise) - min(pairwise), abs=2.0)
    summary = summarize(
        rows,
        parsed,
        source="loopback",
        stations=STATIONS,
        alignment=alignment,
        capture_sha256="0" * 64,
    )
    assert summary["pass_skew_36_slots"] is True and summary["n_slots"] == 36
    assert 40 <= summary["max_skew_ms"] <= 62
    assert summary["alignment"]["rate_ppm"] == pytest.approx(-40.0, abs=5.0)
    for station in STATIONS:
        assert summary["by_station"][station]["detected"] == 72
        assert summary["by_station"][station]["max_reference_interval_error_ms"] <= 10.0


def test_a_late_station_or_a_missing_onset_fails_the_criterion():
    schedule = schedule_for()
    parsed = schedule_from_document(schedule)
    late = parsed.onsets[40]
    capture, _ = synthetic_capture(
        schedule,
        skew_ms={("S3", late.rating_slot_id, late.role): 150.0},
        drop={("S2", parsed.onsets[10].rating_slot_id, parsed.onsets[10].role)},
    )
    onsets, alignment = capture_onsets(capture, parsed, {s: i for i, s in enumerate(STATIONS)})
    rows = onset_table(parsed, onsets, stations=STATIONS)
    summary = summarize(rows, parsed, source="loopback", stations=STATIONS, alignment=alignment)
    assert summary["pass_skew_36_slots"] is False
    assert summary["complete_rows"] == 71 and summary["max_skew_ms"] == pytest.approx(150, abs=2)
    assert summary["longest_slot_run_within_skew"] < 36
    with pytest.raises(SkewInputError):
        capture_onsets(capture, parsed, {"S4": 3})


def test_longest_run_counts_placeholders_and_respects_slot_limits():
    rows = [
        OnsetRow(1, 0, "a", "candidate", 0, {"S1": 0.0, "S2": 10.0}),
        OnsetRow(2, 2, "c", "candidate", 40_000, {"S1": 40_000.0, "S2": 40_200.0}),
        OnsetRow(3, 3, "d", "candidate", 60_000, {"S1": 60_000.0, "S2": None}),
    ]
    assert rows[0].within_skew and rows[0].within_onset
    assert not rows[1].within_skew and rows[1].skew_ms == 200.0 and not rows[1].within_onset
    assert not rows[2].complete and rows[2].skew_ms is None and rows[2].max_abs_dev_ms == 0.0
    assert longest_run(rows, 5, attr="within_skew") == 2  # slot 0 and placeholder slot 1
    schedule = Schedule(
        "DEMO-x",
        ("S1", "S2"),
        ("a", "b", "c", "d"),
        (ScheduledOnset(0, "a", "candidate", 0), ScheduledOnset(3, "d", "candidate", 60_000)),
    )
    table = onset_table(schedule, {"S1": {("a", "candidate"): 3.0}}, max_slots=2)
    assert [(r.rating_slot_id, r.onsets) for r in table] == [("a", {"S1": 3.0, "S2": None})]
    summary = summarize(table, schedule, source="logged", stations=("S1", "S2"), max_slots=2)
    assert summary["n_slots"] == 2 and summary["max_skew_ms"] is None
    assert summary["pass_onset_all_rows"] is False


def _play(station, slot, role, scheduled, onset, result="played"):
    return PlayEvent(
        run_id="DEMO-panel-01",
        context="rating_candidate" if role == "candidate" else "rating_reference",
        audio_kind="atom",
        asset_id="a" * 64,
        result=result,
        t_ms=onset + 5,
        reason=None if result == "played" else "E_TOKEN_USED",
        station=station,
        actor_id="R01",
        rating_slot_id=slot,
        scheduled_ms=scheduled,
        onset_ms=onset if result == "played" else None,
    ).check()


def test_logged_onsets_and_schedule_from_plays():
    slot = "DEMO-A-P01.K-a1.r1p1"
    plays = [
        _play("S1", slot, "candidate", 5_000, 5_004),
        _play("S1", slot, "candidate", 5_000, 5_900),  # a second report never replaces the first
        _play("S2", slot, "candidate", 5_000, 5_030),
        _play("S1", slot, "reference", 7_000, 7_002),
        _play("S2", slot, "reference", 7_000, 7_001, result="refused"),
    ]
    onsets = logged_onsets(plays)
    assert onsets == {
        "S1": {(slot, "candidate"): 5_004.0, (slot, "reference"): 7_002.0},
        "S2": {(slot, "candidate"): 5_030.0},
    }
    schedule = schedule_from_plays(plays, "DEMO-panel-01")
    assert schedule.stations == ("S1", "S2") and schedule.slot_ids == (slot,)
    assert [(o.role, o.scheduled_ms) for o in schedule.onsets] == [
        ("candidate", 5_000),
        ("reference", 7_000),
    ]
    rows = onset_table(schedule, onsets)
    assert rows[0].skew_ms == 26.0 and rows[0].within_onset and rows[0].within_skew
    assert not rows[1].complete


def test_schedule_documents_are_checked(tmp_path):
    with pytest.raises(SkewInputError):
        schedule_from_document({"format": "something-else"})
    doc = schedule_for(rounds=1, positions=[1, 2], placeholders=[(1, 2)])
    path = tmp_path / "panel-schedule.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    schedule = read_schedule(path)
    assert schedule.sha256 is not None and schedule.slot_ids[1].endswith("r1p2")
    assert [o.role for o in schedule.onsets] == ["candidate", "reference"]


def test_loopback_command_writes_table_and_summary(tmp_path, capsys):
    doc = schedule_for(rounds=1, positions=[1, 2, 3])
    (tmp_path / "panel-schedule.json").write_text(json.dumps(doc), encoding="utf-8")
    capture, _ = synthetic_capture(doc, skew_ms={"S2": 12.0}, rate=4000)
    (tmp_path / "capture.wav").write_bytes(wav_pcm16(capture.samples, capture.rate))
    code = main(
        [
            "loopback",
            "--capture",
            str(tmp_path / "capture.wav"),
            "--channels",
            "S1,S2,S3",
            "--schedule",
            str(tmp_path / "panel-schedule.json"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == 0
    summary = json.loads((tmp_path / "o" / "loopback-skew-summary.json").read_text("utf-8"))
    assert summary["source"] == "loopback" and summary["complete_rows"] == 6
    assert summary["max_skew_ms"] == pytest.approx(12.0, abs=1.5)
    assert summary["pass_skew_36_slots"] is False  # only 3 slots recorded
    assert summary["capture_sha256"] is not None and "linear fit" in summary["note"]
    header = (tmp_path / "o" / "loopback-skew.csv").read_text("utf-8").splitlines()[0]
    assert header == (
        "index,rating_slot_id,role,scheduled_ms,skew_ms,max_abs_dev_ms,within_skew,"
        "within_onset,onset_ms_S1,onset_ms_S2,onset_ms_S3"
    )
    assert "max skew" in capsys.readouterr().out
