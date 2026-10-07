"""Assemble the O6.1.2 verifier manifest from one finished Unity capture run.

Inputs are pinned and read-only: the plan, the Unity run status (which pins
its header and row journal), the finalized private #55 command log after the
separate lock probe, and for a live source the finalized Isaac owner-thread
observation manifest. Outputs are created exclusively inside the run
directory: per-capture complete-state files joined from Isaac observations
(live only), an exact command-log copy and ``captures.json``. Nothing is
captured, retried, edited or replaced; any refusal leaves earlier outputs as
diagnostic data. Retry in a fresh copy of the untouched Unity run.

This is an assembly step, not a leakage screen. Run tools/view_leakage.py on
the printed manifest pin afterwards. No result grants G3 or participant use.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from isaac.reset.manager import angle
from tools import view_leakage as v
from tools.leakage_png import need

ROW_KEYS = ('capture_id pose_id action target head_pose reset_received_ns captured_ns cue_requested_ns '
            'state_sample_ns reset_reply image state audio view')
UNITY_ROW_KEYS = ROW_KEYS + ' inventory applied'
STATUS_KEYS = 'version kind complete fault captures_completed captures_required run rows participant_admission g3_signed'
RUN_KEYS = ('version kind scope participant_admission g3_signed plan_sha256 station_id source_kind capture_surface build_sha256 '
            'clock_id clock_kind plan_seen_ns control_session_id simulation_capability_sha256 capture_method state_origin '
            'trial_boundary settings poses legal_pairs captures_required')
APPLIED_KEYS = ('version kind provenance session_id sequence sim_step published_ns sim_time coordinate_frame '
                'joint_positions objects')
OBS_MANIFEST_KEYS = 'version kind plan_sha256 source_commit complete fault observation_count reset_reply_count files'
OBS_HEADER_KEYS = ('version kind plan_sha256 source_commit station_id control_session_id public_session_id scene_sha256 '
                   'snapshot_sha256 source_clock_id source_clock_kind limits')
OBS_ROW_KEYS = ('version kind observation_id observed_host_ns reset_request_id reset_reply_canonical_sha256 '
                'public_session_id sequence sim_step frame_sha256 frame_utf8 frame state')
CONTINUOUS = ('arrow_angle_rad', 'lid_open_fraction')


def write_new(path, raw):
    """Create-new, flush and fsync. An existing name is never replaced."""
    path = v.local(path, missing=True)
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    need(v.sha(path.read_bytes()) == v.sha(raw), 'WRITE_VERIFY')
    return {'path': path.name, 'sha256': v.sha(raw)}


def load_run(run, status_pin, plan, plan_pin):
    status = v.parse(v.read(run / 'status.json', status_pin, 65536))
    v.closed(status, STATUS_KEYS)
    required = 32 * len(plan['poses'])
    need(status['version'] == 1 and status['kind'] == 'view_capture_status', 'RUN_STATUS_KIND')
    need(status['complete'] is True and status['fault'] is None and status['captures_completed'] == required
         and status['captures_required'] == required and status['participant_admission'] is False, 'RUN_INCOMPLETE')
    header = v.parse(v.reference(run, status['run'], 65536))
    v.closed(header, RUN_KEYS)
    need(header['version'] == 1 and header['kind'] == 'view_capture_run' and header['scope'] == 'SIMULATION_TEST'
         and header['participant_admission'] is False and header['plan_sha256'] == plan_pin
         and header['station_id'] == plan['station_id'] and header['source_kind'] == plan['source_kind']
         and header['capture_surface'] == plan['capture_surface'] and header['captures_required'] == required, 'RUN_BINDING')
    need(isinstance(header['clock_id'], str) and v.GUID.fullmatch(header['clock_id'])
         and isinstance(header['control_session_id'], str) and v.GUID.fullmatch(header['control_session_id']), 'RUN_IDENTITY')
    v.stamp(header['plan_seen_ns'])
    lines = v.reference(run, status['rows'], 8 * 1024 * 1024).splitlines()
    rows = [v.parse(line) for line in lines]
    need(len(rows) == required, 'RUN_ROW_COUNT')
    for row in rows:
        v.closed(row, UNITY_ROW_KEYS)
    return header, rows


def projection_matches(applied, frame, tolerances):
    joints, wanted = applied['joint_positions'], frame['joint_positions']
    if not (isinstance(joints, list) and len(joints) == len(wanted)):
        return False
    if any(abs(a - b) > tolerances['joint_rad'] for a, b in zip(joints, wanted)):
        return False
    objects = {o['id']: o for o in applied['objects']}
    if set(objects) != {o['id'] for o in frame['objects']}:
        return False
    for expected in frame['objects']:
        actual = objects[expected['id']]
        if (math.dist(actual['position_m'], expected['position_m']) > tolerances['position_m']
                or angle(actual['rotation_xyzw'], expected['rotation_xyzw']) > tolerances['orientation_rad']
                or actual['visible'] is not expected['visible'] or actual['enabled'] is not expected['enabled']
                or set(actual['state']) != set(expected['state'])):
            return False
        for key, value in expected['state'].items():
            other = actual['state'][key]
            if key in CONTINUOUS:
                if abs(other - value) > 1e-6:
                    return False
            elif type(other) is not type(value) or other != value:
                return False
    return True


def load_observations(path, pin, plan, plan_pin, header):
    manifest = v.parse(v.read(path, pin, 65536))
    v.closed(manifest, OBS_MANIFEST_KEYS)
    need(manifest['version'] == 1 and manifest['kind'] == 'view_state_observation_manifest'
         and manifest['complete'] is True and manifest['fault'] is None and manifest['plan_sha256'] == plan_pin, 'OBSERVATIONS_INCOMPLETE')
    files = manifest['files']
    need(isinstance(files, dict) and set(files) == {'observations', 'reset_replies', 'commands'}, 'OBSERVATION_FILES')
    entry = files['observations']
    v.closed(entry, 'path sha256 bytes')
    raw = v.reference(Path(path).parent, {'path': entry['path'], 'sha256': entry['sha256']}, 256 * 1024 * 1024)
    need(len(raw) == entry['bytes'], 'OBSERVATION_FILES')
    lines = raw.splitlines()
    need(len(lines) >= 2, 'OBSERVATIONS_EMPTY')
    head = v.parse(lines[0])
    v.closed(head, OBS_HEADER_KEYS)
    need(head['kind'] == 'view_state_observation_header' and head['plan_sha256'] == plan_pin
         and head['station_id'] == plan['station_id'] and head['control_session_id'] == header['control_session_id']
         and head['scene_sha256'] == plan['scene_sha256'] and head['snapshot_sha256'] == plan['snapshot']['sha256'], 'OBSERVATION_BINDING')
    index = {}
    for line in lines[1:]:
        row = v.parse(line)
        v.closed(row, OBS_ROW_KEYS)
        need(row['kind'] == 'view_state_observation' and row['public_session_id'] == head['public_session_id'], 'OBSERVATION_ROW')
        need(v.sha(row['frame_utf8'].encode('utf-8')) == row['frame_sha256'] and v.parse(row['frame_utf8']) == row['frame'], 'OBSERVATION_FRAME')
        key = (row['public_session_id'], row['sequence'])
        need(key not in index, 'OBSERVATION_DUPLICATE')
        index[key] = row
    return index


def assemble(plan_path, plan_pin, run, status_pin, commands_path, commands_pin, observations=None, observations_pin=None):
    plan, _ = v.load_plan(plan_path, plan_pin)
    run = v.local(run)
    header, rows = load_run(run, status_pin, plan, plan_pin)
    live = plan['source_kind'] == 'live'
    need((observations is not None) == live and (observations_pin is not None) == live, 'OBSERVATION_ARGUMENTS')
    index = load_observations(observations, observations_pin, plan, plan_pin, header) if live else None
    commands = v.read(commands_path, commands_pin, 8 * 1024 * 1024)
    _, session = v.verify_rejections(commands, plan['station_id'])
    need(session == header['control_session_id'], 'LOCK_SESSION_MISMATCH')
    for name in ('captures.json', 'commands.jsonl'):
        need(not (run / name).exists(), 'ASSEMBLY_OUTPUT_EXISTS')
    captures = []
    for row in rows:
        reply = v.parse(v.reference(run, row['reset_reply'], 65536))
        if live:
            need(row['state'] is None and isinstance(row['applied'], dict), 'LIVE_STATE_JOIN_REQUIRED')
            applied = v.parse(v.reference(run, row['applied'], 2 * 1024 * 1024))
            v.closed(applied, APPLIED_KEYS)
            need(applied['kind'] == 'view_capture_applied_frame' and applied['provenance'] == 'live', 'APPLIED_FRAME')
            observation = index.get((applied['session_id'], applied['sequence']))
            need(observation is not None and observation['sim_step'] == applied['sim_step'], 'OBSERVATION_MISSING')
            # The observed sample must descend from this capture's own reset.
            need(observation['reset_request_id'] == reply['request_id'], 'OBSERVATION_RESET_LINEAGE')
            need(projection_matches(applied, observation['frame'], plan['state_tolerances']), 'APPLIED_PROJECTION_MISMATCH')
            name = row['applied']['path']
            need(name.endswith('-applied.json'), 'APPLIED_FRAME')
            state_ref = write_new(run / (name[:-len('-applied.json')] + '-state.json'), v.canonical(observation['state']))
        else:
            need(isinstance(row['state'], dict) and row['applied'] is None, 'SNAPSHOT_STATE_REQUIRED')
            state_ref = row['state']
        item = {key: row[key] for key in ROW_KEYS.split()}
        item['state'] = state_ref
        captures.append(item)
    log = write_new(run / 'commands.jsonl', commands)
    manifest = dict(version=1, scope='actual_nonstudy_capture', plan_sha256=plan_pin, station_id=plan['station_id'],
                    clock_id=header['clock_id'], plan_seen_ns=header['plan_seen_ns'], captures=captures, command_log=log)
    result = write_new(run / 'captures.json', v.canonical(manifest))
    return run / 'captures.json', result['sha256']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plan', type=Path, required=True); parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--run', type=Path, required=True); parser.add_argument('--status-sha256', required=True)
    parser.add_argument('--commands', type=Path, required=True); parser.add_argument('--commands-sha256', required=True)
    parser.add_argument('--observations', type=Path); parser.add_argument('--observations-sha256')
    args = parser.parse_args(argv)
    try:
        path, pin = assemble(args.plan, args.plan_sha256, args.run, args.status_sha256, args.commands,
                             args.commands_sha256, args.observations, args.observations_sha256)
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        print('VIEW_CAPTURE_ASSEMBLY_REFUSED'); return 2
    print(json.dumps({'captures': str(path), 'captures_sha256': pin, 'screen_run': False, 'participant_ready': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
