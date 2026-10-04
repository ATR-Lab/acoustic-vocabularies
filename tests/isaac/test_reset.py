"""Synthetic coverage only; actual Isaac cycles have a separate evidence file."""
import copy
import hashlib
import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from isaac.reset import ResetManager, capture, load_snapshot, snapshot_bytes
from isaac.reset.snapshot import sha256
from isaac.reset.event_log import DurableResetLog


def fixture():
    pose = {"position_m": [0., 0., 0.], "rotation_xyzw": [0., 0., 0., 1.]}
    return {"robot": {"joint_names": [f"engineering_joint_{i}" for i in range(43)],
                      "joint_positions_rad": [0.]*43, "joint_velocities_rad_s": [0.]*43,
                      "root_position_m": [0., 0., 0.], "root_rotation_xyzw": [0., 0., 0., 1.],
                      "root_linear_velocity_m_s": [0.]*3, "root_angular_velocity_rad_s": [0.]*3},
            "objects": {f"engineering_object_{i}": {**copy.deepcopy(pose), "visible": True,
                        "enabled": True, "collision_enabled": False, "linear_velocity_m_s": [0.]*3,
                        "angular_velocity_rad_s": [0.]*3, "state": {"card_face": 0, "arrow_angle_rad": 0.,
                        "lid_open_fraction": 0., "tag_attached": True, "location": "fixture_anchor"}} for i in range(8)},
            "environment": {"materials": {"fixture_material": {"color_rgb": [.2, .3, .4]}},
                            "lights": {"fixture_light": {"intensity": 500., "visible": True}}},
            "frames": {n: copy.deepcopy(pose) for n in ("head", "left_hand", "right_hand")}}


class FakeAdapter:
    scene_sha256 = "a"*64
    sim_time = 0.
    def __init__(self):
        self.state = fixture()
        self.writes = 0
        self.locked = False
    def read_state(self):
        return copy.deepcopy(self.state)
    def write_state(self, state):
        self.writes += 1
        self.state = copy.deepcopy(state)
        if self.locked:
            self.state["robot"]["joint_positions_rad"][0] = .5
    def step_fixed(self, count):
        self.sim_time += count/240


def manager():
    adapter = FakeAdapter()
    snapshot = capture(adapter)
    events = []
    return adapter, snapshot, events, ResetManager(adapter, snapshot, sha256(snapshot), events.append)


def perturb(adapter, rng):
    # Every joint and every object, including flags/discrete/velocities, changes.
    for key in ("joint_positions_rad", "joint_velocities_rad_s"):
        adapter.state["robot"][key] = [rng.uniform(-1, 1) for _ in range(43)]
    for obj in adapter.state["objects"].values():
        obj["position_m"] = [rng.uniform(-1, 1) for _ in range(3)]
        obj["linear_velocity_m_s"] = [1.]*3
        obj["angular_velocity_rad_s"] = [1.]*3
        obj["rotation_xyzw"] = [0., 0., 1., 0.]
        obj["visible"] = obj["enabled"] = False
        obj["collision_enabled"] = True
        obj["state"].update(card_face=1, arrow_angle_rad=1., lid_open_fraction=1., tag_attached=False, location="other_anchor")
    adapter.state["environment"]["lights"]["fixture_light"]["intensity"] = 100.
    adapter.state["environment"]["materials"]["fixture_material"]["color_rgb"] = [1., 1., 1.]
    for frame in adapter.state["frames"].values():
        frame["position_m"] = [1.]*3


def test_1000_all_state_perturb_resets():
    adapter, snapshot, events, reset = manager()
    rng = random.Random(531000)  # Public engineering fixture, never study randomization.
    for _ in range(1000):
        perturb(adapter, rng)
        result = reset.reset()
        assert result["reset_ok"] and reset.exposure_ready
        assert adapter.state == snapshot["state"]
    assert len(events) == adapter.writes == 1000


def test_locked_joint_fault_latches_until_explicit_success():
    adapter, _, events, reset = manager()
    adapter.locked = True
    assert not reset.reset()["reset_ok"]
    assert len(events) == 1 and not reset.exposure_ready and adapter.writes == 1
    adapter.locked = False
    adapter.state = fixture()
    assert reset.verify_current()["reset_ok"] and not reset.exposure_ready
    assert reset.reset()["reset_ok"] and reset.exposure_ready


def test_snapshot_hash_and_scene_mismatch_before_any_write(tmp_path):
    adapter, snapshot, _, reset = manager()
    raw = snapshot_bytes(snapshot)
    path = tmp_path/"neutral.json"
    path.write_bytes(raw)
    assert load_snapshot(path, hashlib.sha256(raw).hexdigest()) == snapshot
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="mismatch"):
        load_snapshot(path, hashlib.sha256(raw).hexdigest())
    adapter.scene_sha256 = "b"*64
    assert not reset.reset()["reset_ok"] and adapter.writes == 0


@pytest.mark.parametrize("mutation", [
    lambda s: s["objects"].pop("engineering_object_0"),
    lambda s: s["objects"]["engineering_object_0"]["state"].update(card_face=1),
    lambda s: s["robot"]["joint_names"].reverse(),
    lambda s: s["robot"]["joint_positions_rad"].__setitem__(0, float("nan")),
    lambda s: s["environment"]["materials"].clear(),
    lambda s: s["frames"]["head"]["position_m"].__setitem__(0, .002),
    lambda s: s["robot"]["root_linear_velocity_m_s"].__setitem__(0, .01),
])
def test_readback_rejects_incomplete_or_changed_state(mutation):
    adapter, _, _, reset = manager()
    reset.reset()
    mutation(adapter.state)
    assert not reset.verify_current()["reset_ok"] and not reset.exposure_ready


def test_quaternion_sign_equivalence_and_boundary():
    adapter, _, _, reset = manager()
    adapter.state["robot"]["root_rotation_xyzw"] = [0., 0., 0., -1.]
    assert reset.verify_current()["reset_ok"]
    adapter.state["objects"]["engineering_object_0"]["position_m"][0] = .001
    assert reset.verify_current()["reset_ok"]
    adapter.state["objects"]["engineering_object_0"]["position_m"][0] = .00101
    assert not reset.verify_current()["reset_ok"]


def test_synthetic_demo_sequence_then_reset():
    adapter, snapshot, _, reset = manager()
    for i in range(32):
        perturb(adapter, random.Random(i))
    assert reset.reset()["reset_ok"]
    assert adapter.state == snapshot["state"]


def test_durable_log_and_failure_never_open_gate(tmp_path):
    adapter, snapshot, _, _ = manager()
    path = tmp_path/"reset.jsonl"
    log = DurableResetLog(path, session_id="0"*32, apparatus_version="engineering-fixture", protocol_version="unresolved-methodology")
    reset = ResetManager(adapter, snapshot, sha256(snapshot), log)
    reset.reset()
    adapter.locked = True
    reset.reset()
    log.close()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [x["event_type"] for x in events] == ["neutral_reset", "reset_fault"]
    assert [x["event_seq"] for x in events] == [0, 1]
    with pytest.raises(FileExistsError):
        DurableResetLog(path, session_id="0"*32, apparatus_version="fixture", protocol_version="unknown")
    adapter.locked = False
    with pytest.raises(ValueError):
        reset.reset()  # closed sink fails, no successful acknowledgement
    assert not reset.exposure_ready


def test_capture_rejects_nonzero_velocity_and_caller_mutation():
    adapter, snapshot, _, reset = manager()
    snapshot["state"]["robot"]["joint_positions_rad"][0] = 1.
    assert reset.reset()["reset_ok"]
    adapter.state["robot"]["joint_velocities_rad_s"][0] = 1e-8
    with pytest.raises(ValueError, match="zero"):
        capture(adapter)
