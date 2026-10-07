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
from isaac.demos.geometry import compose, pose, rotate
from isaac.demos.planner import compile_plan
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
