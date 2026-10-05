"""Probe contract tests; fake Cartesian IK is not robot/contact evidence."""
from copy import deepcopy
import math

import pytest

from isaac.demos.grip_probe import (CLOSED, OPEN, FINGER_NAMES, compile_grip_plan,
    cylinder_distance, hand_positions)
from isaac.demos.runtime import DemoLibrary
from isaac.workcell.layout import neutral_layout
from test_demos import FakeBackend, FakeAccessors


def backend():
    value = FakeBackend()
    for index, suffix in enumerate((*FINGER_NAMES, 'index_0', 'index_1'), 19):
        value.names[index] = 'right_hand_'+suffix+'_joint'
    return value


def test_outer_cylinder_distance_faces_and_interior():
    assert cylinder_distance([.0125, 0, 0]) == 0
    assert cylinder_distance([0, 0, .003]) == 0
    assert cylinder_distance([.0135, 0, 0]) == pytest.approx(.001)
    assert cylinder_distance([0, 0, 0]) == pytest.approx(-.003)


@pytest.mark.parametrize('closure', [-1., 1.1, math.nan, math.inf])
def test_closure_rejects_nonfinite_or_outside_range(closure):
    value = backend()
    with pytest.raises(ValueError):
        hand_positions(value, value.positions(), closure)


def test_finger_candidate_changes_only_selected_hand_and_preserves_input():
    value = backend()
    original = value.positions()
    result = hand_positions(value, original, 1.)
    assert result[19:24] == list(CLOSED)
    assert hand_positions(value, original, 0.)[19:24] == list(OPEN)
    assert result[:19] == original[:19]
    assert result[26:] == original[26:]
    assert original == value.positions()


def test_probe_restores_prop_and_explicitly_excludes_complete_action_claim():
    layout, value = neutral_layout(), backend()
    accessors = FakeAccessors(layout)
    neutral = deepcopy(accessors.state)
    plan = compile_grip_plan(value, layout, neutral)
    assert plan['diagnostic_stage'] == 'pickup_and_replace_only'
    assert plan['expected'] == neutral
    assert plan['grasp_contact_validated'] is False
    assert plan['collision_reviewed'] is False
    assert plan['attachments'][0]['start'] > .20
    assert plan['release'] < .70  # Detach before opening, not after moving away.
    library = DemoLibrary(value, accessors, neutral, {('ADD_ONE', 'tray_A'): plan})
    assert len(list(library('ADD_ONE', 'tray_A'))) == 300
    assert library.last_result['execution_ok'] is True
    assert accessors.state[plan['primary']]['state']['location'] == 'supply_cup'


def test_probe_rejects_unreviewed_washer_geometry():
    layout, value = neutral_layout(), backend()
    accessors = FakeAccessors(layout)
    for item in layout['objects']:
        if item['kind'] == 'washer':
            item['dimensions_m'] = [.035, .035, .006]
    with pytest.raises(ValueError, match='25x25x6mm'):
        compile_grip_plan(value, layout, accessors.state)
