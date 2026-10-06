"""Offline, non-admitting O6.1.2 checks over pinned actual capture bytes.

This tool never captures a frame, drives a trial, sends a command or signs a
review. Missing evidence is an error, not a synthesized neutral measurement.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import itertools
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from isaac.reset.manager import Tolerances, angle
from isaac.reset.snapshot import validate_snapshot, validate_state, quaternion, vector
from tools.leakage_png import decode_png, compare_pixels, need

HASH = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
GUID = re.compile(r"[0-9a-f]{32}\Z")
PAIRS = tuple((a, 'tray_' + t) for a in ('ADD_ONE', 'REMOVE_ONE', 'FLIP_CARD', 'ALIGN_ARROW') for t in 'ABCD') + tuple(
    (a, 'container_' + t) for a in ('SCAN', 'TAG', 'CLOSE', 'QUARANTINE') for t in 'EFGH')
DEFAULT_TOLERANCES = asdict(Tolerances())
MAX_FILE = 80 * 1024 * 1024


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def closed(value, keys):
    need(isinstance(value, dict) and set(value) == set(keys.split()), 'CLOSED_FIELDS')
    return value


def finite(value, maximum=None):
    need(type(value) in (int, float) and math.isfinite(value) and value >= 0 and
         (maximum is None or value <= maximum), 'FINITE_RANGE')
    return value


def stamp(value):
    need(isinstance(value, str) and re.fullmatch(r'[0-9]{1,20}', value), 'MONOTONIC_NS')
    return int(value)


def parse(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            need(k not in result, 'DUPLICATE_KEY'); result[k] = v
        return result
    def walk(value, depth=0):
        need(depth <= 30, 'JSON_DEPTH')
        if isinstance(value, float): need(math.isfinite(value), 'NONFINITE_JSON')
        if isinstance(value, (dict, list)):
            for child in value.values() if isinstance(value, dict) else value: walk(child, depth + 1)
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
        walk(value)
        return value
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError('INVALID_JSON') from error


def local(path, missing=False):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not str(path).startswith(('\\\\', '//')), 'LOCAL_ABSOLUTE_PATH')
    if os.name == 'nt':
        import ctypes
        need(':' not in str(path)[2:] and ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 4, 'LOCAL_DRIVE')
    for entry in reversed([path, *path.parents]):
        try: info = entry.lstat()
        except FileNotFoundError:
            need(entry == path and missing, 'MISSING_FILE'); return path
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400, 'LINK_FORBIDDEN')
        if entry != path: need(stat.S_ISDIR(info.st_mode), 'DIRECTORY_REQUIRED')
        elif not stat.S_ISDIR(info.st_mode):
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'REGULAR_UNLINKED_FILE')
    return path


def read(path, pin, limit=MAX_FILE):
    need(isinstance(pin, str) and HASH.fullmatch(pin), 'SHA256_REQUIRED')
    path = local(path); before = path.stat()
    need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, 'FILE_BOUND')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno()); raw = stream.read(limit + 1); after = os.fstat(stream.fileno())
    last = local(path).stat()
    need(len({(x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns) for x in (before, opened, after, last)}) == 1 and len(raw) == before.st_size, 'FILE_CHANGED')
    need(sha(raw) == pin, 'FILE_HASH')
    return raw


def reference(root, value, limit=MAX_FILE):
    closed(value, 'path sha256')
    name = value['path']
    need(isinstance(name, str) and name and '\\' not in name and ':' not in name, 'RELATIVE_REFERENCE')
    relative = Path(name)
    need(not relative.is_absolute() and '..' not in relative.parts, 'RELATIVE_REFERENCE')
    return read(root / relative, value['sha256'], limit)


def pose(value):
    closed(value, 'position_m rotation_xyzw')
    vector(value['position_m'], 3, 'head'); quaternion(value['rotation_xyzw'], 'head')


def load_plan(path, pin):
    raw = read(path, pin, 65536); p = parse(raw)
    closed(p, 'version scope participants source_kind capture_surface station_id build_sha256 scene_sha256 snapshot image poses head_position_tolerance_m head_orientation_tolerance_rad state_tolerances panel_state_sha256 max_state_arrival_age_ms')
    need(type(p['version']) is int and p['version'] == 1 and p['scope'] == 'engineering_provisional' and p['participants'] is False, 'PLAN_SCOPE')
    need(p['source_kind'] in ('live', 'snapshot') and p['capture_surface'] in ('headset', 'desktop_engineering'), 'CAPTURE_SOURCE')
    need(isinstance(p['station_id'], str) and ID.fullmatch(p['station_id']), 'STATION_ID')
    for k in ('build_sha256', 'scene_sha256', 'panel_state_sha256'):
        need(isinstance(p[k], str) and HASH.fullmatch(p[k]), 'PLAN_HASH')
        need(p[k] != '0' * 64, 'UNCONFIGURED_PLAN_HASH')
    closed(p['image'], 'width height max_channel_delta minimum_unique_rgb_colors')
    for k in ('width', 'height'): need(type(p['image'][k]) is int and 0 < p['image'][k] <= 8192, 'IMAGE_DIMENSION')
    # At most 512 MiB of decoded RGBA for the 32 images in one pose group.
    need(p['image']['width'] * p['image']['height'] <= 4_194_304, 'IMAGE_DIMENSION')
    need(type(p['image']['max_channel_delta']) is int and p['image']['max_channel_delta'] == 0, 'ZERO_PIXEL_TOLERANCE_REQUIRED')
    need(type(p['image']['minimum_unique_rgb_colors']) is int and 2 <= p['image']['minimum_unique_rgb_colors'] <= 256, 'NONBLANK_SCREEN')
    finite(p['head_position_tolerance_m'], .001); finite(p['head_orientation_tolerance_rad'], math.radians(.5))
    finite(p['max_state_arrival_age_ms'], 250)
    closed(p['state_tolerances'], ' '.join(DEFAULT_TOLERANCES))
    for k, value in p['state_tolerances'].items(): finite(value, DEFAULT_TOLERANCES[k])
    need(isinstance(p['poses'], list) and len(p['poses']) == 5, 'FIVE_POSES_REQUIRED')
    seen = set()
    for item in p['poses']:
        closed(item, 'id head_pose'); pose(item['head_pose'])
        need(isinstance(item['id'], str) and ID.fullmatch(item['id']) and item['id'] not in seen, 'POSE_ID')
        seen.add(item['id'])
    need(seen == {'center', 'yaw_left', 'yaw_right', 'pitch_up', 'pitch_down'}, 'FIVE_POSE_IDS_REQUIRED')
    for a, b in itertools.combinations(p['poses'], 2):
        left, right = a['head_pose'], b['head_pose']
        need(math.dist(left['position_m'], right['position_m']) > p['head_position_tolerance_m'] or
             angle(left['rotation_xyzw'], right['rotation_xyzw']) > 2*p['head_orientation_tolerance_rad'], 'POSES_NOT_DISTINCT')
    snapshot = parse(reference(Path(path).parent, p['snapshot'], 2 * 1024 * 1024)); validate_snapshot(snapshot)
    need(snapshot['scene_sha256'] == p['scene_sha256'], 'SCENE_BINDING')
    return p, snapshot


def state_differences(actual, wanted, tolerances):
    validate_state(actual); validate_state(wanted)
    errors = []
    def visit(a, b, path):
        if isinstance(b, dict):
            if not isinstance(a, dict) or set(a) != set(b): errors.append({'path': path, 'reason': 'registry'}); return
            for key in b: visit(a[key], b[key], path + '/' + key)
            return
        key = path.rsplit('/', 1)[-1]
        if '/state/' in path or key == 'joint_names' or type(b) in (str, bool):
            if type(a) is not type(b) or a != b: errors.append({'path': path, 'reason': 'exact'})
            return
        category = 'environment_absolute'
        if 'rotation_xyzw' in key: delta, category = angle(a, b), 'orientation_rad'
        elif key in ('position_m', 'root_position_m'): delta, category = math.dist(a, b), 'position_m'
        else:
            if key == 'joint_positions_rad': category = 'joint_rad'
            elif 'angular_velocity' in key or key == 'joint_velocities_rad_s': category = 'angular_velocity_rad_s'
            elif 'linear_velocity' in key: category = 'linear_velocity_m_s'
            if isinstance(b, list):
                if not isinstance(a, list) or len(a) != len(b): errors.append({'path': path, 'reason': 'shape'}); return
                delta = max(abs(x-y) for x, y in zip(a, b))
            else: delta = abs(a-b)
        if delta > tolerances[category]: errors.append({'path': path, 'reason': category, 'deviation': delta})
    visit(actual, wanted, 'capture')
    return errors


def routing(value):
    closed(value, 'spatial_blend pan_stereo doppler_level spread volume pitch mute enabled loop output_mixer_group route_id source_count effects')
    for k in ('spatial_blend', 'pan_stereo', 'doppler_level', 'spread'): need(type(value[k]) in (int, float) and value[k] == 0, 'SPATIAL_ROUTING')
    need(value['pitch'] == 1 and type(value['pitch']) in (int, float), 'AUDIO_PITCH')
    finite(value['volume'], 1)
    need(value['mute'] is False and value['enabled'] is True and value['loop'] is False and
         type(value['source_count']) is int and value['source_count'] == 1 and value['effects'] == [], 'AUDIO_ROUTE_STATE')
    need(all(isinstance(value[k], str) and len(value[k]) <= 160 for k in ('output_mixer_group', 'route_id')) and value['route_id'], 'AUDIO_ROUTE_ID')


def view(value, p):
    closed(value, 'panel_state_sha256 visible_text renderer_inventory_sha256 console_visible diagnostics_visible')
    need(value['panel_state_sha256'] == p['panel_state_sha256'] and value['console_visible'] is False and value['diagnostics_visible'] is False, 'VISIBLE_OVERLAY_OR_PANEL')
    need(isinstance(value['renderer_inventory_sha256'], str) and HASH.fullmatch(value['renderer_inventory_sha256']), 'VISIBLE_REGISTRY_HASH')
    need(isinstance(value['visible_text'], list) and len(value['visible_text']) <= 256 and
         all(isinstance(t, str) and len(t) <= 512 for t in value['visible_text']), 'VISIBLE_TEXT')


def reply_shape(reply):
    closed(reply, 'version kind request_id accepted reason mode host_mono_ms sim_time reset_ok duplicate health')
    need(type(reply['version']) is int and reply['version'] == 1 and reply['kind'] == 'private_reply', 'REPLY_VERSION')
    need(isinstance(reply['request_id'], str) and GUID.fullmatch(reply['request_id']), 'REPLY_ID')
    need(type(reply['accepted']) is bool and type(reply['duplicate']) is bool and
         (reply['reset_ok'] is None or type(reply['reset_ok']) is bool), 'REPLY_FLAGS')
    need(reply['mode'] in ('teaching', 'test', 'post_endpoint') and isinstance(reply['reason'], str) and
         re.fullmatch(r'[A-Z0-9_]{1,80}', reply['reason']), 'REPLY_MODE_REASON')
    finite(reply['host_mono_ms']); finite(reply['sim_time'])
    h = reply['health']
    closed(h, 'control_session_id mode paused stopped fault demo_active publisher_ready exposure_ready public_stream_recovered neutral_verification_age_ms publisher_age_ms health_sample_host_mono_ms')
    need(isinstance(h['control_session_id'], str) and GUID.fullmatch(h['control_session_id']) and h['mode'] == reply['mode'], 'HEALTH_SESSION')
    for key in ('paused', 'stopped', 'demo_active', 'publisher_ready', 'exposure_ready'):
        need(type(h[key]) is bool, 'HEALTH_BOOLEAN')
    need(h['public_stream_recovered'] is False and (h['fault'] is None or isinstance(h['fault'], str)), 'HEALTH_FAULT')
    for key in ('neutral_verification_age_ms', 'publisher_age_ms'):
        if h[key] is not None: finite(h[key])
    finite(h['health_sample_host_mono_ms'])


def verify_rejections(raw, station_id):
    seen, requests, session, last_seq = set(), set(), None, -1
    rows = raw.splitlines(); need(0 < len(rows) <= 4096, 'COMMAND_LOG_BOUND')
    for line in rows:
        event = parse(line)
        need(isinstance(event, dict), 'COMMAND_EVENT')
        if event.get('event_type') != 'private_command_result': continue
        closed(event, 'schema_version session_id apparatus_version protocol_version clock_id event_type event_seq host_mono_ms sim_time payload')
        need(event['schema_version'] == '0.3.1' and isinstance(event['session_id'], str) and GUID.fullmatch(event['session_id']), 'COMMAND_LOG_VERSION')
        finite(event['host_mono_ms']); finite(event['sim_time'])
        p = event.get('payload'); need(isinstance(p, dict), 'COMMAND_PAYLOAD')
        closed(p, 'station_id mode client raw_command command arguments reply')
        request = parse(p.get('raw_command', ''))
        if not isinstance(request, dict) or request.get('command') != 'demo': continue
        closed(request, 'version kind control_session_id request_id command args')
        closed(request['args'], 'action target')
        pair = (request['args']['action'], request['args']['target'])
        need(pair in PAIRS and pair not in seen, 'LOCK_MATRIX_DUPLICATE_OR_ILLEGAL')
        need(type(request['version']) is int and request['version'] == 1 and request['kind'] == 'private_command', 'COMMAND_VERSION')
        rid = request['request_id']; sid = request['control_session_id']
        need(isinstance(rid, str) and GUID.fullmatch(rid) and rid not in requests and isinstance(sid, str) and GUID.fullmatch(sid), 'COMMAND_ID')
        if session is None: session = sid
        need(session == sid and p.get('station_id') == station_id and p.get('mode') == 'test' and p.get('command') == 'demo' and p.get('arguments') == request['args'], 'LOCK_BINDING')
        reply = p.get('reply'); reply_shape(reply)
        need(reply.get('request_id') == rid and reply.get('accepted') is False and reply.get('duplicate') is False and reply.get('mode') == 'test' and reply.get('reason') == 'PROTECTED_TARGET_COMMAND', 'LOCK_NOT_REJECTED')
        need(reply.get('health', {}).get('control_session_id') == session, 'LOCK_REPLY_SESSION')
        seq = event.get('event_seq'); need(type(seq) is int and seq > last_seq, 'COMMAND_EVENT_ORDER')
        last_seq = seq; seen.add(pair); requests.add(rid)
    need(seen == set(PAIRS), 'LOCK_MATRIX_INCOMPLETE')
    return len(seen), session


def verify(plan_path, plan_pin, manifest_path, manifest_pin):
    p, neutral = load_plan(plan_path, plan_pin)
    raw = read(manifest_path, manifest_pin, 4 * 1024 * 1024); m = parse(raw); root = Path(manifest_path).parent
    closed(m, 'version scope plan_sha256 station_id clock_id plan_seen_ns captures command_log')
    need(type(m['version']) is int and m['version'] == 1 and m['scope'] == 'actual_nonstudy_capture' and m['plan_sha256'] == plan_pin and m['station_id'] == p['station_id'], 'CAPTURE_BINDING')
    need(isinstance(m['clock_id'], str) and GUID.fullmatch(m['clock_id']), 'CAPTURE_CLOCK')
    start = stamp(m['plan_seen_ns']); rows = m['captures']
    need(isinstance(rows, list) and len(rows) == 32 * len(p['poses']), 'CAPTURE_MATRIX_COUNT')
    poses = {v['id']: v['head_pose'] for v in p['poses']}
    seen, images, records, failures = set(), set(), {}, []
    capture_ids, reset_ids, state_paths, control_sessions = set(), set(), set(), set()
    last_time = start
    for row in rows:
        closed(row, 'capture_id pose_id action target head_pose reset_received_ns captured_ns cue_requested_ns state_sample_ns reset_reply image state audio view')
        key = (row['pose_id'], row['action'], row['target'])
        need(row['pose_id'] in poses and key[1:] in PAIRS and key not in seen, 'CAPTURE_MATRIX_KEY')
        need(isinstance(row['capture_id'], str) and GUID.fullmatch(row['capture_id']) and row['capture_id'] not in capture_ids, 'CAPTURE_ID')
        capture_ids.add(row['capture_id'])
        now = stamp(row['captured_ns']); reset_time = stamp(row['reset_received_ns']); source_time = stamp(row['state_sample_ns'])
        need(start <= reset_time <= now < stamp(row['cue_requested_ns']) and now > last_time and start <= source_time <= now, 'PRE_CUE_ORDER')
        need((now - source_time) / 1e6 <= p['max_state_arrival_age_ms'], 'STATE_ARRIVAL_AGE')
        last_time = now; pose(row['head_pose'])
        intended = poses[row['pose_id']]
        need(math.dist(row['head_pose']['position_m'], intended['position_m']) <= p['head_position_tolerance_m'] and angle(row['head_pose']['rotation_xyzw'], intended['rotation_xyzw']) <= p['head_orientation_tolerance_rad'], 'HEAD_POSE_MISMATCH')
        reply = parse(reference(root, row['reset_reply'], 65536))
        reply_shape(reply)
        need(isinstance(reply, dict) and reply.get('kind') == 'private_reply' and reply.get('accepted') is True and reply.get('reset_ok') is True and reply.get('mode') == 'test' and reply.get('duplicate') is False and reply.get('reason') in ('RESET_COMPLETE', 'MODE_CHANGED'), 'RESET_NOT_VERIFIED')
        need(isinstance(reply.get('request_id'), str) and GUID.fullmatch(reply['request_id']), 'RESET_ID')
        need(reply['request_id'] not in reset_ids and reply['health']['fault'] is None and
             reply['health']['exposure_ready'] is True and not reply['health']['paused'] and
             not reply['health']['stopped'] and not reply['health']['demo_active'], 'RESET_HEALTH_OR_REUSE')
        reset_ids.add(reply['request_id']); control_sessions.add(reply['health']['control_session_id'])
        image_ref = row['image']; closed(image_ref, 'path sha256')
        need(image_ref['path'] not in images, 'REUSED_CAPTURE_PATH'); images.add(image_ref['path'])
        # Decode one pose at a time below, keeping large image storage bounded.
        closed(row['state'], 'path sha256')
        need(row['state']['path'] not in state_paths, 'REUSED_STATE_CAPTURE_PATH'); state_paths.add(row['state']['path'])
        state = parse(reference(root, row['state'], 2 * 1024 * 1024)); validate_state(state)
        errors = state_differences(state, neutral['state'], p['state_tolerances'])
        audio = parse(reference(root, row['audio'], 65536)); routing(audio)
        visible = parse(reference(root, row['view'], 256 * 1024)); view(visible, p)
        if errors: failures.append({'capture_id': row['capture_id'], 'reason': 'NOT_NEUTRAL', 'differences': errors})
        records[key] = (row, state, audio, visible); seen.add(key)
    lock_count, session = verify_rejections(reference(root, m['command_log'], 8 * 1024 * 1024), p['station_id'])
    need(control_sessions == {session}, 'CAPTURE_CONTROL_SESSION')
    comparisons = []
    baseline_audio = next(iter(records.values()))[2]
    for record in records.values():
        if record[2] != baseline_audio: failures.append({'capture_id': record[0]['capture_id'], 'reason': 'ROUTING_DIFFERS'})
    for pose_id in poses:
        decoded = {}
        for pair in PAIRS:
            row = records[(pose_id, *pair)][0]
            image = decode_png(reference(root, row['image']))
            need((image['width'], image['height']) == (p['image']['width'], p['image']['height']), 'CAPTURE_DIMENSIONS')
            pixels, stride = image['pixels'], image['channels']; colors = set()
            for i in range(0, len(pixels), stride):
                # Fully transparent pixels cannot establish visible scene content.
                if stride == 3 or pixels[i + 3] != 0: colors.add(pixels[i:i + 3])
                if len(colors) >= p['image']['minimum_unique_rgb_colors']: break
            need(len(colors) >= p['image']['minimum_unique_rgb_colors'], 'BLANK_OR_UNDERDETAILED_CAPTURE')
            decoded[pair] = image
        for a, b in itertools.combinations(PAIRS, 2):
            left, right = records[(pose_id, *a)], records[(pose_id, *b)]
            pixel = compare_pixels(decoded[a], decoded[b], p['image']['max_channel_delta'])
            state = state_differences(left[1], right[1], p['state_tolerances'])
            same_view = left[3] == right[3]
            ah, bh = left[0]['head_pose'], right[0]['head_pose']
            matched_head = (math.dist(ah['position_m'], bh['position_m']) <= p['head_position_tolerance_m'] and
                            angle(ah['rotation_xyzw'], bh['rotation_xyzw']) <= p['head_orientation_tolerance_rad'])
            passed = pixel['within_tolerance'] and not state and same_view and left[2] == right[2] and matched_head
            comparisons.append({'pose_id': pose_id, 'left': left[0]['capture_id'], 'right': right[0]['capture_id'], 'within_tolerance': passed,
                                'pixels': pixel, 'state_differences': state, 'view_equal': same_view, 'routing_equal': left[2] == right[2], 'matched_head_pose': matched_head})
    passed = not failures and all(c['within_tolerance'] for c in comparisons)
    return {'version': 1, 'scope': 'offline_actual_capture_screen', 'plan_sha256': plan_pin, 'manifest_sha256': manifest_pin,
            'screen_passed': passed, 'capture_count': len(rows), 'pose_count': len(poses), 'comparison_count': len(comparisons),
            'lock_rejections_verified': lock_count, 'failures': failures, 'comparisons': comparisons, 'g3_signed': False,
            'participant_ready': False, 'recommendation': 'HOLD', 'pending': [
                'Independent code-path review and closure of every finding',
                'Independent custody proof that the pinned plan preceded capture',
                'Capture provenance, rendered-state alignment and no omitted renderer/overlay review',
                'Station/headset/source qualification and natural movement coverage',
                'Complete robot asset appearance coverage beyond registered workcell materials/lights',
                'Acoustic routing confirmation; saved AudioSource settings are not an acoustic measurement']}


def write_report(path, value):
    path = local(path, missing=True)
    need(any(p.lower() in ('.local', 'private', 'local-data') for p in path.parent.parts[2:]), 'PRIVATE_OUTPUT_REQUIRED')
    with path.open('xb') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())


def write_comparison_csv(path, result):
    path = local(path, missing=True)
    need(any(p.lower() in ('.local', 'private', 'local-data') for p in path.parent.parts[2:]), 'PRIVATE_OUTPUT_REQUIRED')
    fields = ('pose_id', 'left_capture_id', 'right_capture_id', 'pixels_equal', 'state_within_tolerance',
              'view_equal', 'routing_equal', 'matched_head_pose', 'within_tolerance', 'state_differences_json')
    output = io.StringIO(newline=''); writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    for item in result['comparisons']:
        writer.writerow(dict(pose_id=item['pose_id'], left_capture_id=item['left'], right_capture_id=item['right'],
            pixels_equal=item['pixels']['within_tolerance'], state_within_tolerance=not item['state_differences'],
            view_equal=item['view_equal'], routing_equal=item['routing_equal'], matched_head_pose=item['matched_head_pose'],
            within_tolerance=item['within_tolerance'], state_differences_json=json.dumps(item['state_differences'], separators=(',', ':'), allow_nan=False)))
    with path.open('xb') as stream:
        stream.write(output.getvalue().encode('utf-8')); stream.flush(); os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True); parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--validate-plan', action='store_true')
    parser.add_argument('--captures', type=Path); parser.add_argument('--captures-sha256'); parser.add_argument('--output', type=Path)
    parser.add_argument('--comparison-csv', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.validate_plan:
            p, _ = load_plan(args.plan, args.plan_sha256)
            print(json.dumps({'plan_valid': True, 'captures_required': 32 * len(p['poses']), 'participant_ready': False})); return 0
        need(args.captures is not None and args.captures_sha256 is not None and args.output is not None, 'CAPTURE_ARGUMENTS')
        result = verify(args.plan, args.plan_sha256, args.captures, args.captures_sha256)
        write_report(args.output, result)
        if args.comparison_csv is not None: write_comparison_csv(args.comparison_csv, result)
        print(json.dumps({k: result[k] for k in ('screen_passed', 'capture_count', 'comparison_count', 'recommendation', 'participant_ready')}))
        return 0 if result['screen_passed'] else 1
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        print('VIEW_CAPTURE_EVIDENCE_REFUSED'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
