"""Synthetic fixed-step demo library for the Unity #62/#66 consumers.

Writes one clearly synthetic recording through the real ``record_fixed_schedule``
and ``TrajectoryWriter`` (so the NDJSON bytes and the per-row ``capture`` record
are exactly what the recorder emits), a matching neutral snapshot, a 40-row
private-index shape, a pre-fixed-step (legacy) capture record and a host-clock
playback table from ``playback_index``/``playback_offset_seconds``. The Unity
EditMode/PlayMode tests read these committed files; ``--check`` (and the pytest
in ``tests/isaac/test_unity_fixture.py``) proves they still match this recorder.

The host clock is a deterministic contended clock (40 ms per sample and a
700 ms stall), so the retained capture stamps span more than 10 s and contain a
gap far above one frame. A consumer that paced playback from those stamps, or
refused them as a playback gap, disagrees with the recording contract.

Nothing here is Isaac or G1 evidence: identities are synthetic, joints are
``j00``..``j42`` and the only public object is one ``card``.

    python -m isaac.demos.unity_fixture --out tests/isaac/fixtures/fixed-step-demo
    python -m isaac.demos.unity_fixture --check tests/isaac/fixtures/fixed-step-demo
"""
import argparse
import filecmp
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from isaac.publisher.protocol import PublicRegistry, StateEncoder
from .recording import (TrajectoryWriter, record_fixed_schedule, write_json, validate_suite,
                        playback_index, playback_offset_seconds, SCHEDULE)
from .runtime import SAMPLE_COUNT, SAMPLE_HZ, NOMINAL_DURATION_SECONDS, PHYSICS_DT_SECONDS
from .semantics import ORIENTATION_PAIRS

STATION = 'synthetic-fixed-step-station'
SCENE_SHA256 = hashlib.sha256(b'synthetic fixed-step demo fixture scene; not an Isaac scene\n').hexdigest()
JOINTS = tuple('j%02d' % i for i in range(43))
SESSION = 'f1' * 16
TRAJECTORY = '000.ndjson'
FILES = ('manifest.json', 'neutral.json', TRAJECTORY, 'index.private.json', 'legacy-capture.json', 'playback.json')
SIM_START_SECONDS = 123.25
CLOCK_START_NS = 10**12
STALL_SAMPLE = 150
# 32 legal pairs in benchmark order (sorted), without importing the command dispatcher.
EXECUTION_ROWS = 32


def _card(index):
    u = index/(SAMPLE_COUNT-1)
    return dict(position_m=[round(.1*u, 9), 0., 0.], rotation_xyzw=[0., 0., 0., 1.],
                visible=True, enabled=True, state=dict(card_face=0 if index < SAMPLE_COUNT//2 else 1))


def _joints(index):
    return [round(.5*index/(SAMPLE_COUNT-1), 9)]+[0.]*42


def neutral_snapshot():
    card = _card(0)
    card.update(collision_enabled=False, linear_velocity_m_s=[0., 0., 0.], angular_velocity_rad_s=[0., 0., 0.])
    return dict(schema_version='1.0.0', scene_sha256=SCENE_SHA256, fixed_steps=1,
                coordinate_frame='usd_world_rh_z_up_xyzw', state=dict(
                    robot=dict(joint_names=list(JOINTS), joint_positions_rad=_joints(0),
                               joint_velocities_rad_s=[0.]*43, root_position_m=[0., 0., 0.],
                               root_rotation_xyzw=[0., 0., 0., 1.], root_linear_velocity_m_s=[0., 0., 0.],
                               root_angular_velocity_rad_s=[0., 0., 0.]),
                    objects=dict(card=card), environment=dict(materials={}, lights={}),
                    frames={'link%d' % i: dict(position_m=[0., 0., 0.], rotation_xyzw=[0., 0., 0., 1.])
                            for i in range(3)}))


def contended_clock():
    state = dict(now=CLOCK_START_NS, calls=0)

    def clock():
        state['now'] += 40_000_000+(700_000_000 if state['calls'] == STALL_SAMPLE else 0)
        state['calls'] += 1
        return state['now']
    return clock


def legacy_capture(record):
    """The pre-fixed-step (#118) capture field set, for refusal tests only."""
    old = {key: record[key] for key in ('file', 'sha256', 'frame_count', 'nominal_sample_hz',
                                        'nominal_duration_seconds', 'capture_complete',
                                        'actual_host_timestamps_preserved', 'time_compressed')}
    old.update(measured_first_to_last_host_seconds=record['capture_host_seconds'],
               interval_ms=record['capture_interval_ms'], timing_ok=True,
               timing_rule='300 samples, first-to-last span <= 10 s, no gap > 250 ms; provisional engineering screen')
    return old


def _row(group, action, target, capture):
    execution = dict(execution_ok=True, semantic_error=False, failures=[], robot_neutral_error_rad=0.,
                     sample_count=SAMPLE_COUNT, nominal_duration_seconds=NOMINAL_DURATION_SECONDS,
                     private_result={}, events=[], collision_reviewed=False, grasp_contact_validated=False)
    return dict(group=group, action=action, target=target, capture=dict(capture), execution=execution,
                error=None, reset_ok=True, replay_end_state_ok=True,
                expected_objects_sha256=hashlib.sha256(('synthetic expected '+action+'/'+target).encode()).hexdigest(),
                private_plan_key=action+'/'+target)


def generate(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    neutral_bytes = (json.dumps(neutral_snapshot(), indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    (out/'neutral.json').write_bytes(neutral_bytes)
    snapshot_sha256 = hashlib.sha256(neutral_bytes).hexdigest()
    registry = PublicRegistry(STATION, SCENE_SHA256, snapshot_sha256, JOINTS, (('card', ('card_face',)),), ())
    encoder = StateEncoder(registry, source_kind='live', clock_ns=contended_clock())
    encoder.session_id = SESSION
    sim = dict(time=SIM_START_SECONDS, steps=0, sample=-1)

    def physics_step():
        sim['time'] += PHYSICS_DT_SECONDS
        sim['steps'] += 1

    def advance():
        sim['sample'] += 1

    writer = TrajectoryWriter(out/TRAJECTORY, registry)
    record_fixed_schedule(writer, encoder, physics_step=physics_step, advance=advance, finished=lambda: False,
                          sample=lambda: (_joints(sim['sample']), dict(card=_card(sim['sample'])), sim['time']))
    capture = writer.close(completed=True)
    rows = [_row('orientation', action, target, capture) for action, target in ORIENTATION_PAIRS]
    # Execution rows carry the same synthetic capture; only their schedule
    # records matter to the Unity orientation loader (suite-wide duration).
    rows += [_row('execution', 'SYNTHETIC_%02d' % i, 'synthetic', capture) for i in range(EXECUTION_ROWS)]
    validate_suite([row['capture'] for row in rows])
    index = dict(kind='actual_G1_kinematic_visualization', scene_sha256=SCENE_SHA256,
                 reset_snapshot_sha256=snapshot_sha256, planning_pairs=32, feasible_pairs=32,
                 planning_reset_ok=True, collision_reviewed=False, grasp_contact_validated=False,
                 methodology_review_complete=False, recording_complete=True, rows=rows,
                 protected_real_factory=dict(rejected=32, before_sha256='b'*64, after_sha256='b'*64,
                                             unchanged=True, factory_ran=False),
                 joint_names_sha256=hashlib.sha256(('\n'.join(JOINTS)+'\n').encode()).hexdigest(),
                 station_id=STATION, nominal_duration_seconds=NOMINAL_DURATION_SECONDS)
    write_json(out/'index.private.json', index)
    write_json(out/'legacy-capture.json', legacy_capture(capture))
    probes = sorted({0., 1/30-1e-6, 1/30, 299/30, 9.999, 10-1e-9, NOMINAL_DURATION_SECONDS, 10.5}
                    | {i/SAMPLE_HZ for i in range(SAMPLE_COUNT)} | {(i+.5)/SAMPLE_HZ for i in range(SAMPLE_COUNT)})
    write_json(out/'playback.json', dict(
        schedule=SCHEDULE, offsets_seconds=[playback_offset_seconds(i) for i in range(SAMPLE_COUNT)],
        index_at_elapsed_seconds=[[elapsed, playback_index(elapsed)] for elapsed in probes]))
    files = {name: hashlib.sha256((out/name).read_bytes()).hexdigest() for name in FILES if name != 'manifest.json'}
    write_json(out/'manifest.json', dict(
        kind='synthetic_fixed_step_demo_fixture', synthetic=True, isaac_evidence=False,
        generator='python -m isaac.demos.unity_fixture', station_id=STATION, scene_sha256=SCENE_SHA256,
        reset_snapshot_sha256=snapshot_sha256, joint_names=list(JOINTS), objects=dict(card=['card_face']),
        anchors=[], schedule=SCHEDULE, trajectory=TRAJECTORY, capture_host_stall_sample=STALL_SAMPLE,
        files=files))
    return capture


def check(directory):
    """Regenerate into a fresh directory and compare every committed byte."""
    with tempfile.TemporaryDirectory() as temporary:
        fresh = Path(temporary)/'fixture'
        generate(fresh)
        names = sorted(path.name for path in Path(directory).iterdir())
        if names != sorted(FILES):
            return ['file set: %r' % names]
        return [name for name in FILES if not filecmp.cmp(fresh/name, Path(directory)/name, shallow=False)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out', help='fresh directory to write')
    group.add_argument('--check', help='committed fixture directory to verify')
    args = parser.parse_args(argv)
    if args.out:
        generate(args.out)
        print('WROTE synthetic fixed-step fixture', args.out)
        return 0
    differences = check(args.check)
    print('FIXTURE_MISMATCH '+', '.join(differences) if differences else 'PASS: fixture matches the recorder')
    return 1 if differences else 0


if __name__ == '__main__':
    sys.exit(main())
