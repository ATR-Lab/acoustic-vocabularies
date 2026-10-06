import csv

import pytest

from isaac.publisher.analyze import analyze


def write_run(path, seconds, rate=30, gap=0):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["seq", "host_monotonic_ns", "sim_step", "queue_overwrites", "missed_deadlines"])
        for i in range(seconds*rate):
            writer.writerow([i, i*1_000_000_000//rate + (gap if i >= 15 else 0), i*2, 0, 0])
    return dict(rate_hz=rate, start_host_ns="0", end_host_ns=str(seconds*1_000_000_000+gap), completed=True,
                source_kind="live", schema_validated_frames=seconds*rate, fault=None)


def test_short_capture_never_claims_hour_acceptance(tmp_path):
    path = tmp_path/"short.csv"
    metadata = write_run(path, 2)
    result = analyze(path, metadata, required_seconds=2)
    assert result["complete"] and result["timing_screen"]
    assert not result["rate_screen"]


def test_real_duration_and_source_required_even_for_clean_timing(tmp_path):
    path = tmp_path/"synthetic-hour.csv"
    metadata = write_run(path, 3600)
    assert analyze(path, metadata)["rate_screen"]
    metadata["source_kind"] = "synthetic"
    assert not analyze(path, metadata)["rate_screen"]
    metadata["source_kind"] = "live"
    metadata["completed"] = False
    assert not analyze(path, metadata)["rate_screen"]


def test_stalls_and_unvalidated_messages_fail(tmp_path):
    path = tmp_path/"gap.csv"
    metadata = write_run(path, 2, gap=300_000_000)
    result = analyze(path, metadata, required_seconds=2)
    assert result["gaps_over_250_ms"] == 1
    assert not result["timing_screen"]
    metadata["end_host_ns"] = "1"
    with pytest.raises(ValueError):
        analyze(path, metadata)
