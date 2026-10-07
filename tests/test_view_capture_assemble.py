"""Synthetic Unity-run fixtures for the O6.1.2 assembler; never station evidence."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import view_capture_assemble as a
from tools import view_leakage as v
from test_view_leakage import png, reply

SESSION = 'a' * 32
NEUTRAL = json.loads((ROOT / 'isaac/snapshots/neutral_v1.json').read_bytes())


def save(root, name, value, raw=False):
    data = value if raw else v.canonical(value)
    (root / name).write_bytes(data)
    return {'path': name, 'sha256': v.sha(data)}


def plan_for(root, source_kind='snapshot'):
    poses = [('center', 1, 0)] + [(n, axis, deg) for n, axis, deg in (
        ('yaw_left', 1, -15), ('yaw_right', 1, 15), ('pitch_up', 0, -10), ('pitch_down', 0, 10))]
    items = []
    for name, axis, degrees in poses:
        q = [0., 0., 0., math.cos(math.radians(degrees) / 2)]
        q[axis] = math.sin(math.radians(degrees) / 2)
        items.append(dict(id=name, head_pose=dict(position_m=[0, 1.2, -2], rotation_xyzw=q)))
    plan = dict(version=1, scope='engineering_provisional', participants=False, source_kind=source_kind,
                capture_surface='desktop_engineering', station_id='DEMO-station', build_sha256='b' * 64,
                scene_sha256=NEUTRAL['scene_sha256'], snapshot=save(root, 'neutral.json', NEUTRAL),
                image=dict(width=4, height=2, max_channel_delta=0, minimum_unique_rgb_colors=2), poses=items,
                head_position_tolerance_m=.001, head_orientation_tolerance_rad=math.radians(.5),
                state_tolerances=v.DEFAULT_TOLERANCES.copy(), panel_state_sha256='c' * 64, max_state_arrival_age_ms=250)
    return plan, save(root, 'plan.json', plan)['sha256']


def frame_of(state, sequence):
    objects = [dict(id=k, position_m=o['position_m'], rotation_xyzw=o['rotation_xyzw'], visible=o['visible'],
                    enabled=o['enabled'], state=o['state']) for k, o in sorted(state['objects'].items())]
    return dict(joint_positions=state['robot']['joint_positions_rad'], objects=objects, seq=sequence)


def unity_run(root, plan, plan_pin):
    """Mirror of the Unity driver's run directory (ViewCaptureOutput)."""
    run = root / 'evidence' / 'view-capture-synthetic'
    run.mkdir(parents=True)
    live = plan['source_kind'] == 'live'
    audio = dict(spatial_blend=0, pan_stereo=0, doppler_level=0, spread=0, volume=.25, pitch=1, mute=False,
                 enabled=True, loop=False, output_mixer_group='', route_id='DEMO-route', source_count=1, effects=[])
    view = dict(panel_state_sha256='c' * 64, visible_text=['DEMO fixed panel'], renderer_inventory_sha256='d' * 64,
                console_visible=False, diagnostics_visible=False)
    header = dict(version=1, kind='view_capture_run', scope='SIMULATION_TEST', participant_admission=False, g3_signed=False,
                  plan_sha256=plan_pin, station_id='DEMO-station', source_kind=plan['source_kind'],
                  capture_surface='desktop_engineering', build_sha256='b' * 64, clock_id='f' * 32,
                  clock_kind='client_stopwatch_monotonic_ns', plan_seen_ns='1', control_session_id=SESSION,
                  simulation_capability_sha256='e' * 64, capture_method='synthetic', state_origin='synthetic',
                  trial_boundary='synthetic', settings=dict(pose_settle_frames=1, pre_cue_frames=1, step_timeout_ms=500),
                  poses=[p['id'] for p in plan['poses']], legal_pairs=32, captures_required=160)
    run_ref = save(run, 'run.json', header)
    rows, observations = [], []
    index = 0
    for pose in plan['poses']:
        for action, target in v.PAIRS:
            index += 1
            prefix = f'c{index:03d}'
            stamp = index * 1000
            row = dict(capture_id=f'{index:032x}', pose_id=pose['id'], action=action, target=target,
                       head_pose=deepcopy(pose['head_pose']), reset_received_ns=str(stamp), captured_ns=str(stamp + 2),
                       cue_requested_ns=str(stamp + 3), state_sample_ns=str(stamp + 1))
            row['reset_reply'] = save(run, prefix + '-reset.json', reply(index))
            row['image'] = save(run, prefix + '-image.png', png(), True)
            row['audio'] = save(run, prefix + '-audio.json', audio)
            row['view'] = save(run, prefix + '-view.json', view)
            row['inventory'] = save(run, prefix + '-inventory.json', dict(version=1, kind='synthetic'))
            if live:
                frame = frame_of(NEUTRAL['state'], index)
                row['state'] = None
                row['applied'] = save(run, prefix + '-applied.json', dict(
                    version=1, kind='view_capture_applied_frame', provenance='live', session_id='9' * 32, sequence=index,
                    sim_step=index, published_ns=str(stamp), sim_time=index / 60, coordinate_frame='isaac_world_public_projection',
                    joint_positions=frame['joint_positions'], objects=frame['objects']))
                text = json.dumps(frame, separators=(',', ':'))
                observations.append(dict(version=1, kind='view_state_observation', observation_id=f'{index:032x}',
                    observed_host_ns=str(stamp), reset_request_id=f'{index:032x}', reset_reply_canonical_sha256='0' * 64,
                    public_session_id='9' * 32, sequence=index, sim_step=index, frame_sha256=v.sha(text.encode()),
                    frame_utf8=text, frame=frame, state=deepcopy(NEUTRAL['state'])))
            else:
                row['state'] = save(run, prefix + '-state.json', NEUTRAL['state'])
                row['applied'] = None
            rows.append(row)
    rows_ref = save(run, 'rows.jsonl', b''.join(v.canonical(r) for r in rows), True)
    status = dict(version=1, kind='view_capture_status', complete=True, fault=None, captures_completed=160,
                  captures_required=160, run=run_ref, rows=rows_ref, participant_admission=False, g3_signed=False)
    status_pin = save(run, 'status.json', status)['sha256']
    obs = None
    if live:
        obs_dir = root / 'observations'
        obs_dir.mkdir()
        head = dict(version=1, kind='view_state_observation_header', plan_sha256=plan_pin, source_commit='e' * 40,
                    station_id='DEMO-station', control_session_id=SESSION, public_session_id='9' * 32,
                    scene_sha256=plan['scene_sha256'], snapshot_sha256=plan['snapshot']['sha256'],
                    source_clock_id='8' * 32, source_clock_kind='host_monotonic_ns',
                    limits=dict(max_records=4096, max_serialized_bytes=256 * 1024 * 1024, max_seconds=900))
        raw = b''.join(v.canonical(x) for x in [head, *observations])
        entry = save(obs_dir, 'source-observations.jsonl', raw, True)
        manifest = dict(version=1, kind='view_state_observation_manifest', plan_sha256=plan_pin, source_commit='e' * 40,
                        complete=True, fault=None, observation_count=160, reset_reply_count=160,
                        files=dict(observations=dict(entry, bytes=len(raw)), reset_replies=None, commands=None))
        obs = (obs_dir / 'manifest.json', save(obs_dir, 'manifest.json', manifest)['sha256'])
    return run, status_pin, rows, obs


def command_log(root, session=SESSION):
    events = []
    for i, (action, target) in enumerate(v.PAIRS, 1):
        request = dict(version=1, kind='private_command', control_session_id=session, request_id=f'{i + 1000:032x}',
                       command='demo', args=dict(action=action, target=target))
        rejected = reply(i + 1000, False)
        rejected['health']['control_session_id'] = session
        events.append(dict(schema_version='0.3.1', session_id='e' * 32, apparatus_version='DEMO', protocol_version='DEMO',
                           clock_id='DEMO', event_type='private_command_result', event_seq=i, host_mono_ms=i, sim_time=0,
                           payload=dict(station_id='DEMO-station', mode='test', client='DEMO-client',
                                        raw_command=v.canonical(request).decode(), command='demo',
                                        arguments=request['args'], reply=rejected)))
    raw = b''.join(v.canonical(e) for e in events)
    (root / 'lock-commands.jsonl').write_bytes(raw)
    return root / 'lock-commands.jsonl', v.sha(raw)


@pytest.fixture
def private(tmp_path):
    root = tmp_path / '.local'
    root.mkdir()
    return root


@pytest.mark.parametrize('source_kind', ['snapshot', 'live'])
def test_assembled_unity_run_passes_offline_verifier(private, source_kind):
    plan, plan_pin = plan_for(private, source_kind)
    run, status_pin, _, obs = unity_run(private, plan, plan_pin)
    commands, commands_pin = command_log(private)
    path, pin = a.assemble(private / 'plan.json', plan_pin, run, status_pin, commands, commands_pin,
                           *(obs if obs else (None, None)))
    result = v.verify(private / 'plan.json', plan_pin, path, pin)
    assert result['screen_passed'] and result['capture_count'] == 160 and result['comparison_count'] == 2480
    assert result['lock_rejections_verified'] == 32 and result['participant_ready'] is False
    manifest = json.loads(path.read_bytes())
    assert all(set(row) == set(a.ROW_KEYS.split()) for row in manifest['captures'])
    if source_kind == 'live':
        assert json.loads((run / 'c001-state.json').read_bytes()) == NEUTRAL['state']
    with pytest.raises(ValueError, match='ASSEMBLY_OUTPUT_EXISTS'):
        a.assemble(private / 'plan.json', plan_pin, run, status_pin, commands, commands_pin, *(obs if obs else (None, None)))


@pytest.mark.parametrize('fault,code', [('incomplete', 'RUN_INCOMPLETE'), ('row_removed', 'RUN_ROW_COUNT'),
    ('wrong_session', 'LOCK_SESSION_MISMATCH'), ('lineage', 'OBSERVATION_RESET_LINEAGE'),
    ('projection', 'APPLIED_PROJECTION_MISMATCH'), ('missing_observations', 'OBSERVATION_ARGUMENTS')])
def test_assembly_refuses_incomplete_or_unbound_evidence(private, fault, code):
    plan, plan_pin = plan_for(private, 'snapshot' if fault in ('incomplete', 'row_removed', 'wrong_session') else 'live')
    run, status_pin, rows, obs = unity_run(private, plan, plan_pin)
    commands, commands_pin = command_log(private, 'b' * 32 if fault == 'wrong_session' else SESSION)
    status = json.loads((run / 'status.json').read_bytes())
    if fault == 'incomplete':
        status.update(complete=False, fault='RESET_ACK_TIMEOUT')
    if fault == 'row_removed':
        status['rows'] = save(run, 'rows-short.jsonl', b''.join(v.canonical(r) for r in rows[:-1]), True)
    if fault in ('incomplete', 'row_removed'):
        (run / 'status.json').unlink()
        status_pin = save(run, 'status.json', status)['sha256']
    if fault in ('lineage', 'projection'):
        obs_dir = obs[0].parent
        lines = (obs_dir / 'source-observations.jsonl').read_bytes().splitlines()
        row = json.loads(lines[1])
        if fault == 'lineage':
            row['reset_request_id'] = f'{999:032x}'
        else:
            row['frame']['joint_positions'][0] += .1
            row['frame_utf8'] = json.dumps(row['frame'], separators=(',', ':'))
            row['frame_sha256'] = v.sha(row['frame_utf8'].encode())
        lines[1] = v.canonical(row).rstrip(b'\n')
        raw = b'\n'.join(lines) + b'\n'
        (obs_dir / 'source-observations.jsonl').write_bytes(raw)
        manifest = json.loads(obs[0].read_bytes())
        manifest['files']['observations'] = dict(path='source-observations.jsonl', sha256=v.sha(raw), bytes=len(raw))
        obs = (obs[0], save(obs_dir, 'manifest.json', manifest)['sha256'])
    if fault == 'missing_observations':
        obs = (None, None)
    with pytest.raises(ValueError, match=code):
        a.assemble(private / 'plan.json', plan_pin, run, status_pin, commands, commands_pin, *(obs if obs else (None, None)))
    assert not (run / 'captures.json').exists()


def test_actual_unity_editmode_export_assembles_and_verifies():
    """Cross-check a run written by the Unity EditMode synthetic test.

    Set AV_VIEW_CAPTURE_UNITY_EXPORT to the fresh directory that the Unity test
    received as AV_VIEW_CAPTURE_EXPORT. The Unity run is synthetic, not a capture.
    """
    exported = os.environ.get('AV_VIEW_CAPTURE_UNITY_EXPORT')
    if not exported:
        pytest.skip('No Unity EditMode synthetic export supplied')
    root = Path(exported)
    run = root / 'evidence' / 'view-capture-synthetic'
    plan_pin = v.sha((root / 'plan.json').read_bytes())
    status_pin = v.sha((run / 'status.json').read_bytes())
    commands, commands_pin = command_log(root)
    path, pin = a.assemble(root / 'plan.json', plan_pin, run, status_pin, commands, commands_pin)
    result = v.verify(root / 'plan.json', plan_pin, path, pin)
    assert result['screen_passed'] and result['capture_count'] == 160 and result['comparison_count'] == 2480
