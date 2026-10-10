"""Meaning, coupling, cancellation and recording tests; fake IK is not reach evidence."""
from copy import deepcopy
import math

import pytest

from isaac.commands.protocol import LEGAL_PAIRS
from isaac.demos.geometry import norm, pose, compose, relative, axis_angle, angle
from isaac.demos.planner import compile_plan
from isaac.demos.runtime import (DemoLibrary, SAMPLE_COUNT, NOMINAL_DURATION_SECONDS, PHYSICS_DT_SECONDS,
                                 PHYSICS_STEPS_PER_SAMPLE, TOTAL_PHYSICS_STEPS, compare_objects)
from isaac.demos.semantics import consequence, ORIENTATION_PAIRS
from isaac.workcell.layout import neutral_layout, neutral_state
from isaac.workcell.state import validate_states


HAND_SUFFIXES = ('thumb_0', 'thumb_1', 'thumb_2', 'middle_0', 'middle_1', 'index_0', 'index_1')


class FakeBackend:
    def __init__(self):
        self.names = ['fixture_joint_'+str(i) for i in range(43)]
        for start, side in ((12, 'left'), (19, 'right')):
            for offset, suffix in enumerate(HAND_SUFFIXES):
                self.names[start+offset] = side+'_hand_'+suffix+'_joint'
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

    def solve(self, seed, side, target, quaternion=None, local_point=None, **_):
        q = list(seed)
        i = 0 if side == 'left' else 6
        q[i:i+3] = target
        if quaternion is not None:
            size = norm(quaternion[:3])
            scale = 0. if size < 1e-10 else 2*math.atan2(size, quaternion[3])/size
            q[i+3:i+6] = [v*scale for v in quaternion[:3]]
        self.write(q)
        if local_point is not None:
            from isaac.demos.geometry import rotate
            off = rotate(self.palm(side)[1], local_point)
            q[i:i+3] = [a-b for a,b in zip(target, off)]
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


def test_card_side_grip_stays_level_when_the_card_is_turned_over():
    demo, plan = library('FLIP_CARD', 'tray_A')
    iterator = demo('FLIP_CARD', 'tray_A')
    for index in range(210):
        next(iterator)
        if index in (60, 191):
            palm = demo.backend.palm('right')
            card = demo.accessors.state[plan['primary']]
            assert abs(palm[0][2]-card['position_m'][2]) < 1e-8
    iterator.close()


def test_preconditions_and_illegal_targets_fail_before_motion():
    layout = neutral_layout()
    state = neutral_state(layout)
    with pytest.raises(ValueError, match='Illegal'):
        consequence(layout, state, 'CLOSE', 'tray_A')
    state['container_E/lid']['state']['lid_open_fraction'] = 0.
    with pytest.raises(ValueError, match='precondition'):
        consequence(layout, state, 'CLOSE', 'container_E')


# Fixed sim-step recording schedule ------------------------------------------

def registry_fixture():
    from isaac.publisher.protocol import PublicRegistry
    layout = neutral_layout()
    objects = neutral_state(layout)
    registry = PublicRegistry('fixture', 'a'*64, 'b'*64, tuple('j'+str(i) for i in range(43)),
        tuple((key, tuple(sorted(value['state']))) for key, value in sorted(objects.items())), tuple(layout['anchor_ids']))
    return registry, objects


def contended_host_clock(start=10**12):
    """Synthetic loaded host: 40 ms samples plus a 700 ms stall at sample 150."""
    state = dict(now=start, calls=0)

    def clock():
        state['now'] += 40_000_000+(700_000_000 if state['calls'] == 150 else 0)
        state['calls'] += 1
        return state['now']
    return clock


class FakeSim:
    """Simulator clock advancing ``dt`` per physics step from a nonzero time."""

    def __init__(self, dt=PHYSICS_DT_SECONDS, start=123.25):
        self.dt, self.time, self.steps = dt, start, 0

    def step(self):
        self.time += self.dt
        self.steps += 1


def recording_fixture(sim_start=50.):
    from isaac.publisher.protocol import StateEncoder
    registry, objects = registry_fixture()
    encoder = StateEncoder(registry, source_kind='synthetic', clock_ns=contended_host_clock())
    frames = [encoder.build([0.]*43, objects, sim_start+(i+1)*PHYSICS_STEPS_PER_SAMPLE*PHYSICS_DT_SECONDS,
                            (i+1)*PHYSICS_STEPS_PER_SAMPLE) for i in range(SAMPLE_COUNT)]
    return registry, frames


def test_recording_duration_is_fixed_steps_despite_host_contention(tmp_path):
    from isaac.demos.recording import TrajectoryWriter, read_trajectory, SCHEDULE
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    for frame in frames: writer.append(frame)
    summary = writer.close(completed=True)
    assert summary['schedule_ok'] and summary['frame_count'] == 300
    assert summary['recorded_physics_steps'] == TOTAL_PHYSICS_STEPS == 600
    assert summary['recorded_duration_seconds'] == pytest.approx(NOMINAL_DURATION_SECONDS, abs=1e-12)
    assert summary['schedule'] == SCHEDULE
    # The loaded host took > 12 s; that is preserved provenance, not a duration.
    assert summary['capture_host_seconds'] > 12. and summary['capture_interval_ms']['max'] > 700
    assert summary['actual_host_timestamps_preserved'] and not summary['time_compressed']
    assert summary['playback_clock'] == 'host_monotonic_fixed_sample_period'
    assert 'timing_ok' not in summary and 'never on sim_time' in summary['timing_rule']
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
    assert not report['schedule_ok'] and report['frame_count'] == 1


@pytest.mark.parametrize('field,value', [
    ('sim_step', 3),                                    # One physics step skipped.
    ('sim_step', 6),                                    # Extra steps in the sample.
    ('sim_time', 50.+2/60+1/60),                        # Simulator advanced one step only.
    ('sim_time', 50.+2/60+3/60),                        # Simulator advanced three steps.
])
def test_writer_refuses_frames_off_the_fixed_step_schedule(tmp_path, field, value):
    from isaac.demos.recording import TrajectoryWriter
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    writer.append(frames[0])
    with pytest.raises(ValueError, match='schedule violated'):
        writer.append({**frames[1], field: value})
    report = writer.close(completed=False)
    assert not report['schedule_ok'] and report['frame_count'] == 1


def test_first_sample_must_follow_exactly_two_physics_steps(tmp_path):
    from isaac.demos.recording import TrajectoryWriter
    registry, frames = recording_fixture()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    with pytest.raises(ValueError, match='schedule violated'):
        writer.append({**frames[0], 'sim_step': 1})
    writer.close(completed=False)


def test_short_or_long_capture_is_never_schedule_ok(tmp_path):
    from isaac.demos.recording import TrajectoryWriter, read_trajectory
    from isaac.publisher.protocol import StateEncoder
    registry, frames = recording_fixture()
    short = TrajectoryWriter(tmp_path/'short.ndjson', registry)
    for frame in frames[:-1]: short.append(frame)
    report = short.close(completed=True)
    assert not report['schedule_ok'] and report['recorded_physics_steps'] == 598
    with pytest.raises(ValueError, match='schedule violated'):
        read_trajectory(short.path, report['sha256'], registry)
    full = TrajectoryWriter(tmp_path/'full.ndjson', registry)
    for frame in frames: full.append(frame)
    extra = StateEncoder(registry, source_kind='synthetic', clock_ns=lambda: 10**15).build(
        [0.]*43, registry_fixture()[1], frames[-1]['sim_time']+2/60, 602)
    extra.update(session_id=frames[-1]['session_id'], seq=frames[-1]['seq']+1)
    with pytest.raises(ValueError, match='more than 300'):
        full.append(extra)
    assert full.close(completed=True)['schedule_ok']


def test_capture_loop_steps_physics_exactly_twice_per_sample_without_sleeping(tmp_path, monkeypatch):
    import time
    from isaac.demos.recording import TrajectoryWriter, record_fixed_schedule
    from isaac.publisher.protocol import StateEncoder
    monkeypatch.setattr(time, 'sleep', lambda *_: pytest.fail('capture must not sleep on a host deadline'))
    registry, objects = registry_fixture()
    sim, advances = FakeSim(), []
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    steps = record_fixed_schedule(writer, StateEncoder(registry, 'synthetic', clock_ns=contended_host_clock()),
        physics_step=sim.step, advance=lambda: advances.append(sim.steps), finished=lambda: False,
        sample=lambda: ([0.]*43, objects, sim.time))
    report = writer.close(completed=True)
    assert steps == sim.steps == 600 and report['schedule_ok']
    assert advances == [2*(i+1) for i in range(300)]


@pytest.mark.parametrize('dt', [1/50, 1/120])
def test_capture_refuses_a_simulator_off_the_declared_physics_dt(tmp_path, dt):
    from isaac.demos.recording import TrajectoryWriter, record_fixed_schedule
    from isaac.publisher.protocol import StateEncoder
    registry, objects = registry_fixture()
    sim = FakeSim(dt)
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    with pytest.raises(ValueError, match='schedule violated'):
        record_fixed_schedule(writer, StateEncoder(registry, 'synthetic', clock_ns=contended_host_clock()),
            physics_step=sim.step, advance=lambda: None, finished=lambda: False,
            sample=lambda: ([0.]*43, objects, sim.time))
    assert not writer.close(completed=False)['schedule_ok']


def test_capture_refuses_a_demo_that_finishes_early(tmp_path):
    from isaac.demos.recording import TrajectoryWriter, record_fixed_schedule
    from isaac.publisher.protocol import StateEncoder
    registry, objects = registry_fixture()
    sim = FakeSim()
    writer = TrajectoryWriter(tmp_path/'000.ndjson', registry)
    with pytest.raises(RuntimeError, match='before its fixed sample count'):
        record_fixed_schedule(writer, StateEncoder(registry, 'synthetic', clock_ns=contended_host_clock()),
            physics_step=sim.step, advance=lambda: None, finished=lambda: sim.steps >= 200,
            sample=lambda: ([0.]*43, objects, sim.time))
    assert writer.close(completed=False)['frame_count'] == 99


def record_all_40(tmp_path):
    """8 orientation + 32 execution captures through the real runtime and writer."""
    from isaac.demos.recording import TrajectoryWriter, record_fixed_schedule
    from isaac.publisher.protocol import StateEncoder
    registry, _ = registry_fixture()
    items = [('orientation', a, t) for a, t in ORIENTATION_PAIRS]+[('execution', a, t) for a, t in sorted(LEGAL_PAIRS)]
    records = []
    sim = FakeSim()
    for number, (group, action, target) in enumerate(items):
        demo, plan = library(action, target)
        iterator = demo(action, target)
        writer = TrajectoryWriter(tmp_path/f'{number:03d}.ndjson', registry)
        record_fixed_schedule(writer, StateEncoder(registry, 'synthetic', clock_ns=contended_host_clock(10**12*(number+1))),
            physics_step=sim.step, advance=lambda: next(iterator), finished=lambda: False,
            sample=lambda: (demo.backend.positions(), demo.accessors.read_state(), sim.time))
        with pytest.raises(StopIteration):
            next(iterator)  # Terminal check after the last sample.
        assert demo.last_result['execution_ok']
        records.append(writer.close(completed=True))
    return records


def test_all_8_orientation_and_32_execution_recordings_share_one_duration(tmp_path):
    from isaac.demos.recording import validate_suite
    records = record_all_40(tmp_path)
    suite = validate_suite(records)
    assert suite['recordings'] == 40 and suite['identical_physics_steps'] == 600
    assert {r['recorded_physics_steps'] for r in records} == {600}
    assert {r['frame_count'] for r in records} == {300}
    assert {r['recorded_duration_seconds'] for r in records} == {600*PHYSICS_DT_SECONDS}
    # Every capture overran 10 s of host time under contention; the recorded
    # duration is still exactly 600 steps, and no host stamp was rewritten.
    assert all(r['capture_host_seconds'] > NOMINAL_DURATION_SECONDS for r in records)
    assert all(not r['time_compressed'] and r['actual_host_timestamps_preserved'] for r in records)


@pytest.mark.parametrize('damage', ['steps', 'incomplete', 'frames', 'schedule', 'count'])
def test_suite_refuses_any_recording_with_a_different_duration(damage):
    from isaac.demos.recording import validate_suite, SCHEDULE
    good = dict(schedule=dict(SCHEDULE), capture_complete=True, schedule_ok=True, frame_count=300,
                recorded_physics_steps=600)
    records = [deepcopy(good) for _ in range(40)]
    if damage == 'steps':
        records[17]['recorded_physics_steps'] = 602
    elif damage == 'incomplete':
        records[3]['schedule_ok'] = False
    elif damage == 'frames':
        records[39]['frame_count'] = 299
    elif damage == 'schedule':
        records[0]['schedule'] = {**SCHEDULE, 'physics_steps_per_sample': 3}
    else:
        records.pop()
    with pytest.raises(ValueError, match='(?i)fixed'):
        validate_suite(records)


def test_playback_is_paced_by_host_time_and_sample_index_only():
    from isaac.demos.recording import playback_index, playback_offset_seconds
    assert [playback_offset_seconds(i) for i in (0, 1, 299)] == [0., 1/30, 299/30]
    assert playback_index(0.) == 0 and playback_index(1/30-1e-6) == 0 and playback_index(1/30) == 1
    assert playback_index(299/30) == 299 and playback_index(9.999) == 299  # Last sample held.
    assert playback_index(NOMINAL_DURATION_SECONDS) is None
    for bad in (-1e-9, math.nan, math.inf):
        with pytest.raises(ValueError):
            playback_index(bad)
    with pytest.raises(ValueError):
        playback_offset_seconds(300)
    # The schedule takes no frame input, so neither sim_time nor the capture
    # host stamps (which include a 700 ms stall in the fixture) can retime it.
    import inspect
    assert list(inspect.signature(playback_index).parameters) == ['elapsed_host_seconds']


