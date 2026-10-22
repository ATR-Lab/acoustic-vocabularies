"""Supply-cup grasp plan clearance tests.

These use the frozen #52 cup-shell boxes and a declared hand envelope with
synthetic Cartesian IK. They prove the *planned* path keeps the declared
margin; they are not Isaac link-pose, STL-triangle or whole-robot evidence.
"""
from copy import deepcopy
import functools
import hashlib
import math
import struct

import pytest

from isaac.demos import grip_geometry as gg
from isaac.demos.geometry import angle, axis_angle, compose, mul, pose, rotate
from isaac.demos.planner import TRANSPORT_YAW_STEP_RAD, compile_plan
from isaac.demos.runtime import DemoLibrary, SAMPLE_COUNT
from isaac.workcell.layout import neutral_layout
from test_demos import FakeAccessors, FakeBackend

TRAYS = ('tray_A', 'tray_B', 'tray_C', 'tray_D')


def planned(target, layout=None):
    layout = layout or neutral_layout()
    backend, accessors = FakeBackend(), FakeAccessors(layout)
    neutral = deepcopy(accessors.state)
    plan = compile_plan(backend, layout, neutral, 'ADD_ONE', target)
    return layout, backend, accessors, neutral, plan


@functools.lru_cache(maxsize=None)
def run_and_measure(target):
    """Play the plan through the real runtime; measure every one of 300 samples (read-only)."""
    layout, backend, accessors, neutral, plan = planned(target)
    corridor = plan['cup_corridor']
    library = DemoLibrary(backend, accessors, neutral, {('ADD_ONE', target): plan})
    samples, postures = [], []
    for index, _ in enumerate(library('ADD_ONE', target)):
        u = index/(SAMPLE_COUNT-1)
        attached = plan['attachments'][0]['start'] <= u <= plan['release']
        samples.append(dict(sample=index, u=u, palm=backend.palm('right'),
                            carried=pose(accessors.state[plan['primary']]) if attached else None))
        postures.append(backend.positions())
    assert library.last_result['execution_ok']
    return layout, plan, corridor, samples, postures


def tip_world(corridor, palm):
    return compose(palm, (corridor['tip_local_m'], [0., 0., 0., 1.]))[0]


def test_signed_box_distance_and_segment_minimum():
    center, size = [0., 0., 0.], [.004, .1, .1]
    assert gg.point_box_signed_distance([0, 0, 0], center, size) == pytest.approx(-.002)
    assert gg.point_box_signed_distance([.005, 0, 0], center, size) == pytest.approx(.003)
    assert gg.point_box_signed_distance([.005, .054, 0], center, size) == pytest.approx(math.hypot(.003, .004))
    a, b = [.03, -.2, .01], [-.01, .2, -.02]
    brute = min(gg.point_box_signed_distance([x+(y-x)*k/20000 for x, y in zip(a, b)], center, size)
                for k in range(20001))
    assert gg.segment_box_distance(a, b, center, size) == pytest.approx(brute, abs=2e-6)
    assert gg.segment_box_distance(a, b, center, size) <= brute+1e-12
    with pytest.raises(ValueError):
        gg.point_box_signed_distance([math.nan, 0, 0], center, size)


def test_wall_boxes_are_the_frozen_shell():
    walls = {name: (center, size) for name, center, size in gg.cup_wall_boxes(neutral_layout())}
    assert set(walls) == {'Bottom', 'Back', 'Front', 'Left', 'Right'}
    assert walls['Front'][0][0]-walls['Front'][1][0]/2 == pytest.approx(.074)   # inner face
    assert walls['Right'][0][1]-walls['Right'][1][1]/2 == pytest.approx(-.376)
    assert walls['Bottom'][0][2]+walls['Bottom'][1][2]/2 == pytest.approx(.839)


def test_declared_envelope_comes_from_the_pinned_urdf_chain():
    assert gg.TIP_LOCAL[0] == pytest.approx(.0777+.0458+.0470886118710041)
    assert gg.TIP_LOCAL[1:] == (-.0016, -.0285)
    # URDF limits: middle 0..pi/2 and 0..1.745, index likewise, thumb_2 -1.745..0.
    assert 0 <= gg.PAD_POSTURE['index_0'] <= math.pi/2 and 0 <= gg.PAD_POSTURE['index_1'] <= 1.74532925
    assert gg.PAD_POSTURE['middle_0'] == gg.PAD_POSTURE['middle_1'] == 0.
    # The hand-body bound lies beyond the middle/index knuckles (x=0.0777) and
    # short of the fingertip, so only the straight distal column extends below it.
    assert .0777 < gg.BODY_LIMIT_PALM_X_M < gg.TIP_LOCAL[0]
    assert gg.HAND_REACH_M >= math.sqrt(sum(v*v for v in gg.TIP_LOCAL))+gg.FINGER_RADIUS_M


@pytest.mark.parametrize('target', TRAYS)
def test_add_one_uses_the_supply_cup_corridor_not_the_pinch(target):
    layout, backend, _, neutral, plan = planned(target)
    corridor = plan['cup_corridor']
    assert plan['pickup_kind'] == 'supply_cup_vertical_fingertip'
    assert plan['grasp_contact_validated'] is False and plan['collision_reviewed'] is False
    assert corridor['planned_clearance']['ok'] and not corridor['planned_clearance']['violations']
    # Vertical descent in <= 10 mm keyframes from 120 mm above the rim.
    heights = corridor['tip_heights_m']
    assert heights[0] == pytest.approx(corridor['rim_top_m']+.12)
    assert max(a-b for a, b in zip(heights, heights[1:])) <= gg.DESCENT_STEP_M+1e-12
    washer = neutral[plan['primary']]
    assert heights[-1] == pytest.approx(washer['position_m'][2]+.003)
    # Contact on the washer ring at its mid radius, on the cup-axis side.
    offset = math.hypot(corridor['pick_m'][0]-washer['position_m'][0], corridor['pick_m'][1]-washer['position_m'][1])
    assert .00625 < offset < .0125
    assert plan['attachments'][0]['start'] == corridor['attach']
    assert corridor['enter'] < corridor['attach'] < corridor['exit'] < plan['release']


@pytest.mark.parametrize('target', TRAYS)
def test_every_sample_of_the_planned_path_keeps_the_declared_wall_margin(target):
    layout, plan, corridor, samples, postures = run_and_measure(target)
    report = gg.path_clearance(corridor, samples)
    assert report['ok'] and not report['violations'] and report['samples'] == 300
    minima = report['minima']
    assert minima['finger_m'] >= gg.CLEARANCE_MARGIN_M
    assert minima['body_m'] >= gg.CLEARANCE_MARGIN_M
    assert minima['reach_m'] >= gg.CLEARANCE_MARGIN_M
    assert minima['carried_m'] >= gg.CARRIED_MIN_CLEARANCE_M
    assert minima['neighbors_m'] >= 0.   # Touches only the selected washer.
    # Recorded for review: finger capsule ~9.6 mm, rest of hand ~55 mm above the rim.
    assert minima['finger_m'] == pytest.approx(.0216-.012, abs=5e-4)
    assert minima['body_m'] > .05
    assert len(corridor['neighbors']) == 11


@pytest.mark.parametrize('target', TRAYS)
def test_only_the_corridor_goes_below_the_rim_and_nothing_moves_there(target):
    layout, plan, corridor, samples, postures = run_and_measure(target)
    backend = FakeBackend()
    hand = [backend.names.index('right_hand_'+s+'_joint') for s in gg.PAD_POSTURE]
    inside = [row for row in samples if corridor['enter'] <= row['u'] <= corridor['exit']]
    assert len(inside) > 90
    walls = gg.cup_wall_boxes(layout)
    x0 = min(c[0]-s[0]/2 for _, c, s in walls)-gg.FINGER_RADIUS_M-gg.CLEARANCE_MARGIN_M
    x1 = max(c[0]+s[0]/2 for _, c, s in walls)+gg.FINGER_RADIUS_M+gg.CLEARANCE_MARGIN_M
    y0 = min(c[1]-s[1]/2 for _, c, s in walls)-gg.FINGER_RADIUS_M-gg.CLEARANCE_MARGIN_M
    y1 = max(c[1]+s[1]/2 for _, c, s in walls)+gg.FINGER_RADIUS_M+gg.CLEARANCE_MARGIN_M
    for row in samples:
        tip = tip_world(corridor, row['palm'])
        over_cup = x0 <= tip[0] <= x1 and y0 <= tip[1] <= y1
        if over_cup and tip[2] < corridor['rim_top_m']+gg.CLEARANCE_MARGIN_M:
            assert corridor['enter'] < row['u'] < corridor['exit']
    for row in inside:
        q = postures[row['sample']]
        assert [q[i] for i in hand] == list(gg.PAD_POSTURE.values())   # Fingers fixed.
        assert row['palm'][1] == pytest.approx(inside[0]['palm'][1], abs=1e-12)  # Orientation fixed.
        tip = tip_world(corridor, row['palm'])
        assert tip[:2] == pytest.approx(corridor['pick_m'][:2], abs=1e-9)  # Vertical line.
    lowest = min(tip_world(corridor, row['palm'])[2] for row in samples)
    assert lowest == pytest.approx(corridor['pick_m'][2], abs=1e-9)  # Stops on the washer face.


def test_envelope_detects_a_finger_or_hand_inside_a_wall():
    layout, _, _, _, plan = planned('tray_A')
    corridor = plan['cup_corridor']
    walls = {name: center for name, center, _ in corridor['walls']}
    orientation = corridor['orientation_xyzw']
    # Put the fingertip axis on the Front wall, 5 mm below the rim.
    tip = [walls['Front'][0], walls['Front'][1], corridor['rim_top_m']-.005]
    palm = ([a-b for a, b in zip(tip, rotate(orientation, corridor['tip_local_m']))], orientation)
    value = gg.envelope_clearance(corridor, palm)
    assert value['finger_m'] < 0 and value['limiting_wall'] == 'Front'
    # Beside the cup, the finger is clear but the hand body would sink below the rim.
    tip = [corridor['pick_m'][0]+.15, corridor['pick_m'][1], corridor['rim_top_m']-.09]
    palm = ([a-b for a, b in zip(tip, rotate(orientation, corridor['tip_local_m']))], orientation)
    value = gg.envelope_clearance(corridor, palm)
    assert value['finger_m'] > gg.CLEARANCE_MARGIN_M and value['body_m'] < 0
    report = gg.path_clearance(corridor, [dict(sample=0, u=corridor['attach'], palm=palm)])
    assert not report['ok'] and [v['check'] for v in report['violations']] == ['body_m']


def test_reach_rule_outside_the_corridor_is_strict_or_uncertified():
    layout, _, _, _, plan = planned('tray_A')
    corridor = plan['cup_corridor']
    near = ([.045, -.405, .95], [0., 0., 0., 1.])
    strict = gg.path_clearance(corridor, [dict(sample=1, u=0., palm=near)])
    assert not strict['ok'] and strict['violations'][0]['check'] == 'reach_m'
    loose = gg.path_clearance(corridor, [dict(sample=1, u=0., palm=near)], strict_reach=False)
    assert loose['ok'] and loose['uncertified'][0]['sample'] == 1


def test_planner_refuses_a_cup_too_narrow_for_the_declared_margin():
    layout = neutral_layout()
    cup = next(item for item in layout['objects'] if item['id'] == 'supply_cup')
    cup['dimensions_m'] = [.052, .052, .042]   # Inner face 7.6 mm from the fingertip capsule.
    with pytest.raises(ValueError, match='Supply-cup clearance below declared margin'):
        planned('tray_A', layout)


def test_planned_clearance_refuses_a_larger_declared_margin():
    _, _, _, _, plan = planned('tray_A')
    corridor = dict(plan['cup_corridor'], margin_m=.02)
    orientation = corridor['orientation_xyzw']
    knots = []
    for u, z in ((corridor['enter'], corridor['tip_heights_m'][0]), (corridor['attach'], corridor['tip_heights_m'][-1])):
        tip = list(corridor['pick_m'][:2])+[z]
        knots.append((u, ([a-b for a, b in zip(tip, rotate(orientation, corridor['tip_local_m']))], orientation)))
    with pytest.raises(ValueError, match='finger_m'):
        gg.require_planned_clearance(corridor, knots, ([0., 0., 0.], [0., 0., 0., 1.]))


def test_left_hand_or_other_washer_size_is_refused():
    backend = FakeBackend()
    with pytest.raises(ValueError, match='right Dex3'):
        gg.pad_posture(backend.names, backend.positions(), 'left')
    layout = neutral_layout()
    for item in layout['objects']:
        if item['kind'] == 'washer':
            item['dimensions_m'] = [.035, .035, .006]
    with pytest.raises(ValueError, match='25x25x6mm'):
        planned('tray_A', layout)


def test_native_frame_collection_feeds_the_mesh_screen_and_declared_check():
    from isaac.demos.benchmark import collect_cup_frames
    layout, backend, accessors, neutral, plan = planned('tray_B')

    class Adapter:
        def read_state(self):
            palm = backend.palm('right')
            return dict(objects=accessors.read_state(),
                        frames={'right_hand_palm_link': dict(position_m=list(palm[0]), rotation_xyzw=list(palm[1])),
                                'left_hand_palm_link': dict(position_m=[0., 0., 0.], rotation_xyzw=[0., 0., 0., 1.])})

    library = DemoLibrary(backend, accessors, neutral, {('ADD_ONE', 'tray_B'): plan})
    rows, declared = collect_cup_frames(library, Adapter(), plan, 'ADD_ONE', 'tray_B')
    corridor = plan['cup_corridor']
    assert declared['ok'] and declared['samples'] == 300 and not declared['uncertified']
    assert [row['sample'] for row in rows] == [s for s in range(300) if corridor['enter'] <= s/299 <= corridor['exit']]
    assert all(set(row['frames']) == {'right_hand_palm_link'} for row in rows)
    assert library.last_result['execution_ok']


# Transport reorientation, lean and variant selection --------------------------

def palm_yaw(rotation):
    """Yaw of a fingers-down (possibly leaning) palm: direction of palm +Z in the
    horizontal plane (exact for no lean, used only with fake IK)."""
    axis = rotate(rotation, [0., 0., 1.])
    return math.atan2(axis[1], axis[0])


def finger_lean(rotation):
    axis = rotate(rotation, [1., 0., 0.])
    return math.acos(max(-1., min(1., -axis[2])))


def same_yaw(a, b):
    return abs(math.atan2(math.sin(a-b), math.cos(a-b))) < 1e-6


class SelectiveBackend(FakeBackend):
    """Synthetic IK with a declared reachable set; refusals mimic the actual
    backend's bounded-IK ValueError. Optional per-yaw residual at corridor entry."""

    def __init__(self, layout, target, entry_yaws=None, place_yaws=None, min_place_lean=0., entry_residual=None):
        super().__init__()
        neutral = neutral_layout_state(layout)
        from isaac.demos.semantics import consequence
        plan = consequence(layout, neutral, 'ADD_ONE', target)
        corridor = gg.supply_cup_corridor(layout, neutral, plan['primary'], 0.)
        self.pick, self.entry_z = corridor['pick_m'], corridor['tip_heights_m'][0]
        self.destination = plan['expected'][plan['primary']]['position_m']
        self.entry_yaws, self.place_yaws = entry_yaws, place_yaws
        self.min_place_lean, self.entry_residual = min_place_lean, entry_residual or {}

    def solve(self, seed, side, target, quaternion=None, local_point=None, **options):
        if quaternion is not None and side == 'right':
            yaw = palm_yaw(quaternion)
            in_cup = math.dist(target[:2], self.pick[:2]) < 1e-6
            at_tray = math.dist(target[:2], self.destination[:2]) < 1e-6
            if in_cup and self.entry_yaws is not None and not any(same_yaw(yaw, v) for v in self.entry_yaws):
                raise ValueError('Bounded IK search failed: synthetic entry')
            if at_tray and ((self.place_yaws is not None and not any(same_yaw(yaw, v) for v in self.place_yaws))
                            or finger_lean(quaternion) < self.min_place_lean):
                raise ValueError('Bounded IK search failed: synthetic placement')
            residual = next((value for key, value in self.entry_residual.items() if same_yaw(yaw, key)), 0.)
            if in_cup and abs(target[2]-self.entry_z) < 1e-9 and residual:
                q, actual = super().solve(seed, side, [target[0]+residual, target[1], target[2]], quaternion,
                                          local_point=local_point)
                return q, actual
        return super().solve(seed, side, target, quaternion, local_point=local_point)


def neutral_layout_state(layout):
    return deepcopy(FakeAccessors(layout).state)


def play(backend, layout, target):
    accessors = FakeAccessors(layout)
    neutral = deepcopy(accessors.state)
    plan = compile_plan(backend, layout, neutral, 'ADD_ONE', target)
    library = DemoLibrary(backend, accessors, neutral, {('ADD_ONE', target): plan})
    samples = []
    for index, _ in enumerate(library('ADD_ONE', target)):
        u = index/(SAMPLE_COUNT-1)
        attached = plan['attachments'][0]['start'] <= u <= plan['release']
        samples.append(dict(sample=index, u=u, palm=backend.palm('right'),
                            carried=pose(accessors.state[plan['primary']]) if attached else None))
    assert library.last_result['execution_ok']
    return plan, samples, accessors


def test_transport_turns_the_palm_only_once_clear_and_keeps_the_washer_attached():
    layout = neutral_layout()
    # Pickup reachable only at palm yaw pi/2, placement only at palm yaw 0.
    backend = SelectiveBackend(layout, 'tray_D', entry_yaws=[math.pi/2], place_yaws=[0.])
    plan, samples, accessors = play(backend, layout, 'tray_D')
    corridor = plan['cup_corridor']
    chosen = plan['variant_selection']['chosen']
    assert same_yaw(chosen['palm_yaw'], math.pi/2) and same_yaw(chosen['washer_yaw'], -math.pi/2)
    assert plan['washer_endpoint_yaw_rad'] == chosen['washer_yaw'] and chosen['tilt_rad'] == 0.
    report = gg.path_clearance(corridor, samples)
    assert report['ok'] and not report['violations'] and report['samples'] == 300
    grip = plan['attachments'][0]['relative_pose']
    turned = []
    for previous, row in zip(samples, samples[1:]):
        if not corridor['enter'] <= row['u'] <= plan['release']:
            continue
        if angle(row['palm'][1], corridor['orientation_xyzw']) > 1e-9:
            turned.append(row)
            # Turning happens only after the corridor exit, with the whole
            # declared hand and the carried washer clear of the cup.
            assert row['u'] > corridor['exit']
            assert gg.reach_clearance(corridor, row['palm']) >= gg.CLEARANCE_MARGIN_M
            if row['carried'] is not None:
                assert gg.carried_cup_clearance(corridor, row['carried']) >= gg.CLEARANCE_MARGIN_M
        # Smooth: no sample turns the palm by more than one transport step.
        assert angle(previous['palm'][1], row['palm'][1]) <= TRANSPORT_YAW_STEP_RAD+1e-9
        if row['carried'] is not None:
            # Attachment model: carried = palm * grip; the washer stays level (the
            # synthetic joint interpolation of rotation vectors adds < 1 mrad)
            # and the fingertip stays on its contact point.
            expected = compose(row['palm'], grip)
            assert math.dist(expected[0], row['carried'][0]) < 1e-9 and angle(expected[1], row['carried'][1]) < 1e-7
            assert gg.tilt_from_level(row['carried'][1]) < .002
            assert gg.attachment_deviation(corridor, row['palm'], row['carried'])[2] < 1e-9
    assert turned and same_yaw(palm_yaw(samples[round(plan['release']*299)]['palm'][1]), 0.)
    assert report['maxima']['carried_tilt_rad'] < .002 < gg.CARRIED_TILT_LIMIT_RAD
    # The swept joint path was checked before the plan was accepted.
    swept = corridor['swept_path_clearance']
    assert swept['ok'] and swept['samples'] > 300 and not swept['violations']
    assert len(plan['prior_planning_failures']) == 0
    screen = plan['variant_selection']['screens'][0]
    assert sum('headroom' in p for p in screen['pickups']) == 1


def test_transport_keyframes_are_seeded_toward_the_screened_placement():
    layout = neutral_layout()
    calls = []

    class Recording(SelectiveBackend):
        def solve(self, seed, side, target, quaternion=None, local_point=None, **options):
            calls.append((options.get('label'), [name for name, _ in options.get('extra_seeds', ())]))
            return super().solve(seed, side, target, quaternion, local_point=local_point, **options)

    plan, _, _ = play(Recording(layout, 'tray_B'), layout, 'tray_B')
    corridor = plan['cup_corridor']
    seeded = [label for label, names in calls if names == ['exit_to_screened_placement']]
    transport = [u for u, _ in plan['knots'] if corridor['exit'] < u <= round(.64*299)/299]
    assert len(seeded) == len(transport) >= 6
    # Corridor keyframes keep previous-keyframe continuity only.
    assert all(not names for label, names in calls if label and 'screen' not in label
               and float(label.split('/')[-1]) <= .38)


def test_variant_selection_prefers_corridor_entry_headroom():
    layout = neutral_layout()
    # Yaw 0 enters with 0.49 mm (inside the 0.5 mm limit, ~2 % headroom);
    # yaw pi/2 with 0.05 mm. Both can place.
    backend = SelectiveBackend(layout, 'tray_A', entry_residual={0.: .00049, math.pi/2: .00005})
    plan, samples, _ = play(backend, layout, 'tray_A')
    chosen = plan['variant_selection']['chosen']
    assert same_yaw(chosen['palm_yaw'], math.pi/2)
    assert plan['cup_corridor']['entry_ik']['headroom'] == pytest.approx(.9, abs=1e-6)
    pickups = {round(p['palm_yaw'], 6): p for p in plan['variant_selection']['screens'][0]['pickups']}
    assert pickups[0.]['headroom'] == pytest.approx(.02, abs=1e-6)
    assert gg.path_clearance(plan['cup_corridor'], samples)['ok']


def test_lean_is_used_only_when_a_fingers_down_placement_is_unreachable():
    layout = neutral_layout()
    backend = SelectiveBackend(layout, 'tray_D', min_place_lean=.1)
    plan, samples, _ = play(backend, layout, 'tray_D')
    corridor = plan['cup_corridor']
    chosen = plan['variant_selection']['chosen']
    assert chosen['tilt_rad'] == pytest.approx(.15) and corridor['tilt_rad'] == pytest.approx(.15)
    assert finger_lean(corridor['orientation_xyzw']) == pytest.approx(.15)
    assert plan['variant_selection']['screens'][0]['tilt_rad'] == 0.
    assert any(p.startswith('lean 0.00') for p in plan['prior_planning_failures'])
    report = gg.path_clearance(corridor, samples)
    assert report['ok'] and report['minima']['finger_m'] >= gg.CLEARANCE_MARGIN_M
    assert report['minima']['neighbors_m'] >= 0. and report['minima']['body_m'] >= gg.CLEARANCE_MARGIN_M
    # Vertical fingertip line inside the cup; the washer is level while carried.
    inside = [row for row in samples if corridor['enter'] <= row['u'] <= corridor['exit']]
    for row in inside:
        assert tip_world(corridor, row['palm'])[:2] == pytest.approx(corridor['pick_m'][:2], abs=1e-9)
    assert report['maxima']['carried_tilt_rad'] < 1e-7
    with pytest.raises(ValueError, match='lean'):
        gg.hand_orientation(0., gg.MAX_PICKUP_TILT_RAD+.01)


def test_carry_checks_detect_a_slipping_or_tilted_washer_and_turning_in_the_cup():
    layout, plan, corridor, samples, _ = run_and_measure('tray_A')
    moving = next(row for row in samples if row['u'] > corridor['exit'] and row['carried'] is not None)
    slipped = dict(moving, carried=([moving['carried'][0][0]+.002, *moving['carried'][0][1:]], moving['carried'][1]))
    checks = [v['check'] for v in gg.path_clearance(corridor, [slipped])['violations']]
    assert 'attachment_position_m' in checks and 'contact_offset_m' in checks
    tilted_palm = (moving['palm'][0], mul(axis(0., 1., 0., .1), moving['palm'][1]))
    tilted = dict(moving, palm=tilted_palm, carried=compose(tilted_palm, plan['attachments'][0]['relative_pose']))
    checks = [v['check'] for v in gg.path_clearance(corridor, [tilted])['violations']]
    assert checks == ['carried_tilt_rad']
    # Any turn inside the cup is refused.
    inside = next(row for row in samples if corridor['attach'] < row['u'] < corridor['exit'])
    turned = dict(inside, palm=(inside['palm'][0], mul(axis(0., 0., 1., .05), inside['palm'][1])), carried=None)
    checks = [v['check'] for v in gg.path_clearance(corridor, [turned])['violations']]
    assert 'corridor_orientation_rad' in checks
    # After the exit, the whole declared hand must be clear of the cup.
    low = ([.045, -.30, .95], moving['palm'][1])
    report = gg.path_clearance(corridor, [dict(sample=1, u=moving['u'], palm=low, carried=None)])
    assert 'transport_reach_m' in [v['check'] for v in report['violations']]


def test_float32_palm_readback_does_not_trip_the_carry_checks():
    # PhysX link quaternions are float32; a norm 3e-7 below one made
    # 2*acos(|a.b|) report ~1.5 mrad between identical rotations (dev probe 1).
    layout, plan, corridor, samples, _ = run_and_measure('tray_C')
    grip = plan['attachments'][0]['relative_pose']
    rows = []
    for row in samples:
        palm = (row['palm'][0], [v*(1-3e-7) for v in row['palm'][1]])
        rows.append(dict(row, palm=palm, carried=compose(palm, grip) if row['carried'] is not None else None))
    report = gg.path_clearance(corridor, rows)
    assert report['ok'], report['violations'][:1]
    assert report['maxima']['attachment_rotation_rad'] < 1e-9
    assert report['maxima']['corridor_orientation_rad'] < 1e-6
    assert 2*math.acos(min(1., sum(v*v for v in rows[100]['palm'][1]))) > 1e-3   # The old formula's artefact.


def axis(x, y, z, value):
    return axis_angle([x, y, z], value)


def test_swept_joint_path_is_checked_at_one_millimetre_spacing():
    from isaac.demos.planner import _swept_rows, SWEEP_STEP_M
    layout, backend, _, _, plan = planned('tray_B')
    corridor = plan['cup_corridor']
    grip = plan['attachments'][0]['relative_pose']
    rows = _swept_rows(backend, plan['knots'], 'right', corridor, grip, corridor['enter'], plan['release'],
                       corridor['attach'], plan['release'])
    assert rows[0]['u'] == corridor['enter'] and rows[-1]['u'] == pytest.approx(plan['release'], abs=1e-12)
    for a, b in zip(rows, rows[1:]):
        assert a['u'] <= b['u']
        assert math.dist(a['palm'][0], b['palm'][0])+gg.HAND_REACH_M*angle(a['palm'][1], b['palm'][1]) \
            <= SWEEP_STEP_M+1e-12
    # Two knots whose reach spheres clear the cup, joined by a path over it.
    q = backend.positions()
    above_left, above_right = list(q), list(q)
    above_left[6:12] = [-.25, -.405, 1.0, 0., math.pi/2, 0.]
    above_right[6:12] = [.35, -.405, 1.0, 0., math.pi/2, 0.]
    for knots in ([(.40, above_left), (.45, above_right)],):
        swept = _swept_rows(backend, knots, 'right', corridor, grip, .40, .45, corridor['attach'], plan['release'])
        report = gg.path_clearance(corridor, swept)
        assert not report['ok'] and report['violations'][0]['check'] == 'transport_reach_m'


def binary_stl(triangles):
    data = bytearray(80)+struct.pack('<I', len(triangles))
    for triangle in triangles:
        data += struct.pack('<12fH', 0., 0., 0., *[v for point in triangle for v in point], 0)
    return bytes(data)


def test_mesh_screen_reports_vertices_below_margin(tmp_path):
    raw = binary_stl([[(0., 0., 0.), (.001, 0., 0.), (0., .001, 0.)]])
    (tmp_path/'probe_link.STL').write_bytes(raw)
    meshes = {'probe_link.STL': hashlib.sha256(raw).hexdigest()}
    layout = neutral_layout()
    # Inner Front face is x=0.074; place the vertex 3 mm and then 8 mm inside the cup.
    rows = [dict(sample=s, frames={'probe_link': dict(position_m=[x, -.405, .86], rotation_xyzw=[0., 0., 0., 1.])})
            for s, x in ((1, .071-.001), (2, .066-.001))]
    report = gg.screen(dict(rows=rows), layout, tmp_path, meshes=meshes, margin=.005)
    assert not report['margin_ok'] and report['margin_m'] == .005
    assert report['rows'][0]['below_margin'][0]['wall'] == 'Front'
    assert report['rows'][0]['below_margin'][0]['min_vertex_clearance_m'] == pytest.approx(.003, abs=1e-6)
    assert report['rows'][1]['below_margin'] == []
    assert all(not row['cup_vertex_intersections'] for row in report['rows'])
    with pytest.raises(ValueError, match='hash mismatch'):
        gg.screen(dict(rows=rows), layout, tmp_path, meshes={'probe_link.STL': '0'*64})
