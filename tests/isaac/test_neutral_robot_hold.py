"""Synthetic regression coverage for the detached per-step robot hold target."""
import copy
import threading

import pytest

from test_command_lock import setup
from test_reset import manager


def test_robot_target_matches_full_copy_and_is_deeply_detached():
    adapter, snapshot, events, reset = manager()
    expected = copy.deepcopy(snapshot["state"])
    first = reset.neutral_robot_state
    assert first == reset.neutral_state["robot"] == expected["robot"]
    # Mutate every nested list, the mapping, constructor input and old full getter.
    for value in first.values():
        value.clear()
    first.clear()
    snapshot["state"]["robot"]["joint_names"].clear()
    entire = reset.neutral_state
    entire["robot"]["joint_positions_rad"][0] = 99.
    entire["objects"].clear()
    assert reset.neutral_robot_state == expected["robot"]
    assert reset.neutral_state == expected
    assert not events and adapter.writes == 0 and not reset.exposure_ready
    assert reset.reset()["reset_ok"]
    assert adapter.state == expected  # Full restore still includes every field.


@pytest.mark.parametrize("mode,paused,stopped,neutral,expected,verify", [
    ("test", False, False, True, "neutral", True),
    ("test", True, False, False, "neutral", True),
    ("teaching", False, True, False, "neutral", True),
    ("teaching", True, True, False, "neutral", True),
    ("teaching", False, False, True, "neutral", True),
    ("post_endpoint", True, False, True, "neutral", True),
    ("teaching", True, False, False, "paused", False),
    ("post_endpoint", True, False, False, "paused", False),
    ("teaching", False, False, False, None, False),
    ("post_endpoint", False, False, False, None, False),
])
def test_hold_branches_keep_exact_payload_and_full_verification(
        mode, paused, stopped, neutral, expected, verify):
    adapter, snapshot, reset, dispatcher, _, _ = setup()
    dispatcher.mode, dispatcher.paused = mode, paused
    dispatcher.stopped, dispatcher.neutral_hold = stopped, neutral
    dispatcher.paused_robot = copy.deepcopy(snapshot["state"]["robot"])
    dispatcher.paused_robot["joint_positions_rad"][0] = .2
    held, verifications = [], []
    original_verify = reset.verify_current
    def observe_verify(**kwargs):
        verifications.append(kwargs)
        return original_verify(**kwargs)
    reset.verify_current = observe_verify
    def hold(value):
        held.append(copy.deepcopy(value))
        adapter.state["robot"] = copy.deepcopy(value)
        if expected == "neutral":
            value["joint_positions_rad"][0] = 99.
    dispatcher.hold_robot = hold
    assert dispatcher.after_physics_step()
    target = snapshot["state"]["robot"] if expected == "neutral" else dispatcher.paused_robot
    assert held == ([] if expected is None else [target])
    assert len(verifications) == int(verify)
    assert reset.neutral_robot_state == snapshot["state"]["robot"]


@pytest.mark.parametrize("failure,code", [
    ("missing_hold", "HOLD_NOT_CONFIGURED"),
    ("hold_exception", "HOLD_FAILED"),
    ("read_exception", "NEUTRAL_DIVERGED"),
    ("object_drift", "NEUTRAL_DIVERGED"),
    ("frame_drift", "NEUTRAL_DIVERGED"),
    ("environment_drift", "NEUTRAL_DIVERGED"),
])
def test_failures_remain_latched_without_restoring_nonrobot_state(failure, code):
    adapter, snapshot, reset, dispatcher, _, _ = setup()
    if failure == "missing_hold":
        dispatcher.hold_robot = None
    elif failure == "hold_exception":
        def fail(_):
            raise RuntimeError("fixture hold failure")
        dispatcher.hold_robot = fail
    elif failure == "read_exception":
        def fail():
            raise RuntimeError("fixture read failure")
        adapter.read_state = fail
    elif failure == "object_drift":
        adapter.state["objects"]["engineering_object_0"]["state"]["card_face"] = 1
    elif failure == "frame_drift":
        adapter.state["frames"]["head"]["position_m"][0] = .01
    else:
        adapter.state["environment"]["lights"]["fixture_light"]["intensity"] = 12.
    nonrobot = {k: copy.deepcopy(v) for k, v in adapter.state.items() if k != "robot"}
    assert not dispatcher.after_physics_step()
    assert dispatcher.fault == code
    assert {k: v for k, v in adapter.state.items() if k != "robot"} == nonrobot
    assert reset.neutral_state == snapshot["state"]


def test_hold_keeps_capture_and_owner_thread_contract():
    _, _, reset, dispatcher, _, _ = setup()
    calls = []
    class Capture:
        manager = reset
        def before_read(self):
            calls.append("before")
        def after_read(self, state, result):
            calls.append(("after", result["reset_ok"], set(state)))
    assert dispatcher.after_physics_step(capture=Capture())
    assert calls == ["before", ("after", True, {"robot", "objects", "environment", "frames"})]
    failures = []
    def wrong_thread():
        try:
            dispatcher.after_physics_step(capture=Capture())
        except RuntimeError as error:
            failures.append(str(error))
    worker = threading.Thread(target=wrong_thread)
    worker.start()
    worker.join()
    assert failures == ["commands must dispatch on the simulation thread"]
    assert len(calls) == 2


def test_offline_benchmark_binds_snapshot_and_keeps_gc_policy(tmp_path):
    import gc
    from isaac.reset.neutral_copy_benchmark import benchmark
    from isaac.reset.snapshot import sha256, snapshot_bytes
    _, snapshot, _, _ = manager()
    path = tmp_path / "neutral.json"
    path.write_bytes(snapshot_bytes(snapshot))
    policy = gc.isenabled(), gc.get_threshold()
    report = benchmark(path, sha256(snapshot), iterations=2, cycles=1)
    assert (gc.isenabled(), gc.get_threshold()) == policy
    assert report["payloads_equal"]
    assert report["native_run"] is report["timing_qualified"] is report["participant_admission"] is False
    assert {key: row["count"] for key, row in report["copy_ns"].items()} == {
        "full_state_then_robot": 4, "robot_only": 4}
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="snapshot SHA-256 mismatch"):
        benchmark(path, sha256(snapshot), iterations=2, cycles=1)
