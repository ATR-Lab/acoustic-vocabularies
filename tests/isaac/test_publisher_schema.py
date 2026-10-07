import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from isaac.publisher.protocol import PublicRegistry, StateEncoder, strict_loads, validate_frame
from isaac.publisher.runtime import StatePublisher
from isaac.publisher.transport import WebSocketTransport

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests/fixtures/publisher-state.json").read_text())
SCHEMA = json.loads((ROOT / "isaac/publisher/state-v2.schema.json").read_text())


def registry():
    return PublicRegistry(FIXTURE["station_id"], FIXTURE["scene_sha256"],
        FIXTURE["reset_snapshot_sha256"], tuple(FIXTURE["joint_names"]), (("synthetic_card", ("card_face",)),))


def state():
    obj = copy.deepcopy(FIXTURE["objects"][0])
    obj.pop("id")
    # These simulator-only fields must never be accidentally copied to the stream.
    obj.update(linear_velocity_m_s=[0, 0, 0], collision_enabled=True, private_note="synthetic-secret")
    return [0.0]*43, {"synthetic_card": obj}, {"synthetic_neutral": True}


def test_public_projection_schema_and_identity_are_strict():
    positions, objects, _ = state()
    frame = StateEncoder(registry(), "synthetic", lambda: 123).build(positions, objects, 0.1, 6)
    Draft202012Validator(SCHEMA).validate(frame)
    assert validate_frame(frame, registry()) is frame
    assert "synthetic-secret" not in json.dumps(frame)
    objects["synthetic_card"]["position_m"][0] = 100
    assert frame["objects"][0]["position_m"][0] == 0
    wrong = copy.deepcopy(frame)
    wrong["station_id"] = "station-99"
    with pytest.raises(ValueError):
        validate_frame(wrong, registry())


@pytest.mark.parametrize("field", ["trial_id", "target", "condition", "schedule", "command", "correctness", "scan_result"])
@pytest.mark.parametrize("level", ["envelope", "object", "visual"])
def test_answer_fields_rejected_at_every_wire_level(field, level):
    frame = copy.deepcopy(FIXTURE)
    dest = frame if level == "envelope" else frame["objects"][0] if level == "object" else frame["objects"][0]["state"]
    dest[field] = "synthetic-leak"
    assert list(Draft202012Validator(SCHEMA).iter_errors(frame))
    with pytest.raises(ValueError):
        validate_frame(frame, registry())


@pytest.mark.parametrize("mutation", [
    lambda f: f["joint_positions"].__setitem__(0, math.nan),
    lambda f: f["joint_positions"].__setitem__(0, True),
    lambda f: f["joint_names"].reverse(),
    lambda f: f["objects"].append(copy.deepcopy(f["objects"][0])),
    lambda f: f["objects"][0].__setitem__("rotation_xyzw", [0, 0, 0, 0]),
    lambda f: f["objects"][0]["state"].__setitem__("card_face", True),
    lambda f: f.__setitem__("version", True),
    lambda f: f.__setitem__("host_monotonic_ns", "1e9"),
    lambda f: f.__setitem__("session_id", "z"*32),
])
def test_runtime_rejects_nonfinite_wrong_order_and_invalid_values(mutation):
    frame = copy.deepcopy(FIXTURE)
    mutation(frame)
    with pytest.raises(ValueError):
        validate_frame(frame, registry())


def test_strict_json_duplicate_and_nonfinite_rejection():
    for raw in ('{"kind":"echo","kind":"demo"}', '{"c0_s":NaN}'):
        with pytest.raises(ValueError):
            strict_loads(raw)


class Sink:
    def __init__(self):
        self.frames = []
    def submit(self, payload):
        self.frames.append(json.loads(payload))
    def metrics(self):
        return {"connected_clients": 1, "queue_overwrites": 0, "failed": False}
    def close(self):
        pass


def test_static_scene_keeps_publishing_but_stalled_simulation_latches(tmp_path):
    now = [0]
    sink = Sink()
    publisher = StatePublisher(registry(), state, sink, tmp_path / "rate.csv", source_kind="synthetic", clock_ns=lambda: now[0])
    try:
        for step in range(60):
            now[0] = step*1_000_000_000//30
            assert publisher.after_step(step/30, step)
        assert len(sink.frames) == 60
        assert [f["seq"] for f in sink.frames] == list(range(60))
        now[0] += 40_000_000
        assert publisher.after_step(59/30, 59) is None
        assert publisher.fault == "SIMULATION_NOT_PROGRESSING"
        assert publisher.after_step(2.1, 63) is None
    finally:
        publisher.close()


def test_late_tick_does_not_fabricate_backfill(tmp_path):
    now = [0]
    sink = Sink()
    publisher = StatePublisher(registry(), state, sink, tmp_path / "late.csv", source_kind="synthetic", clock_ns=lambda: now[0])
    try:
        publisher.after_step(0, 0)
        now[0] = 1_000_000_000
        publisher.after_step(1, 60)
        assert len(sink.frames) == 2
        assert publisher.missed == 29
        assert publisher.health()["missed_deadlines"] == 29
    finally:
        publisher.close()


def test_failed_neutral_verification_cannot_leak_or_auto_resume(tmp_path):
    check = {"reset_ok": False, "failures": ["synthetic-position-drift"]}
    sink = Sink()
    publisher = StatePublisher(registry(), state, sink, tmp_path / "protected.csv", neutral_check=lambda _: check, source_kind="synthetic")
    try:
        publisher.require_neutral(True)
        assert publisher.after_step(0, 0) is None
        assert sink.frames == []
        assert publisher.health()["fault"] == "NEUTRAL_DIVERGED"
        check["reset_ok"] = True
        assert publisher.after_step(1, 60) is None
    finally:
        publisher.close()


def test_no_guard_cannot_claim_protection(tmp_path):
    publisher = StatePublisher(registry(), state, Sink(), tmp_path / "unguarded.csv", source_kind="synthetic")
    try:
        with pytest.raises(RuntimeError):
            publisher.require_neutral(True)
    finally:
        publisher.close()


@pytest.mark.parametrize("args", [{}, {"socket_path":"unused", "host":"127.0.0.1"}, {"host":"127.0.0.1"}, {"host":"127.0.0.1", "port":80}])
def test_listener_needs_one_explicit_valid_endpoint(args):
    with pytest.raises(ValueError):
        WebSocketTransport(**args)
