"""Meaning, coupling, cancellation and recording tests; fake IK is not reach evidence."""
from copy import deepcopy
import math

import pytest

from isaac.commands.protocol import LEGAL_PAIRS
from isaac.demos.geometry import norm, pose, compose, relative, axis_angle, angle
from isaac.demos.planner import compile_plan
from isaac.demos.runtime import DemoLibrary, SAMPLE_COUNT, compare_objects
from isaac.demos.semantics import consequence, ORIENTATION_PAIRS
from isaac.workcell.layout import neutral_layout, neutral_state
from isaac.workcell.state import validate_states


class FakeBackend:
    def __init__(self):
        self.names = ['fixture_joint_'+str(i) for i in range(43)]
        self.q = [0.]*43
        self.q[:3], self.q[6:9] = [0., .2, 1.2], [0., -.2, 1.2]
        self.neutral = dict(joint_positions_rad=list(self.q))
        self.writes = 0

    def write(self, q):
        self.writes += 1
        self.q = list(q)

    def positions(self):
        return list(self.q)

    def palm(self, side):
        i = 0 if side == 'left' else 6
        rotation = self.q[i+3:i+6]
        size = norm(rotation)
        q = [0., 0., 0., 1.] if size < 1e-10 else axis_angle(rotation, size)
        return self.q[i:i+3], q

    def fingers(self, q, side, closure):
        result = list(q)
        i = 12 if side == 'left' else 19
        result[i:i+7] = [closure*.3]*7
        return result

    def solve(self, seed, side, target, quaternion=None, **_):
        q = list(seed)
        i = 0 if side == 'left' else 6
        q[i:i+3] = target
        if quaternion is not None:
            size = norm(quaternion[:3])
            scale = 0. if size < 1e-10 else 2*math.atan2(size, quaternion[3])/size
            q[i+3:i+6] = [v*scale for v in quaternion[:3]]
        self.write(q)
        return q, self.palm(side)


class FakeAccessors:
    def __init__(self, layout):
        self.layout = layout
        self.state = deepcopy(neutral_state(layout))
        self.history = []

    def read_state(self):
        return deepcopy(self.state)

    def apply_subset(self, values):
        candidate = {**self.state, **deepcopy(values)}
        validate_states(self.layout, candidate)
        self.state = candidate
        self.history.append(deepcopy(candidate))


def library(action, target):
    layout, backend = neutral_layout(), FakeBackend()
    accessors = FakeAccessors(layout)
    initial = deepcopy(accessors.state)
    plan = compile_plan(backend, layout, initial, action, target)
    return DemoLibrary(backend, accessors, initial, {(action, target): plan}), plan


def finish(iterator):
    samples = []
    while True:
        try:
            samples.append(next(iterator))
        except StopIteration as completed:
            return samples, completed.value


@pytest.mark.parametrize('action,target', sorted(LEGAL_PAIRS))
def test_all_32_meanings_and_end_states_with_synthetic_ik(action, target):
    demo, plan = library(action, target)
    samples, result = finish(demo(action, target))
    assert len(samples) == SAMPLE_COUNT
    assert result['execution_ok'] and not result['semantic_error']
    assert not compare_objects(demo.accessors.state, plan['expected'])
    assert demo.backend.positions() == demo.backend.neutral['joint_positions_rad']
    assert not result['collision_reviewed'] and not result['grasp_contact_validated']
    if action == 'REMOVE_ONE':
        assert [row['event'] for row in result['events']] == ['handoff', 'release']
    if action == 'SCAN':
        assert result['private_result'] == {'scanned_code': target[-1]+'001'}


def test_orientation_set_has_one_of_every_action_and_same_sample_count():
    assert len(ORIENTATION_PAIRS) == 8
    assert len({action for action, _ in ORIENTATION_PAIRS}) == 8
    assert all(len(finish(library(a, t)[0](a, t))[0]) == SAMPLE_COUNT for a, t in ORIENTATION_PAIRS)


def test_handoff_refuses_disagreement_before_left_ownership():
    demo, plan = library('REMOVE_ONE', 'tray_A')
    plan['handoff']['destination_relative'][0][0] += .02
    with pytest.raises(ValueError, match='ownership unchanged'):
        finish(demo('REMOVE_ONE', 'tray_A'))
    assert demo.accessors.state[plan['primary']]['state']['location'] == 'robot/right_hand'
    assert not demo.active and demo.last_result is None


def test_cancellation_preserves_visible_state_for_explicit_reset():
    demo, plan = library('ADD_ONE', 'tray_A')
    iterator = demo('ADD_ONE', 'tray_A')
    for _ in range(120):
        next(iterator)
    before = deepcopy(demo.accessors.state)
    iterator.close()
    assert demo.accessors.state == before and before != demo.neutral
    assert not demo.active and demo.last_result is None


def test_carried_container_moves_its_lid_and_code_but_not_free_tag():
    demo, plan = library('QUARANTINE', 'container_E')
    initial = demo.accessors.read_state()
    iterator = demo('QUARANTINE', 'container_E')
    for _ in range(130):
        next(iterator)
    actual = demo.accessors.state
    for child in ('container_E/lid', 'container_E/code'):
        before = relative(pose(initial['container_E']), pose(initial[child]))
        after = relative(pose(actual['container_E']), pose(actual[child]))
        assert norm([a-b for a, b in zip(before[0], after[0])]) < 1e-9
        assert angle(before[1], after[1]) < 1e-7
    assert actual['container_E/tag'] == initial['container_E/tag']
    iterator.close()


def test_virtual_attachment_is_measured_palm_relative():
    demo, plan = library('ADD_ONE', 'tray_A')
    iterator = demo('ADD_ONE', 'tray_A')
    for _ in range(125):
        next(iterator)
    attachment = plan['attachments'][0]
    expected = compose(demo.backend.palm('right'), attachment['relative_pose'])
    actual = pose(demo.accessors.state[plan['primary']])
    assert actual == expected
    iterator.close()


def test_preconditions_and_illegal_targets_fail_before_motion():
    layout = neutral_layout()
    state = neutral_state(layout)
    with pytest.raises(ValueError, match='Illegal'):
        consequence(layout, state, 'CLOSE', 'tray_A')
    state['container_E/lid']['state']['lid_open_fraction'] = 0.
    with pytest.raises(ValueError, match='precondition'):
        consequence(layout, state, 'CLOSE', 'container_E')


def recording_fixture():
    from isaac.publisher.protocol import PublicRegistry, StateEncoder
    layout = neutral_layout()
    objects = neutral_state(layout)
    registry = PublicRegistry('fixture', 'a'*64, 'b'*64, tuple('j'+str(i) for i in range(43)),
        tuple((key, tuple(sorted(value['state']))) for key, value in sorted(objects.items())), tuple(layout['anchor_ids']))
    stamps = iter(1000000000+round(i*1e9/30) for i in range(SAMPLE_COUNT))
    encoder = StateEncoder(registry, source_kind='synthetic', clock_ns=lambda: next(stamps))
    frames = [encoder.build([0.]*43, objects, (i+1)/30, i+1) for i in range(SAMPLE_COUNT)]
    return registry, frames


def test_recording_hash_public_projection_and_actual_host_span(tmp_path):
    from isaac.demos.recording import TrajectoryWriter, read_trajectory
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    for frame in frames: writer.append(frame)
    summary = writer.close(completed=True)
    assert summary['timing_ok'] and summary['frame_count'] == 300
    assert summary['measured_first_to_last_host_seconds'] == pytest.approx(299/30)
    assert summary['nominal_duration_seconds'] == 10.
    assert read_trajectory(writer.path, summary['sha256'], registry) == frames
    assert all('action' not in frame and 'target' not in frame for frame in frames)
    writer.path.write_bytes(writer.path.read_bytes()+b' ')
    with pytest.raises(ValueError, match='SHA-256'):
        read_trajectory(writer.path, summary['sha256'], registry)


@pytest.mark.parametrize('field,value', [('host_monotonic_ns', '0'), ('sim_time', 0.), ('sim_step', 0), ('seq', 7)])
def test_recording_rejects_replay_or_clock_regression(tmp_path, field, value):
    from isaac.demos.recording import TrajectoryWriter
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    writer.append(frames[0])
    bad = {**frames[1], field: value}
    with pytest.raises(ValueError, match='progress'):
        writer.append(bad)
    report = writer.close(completed=False)
    assert not report['timing_ok'] and report['frame_count'] == 1


def test_capture_overrun_is_preserved_and_not_time_compressed(tmp_path):
    from isaac.demos.recording import TrajectoryWriter
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    for index, frame in enumerate(frames):
        frame['host_monotonic_ns'] = str(int(frame['host_monotonic_ns'])+(500000000 if index >= 150 else 0))
        writer.append(frame)
    report = writer.close(completed=True)
    assert not report['timing_ok'] and report['capture_complete']
    assert report['measured_first_to_last_host_seconds'] > 10.
    assert report['interval_ms']['max'] > 500 and not report['time_compressed']
