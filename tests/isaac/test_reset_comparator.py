"""Synthetic comparator behavior and object-lifetime regression checks."""
import copy
import weakref

import pytest

from test_reset import manager


def test_neutral_verification_does_not_retain_its_manager_until_gc():
    adapter, snapshot, events, reset = manager()
    reference = weakref.ref(reset)
    assert reset.verify_current()["reset_ok"]
    del reset
    # No forced collection or GC setting change: this read-only operation must
    # not create a cycle that retains the manager and its simulator adapter.
    assert reference() is None


def test_multiple_deviations_keep_failure_order_and_complete_worst_values():
    adapter, snapshot, events, reset = manager()
    state = adapter.state
    state["robot"]["joint_positions_rad"][0] = .02
    obj = state["objects"]["engineering_object_0"]
    obj["visible"] = False
    obj["linear_velocity_m_s"][0] = .00002
    obj["state"]["card_face"] = 1
    state["environment"]["materials"]["fixture_material"]["color_rgb"][0] += .001
    state["frames"]["head"]["position_m"][0] = .002
    before = copy.deepcopy(state)
    result = reset.verify_current()
    assert not result["reset_ok"]
    assert adapter.state == before
    assert [(row["item"], row["reason"]) for row in result["failures"]] == [
        ("state/robot/joint_positions_rad", "tolerance"),
        ("state/objects/engineering_object_0/visible", "exact_state"),
        ("state/objects/engineering_object_0/linear_velocity_m_s", "tolerance"),
        ("state/objects/engineering_object_0/state/card_face", "exact_state"),
        ("state/environment/materials/fixture_material/color_rgb", "tolerance"),
        ("state/frames/head/position_m", "tolerance"),
    ]
    assert result["worst_deviation"] == pytest.approx({
        "joint_rad": .02, "position_m": .002, "orientation_rad": 0.,
        "linear_velocity_m_s": .00002, "angular_velocity_rad_s": 0.,
        "environment_absolute": .001,
    })
