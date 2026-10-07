"""Synthetic PNG/journal fixtures exercise the verifier, never station acceptance."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import view_leakage as v
from tools.leakage_png import decode_png, compare_pixels


def chunk(kind, raw):
    return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', zlib.crc32(kind + raw) & 0xffffffff)


def png(pixels=None, method=0, channels=4, width=4, height=2):
    pixels = pixels or bytes([0, 0, 0, 255, 200, 0, 0, 255, 0, 200, 0, 255, 0, 0, 200, 255] * 2)
    if channels == 3 and len(pixels) == width * height * 4:
        pixels = b''.join(pixels[i:i+3] for i in range(0, len(pixels), 4))
    stride = width * channels; filtered = bytearray(); previous = bytes(stride)
    for y in range(height):
        row = pixels[y*stride:(y+1)*stride]; data = bytearray(row)
        for x in range(stride):
            a = row[x-channels] if x >= channels else 0; b = previous[x]; c = previous[x-channels] if x >= channels else 0
            p = a+b-c; aa, bb, cc = abs(p-a), abs(p-b), abs(p-c)
            predictor = (0, a, b, (a+b)//2, a if aa <= bb and aa <= cc else b if bb <= cc else c)[method]
            data[x] = (row[x]-predictor) & 255
        filtered.extend(bytes([method])+data); previous = row
    header = struct.pack('>IIBBBBB', width, height, 8, 2 if channels == 3 else 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(filtered)) + chunk(b'IEND', b'')


@pytest.mark.parametrize('method', range(5))
@pytest.mark.parametrize('channels', (3, 4))
def test_all_lossless_png_filters(method, channels):
    baseline = decode_png(png(channels=channels))
    actual = decode_png(png(method=method, channels=channels))
    assert actual == baseline


def test_png_crc_trailing_animation_and_bomb_rejected():
    raw = png(); changed = bytearray(raw); changed[-6] ^= 1
    for bad in (bytes(changed), raw+b'x', raw[:-12], raw[:33]+chunk(b'acTL', b'12345678')+raw[33:]):
        with pytest.raises(ValueError): decode_png(bad)
    header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
    bomb = raw[:8]+header+chunk(b'IDAT', zlib.compress(bytes(1000000)))+chunk(b'IEND', b'')
    with pytest.raises(ValueError, match='DECODE_SIZE'): decode_png(bomb)


def test_pixel_bound_includes_alpha_and_exact_boundary():
    a = decode_png(png()); data = bytearray(a['pixels']); data[7] -= 2
    b = decode_png(png(bytes(data)))
    assert not compare_pixels(a, b, 1)['within_tolerance']
    assert compare_pixels(a, b, 2)['within_tolerance']
    assert compare_pixels(a, b, 1)['first_exceedance']['channel'] == 3


def ref(root, name, value, raw=False):
    data = value if raw else v.canonical(value)
    (root/name).write_bytes(data)
    return {'path': name, 'sha256': v.sha(data)}


def reply(index, accepted=True):
    return dict(version=1, kind='private_reply', request_id=f'{index:032x}', accepted=accepted,
        reason='RESET_COMPLETE' if accepted else 'PROTECTED_TARGET_COMMAND', mode='test', host_mono_ms=index,
        sim_time=0, reset_ok=True if accepted else None, duplicate=False, health=dict(control_session_id='a'*32,
        mode='test', paused=False, stopped=False, fault=None, demo_active=False, publisher_ready=True, exposure_ready=True,
        public_stream_recovered=False, neutral_verification_age_ms=0, publisher_age_ms=0, health_sample_host_mono_ms=index))


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path/'.local'; root.mkdir()
    neutral = json.loads((ROOT/'isaac/snapshots/neutral_v1.json').read_bytes())
    pose = {'position_m': [0, 1.2, -2], 'rotation_xyzw': [0, 0, 0, 1]}
    plan = dict(version=1, scope='engineering_provisional', participants=False, source_kind='live',
        capture_surface='desktop_engineering', station_id='DEMO-station', build_sha256='b'*64,
        scene_sha256=neutral['scene_sha256'], snapshot=ref(root, 'neutral.json', neutral),
        image=dict(width=4, height=2, max_channel_delta=0, minimum_unique_rgb_colors=2),
        poses=[dict(id='center', head_pose=pose)], head_position_tolerance_m=.001,
        head_orientation_tolerance_rad=math.radians(.5), state_tolerances=v.DEFAULT_TOLERANCES.copy(),
        panel_state_sha256='c'*64, max_state_arrival_age_ms=250)
    audio = dict(spatial_blend=0, pan_stereo=0, doppler_level=0, spread=0, volume=.25, pitch=1,
        mute=False, enabled=True, loop=False, output_mixer_group='master', route_id='DEMO-route', source_count=1, effects=[])
    view = dict(panel_state_sha256='c'*64, visible_text=['DEMO fixed panel'], renderer_inventory_sha256='d'*64,
        console_visible=False, diagnostics_visible=False)
    records, events = [], []
    for i, (action, target) in enumerate(v.PAIRS, 1):
        records.append(dict(capture_id=f'{i:032x}', pose_id='center', action=action, target=target,
            head_pose=deepcopy(pose), reset_received_ns=str(i*1000), captured_ns=str(i*1000+1),
            cue_requested_ns=str(i*1000+2), state_sample_ns=str(i*1000), reset_reply=ref(root, f'reset-{i}.json', reply(i)),
            image=ref(root, f'image-{i}.png', png(), True), state=ref(root, f'state-{i}.json', neutral['state']),
            audio=ref(root, f'audio-{i}.json', audio), view=ref(root, f'view-{i}.json', view)))
        request = dict(version=1, kind='private_command', control_session_id='a'*32, request_id=f'{i+1000:032x}',
            command='demo', args=dict(action=action, target=target))
        events.append(dict(schema_version='0.3.1', session_id='e'*32, apparatus_version='DEMO', protocol_version='DEMO',
            clock_id='DEMO', event_type='private_command_result', event_seq=i, host_mono_ms=i, sim_time=0,
            payload=dict(station_id='DEMO-station', mode='test', client='DEMO-client', raw_command=v.canonical(request).decode(),
            command='demo', arguments=request['args'], reply=reply(i+1000, False))))
    plan_ref = ref(root, 'plan.json', plan)
    # The production profile always has five distinct views. Every capture gets
    # separate durable bytes and fresh IDs, even when this synthetic scene is equal.
    for pose_index, (name, axis, degrees) in enumerate((('yaw_left', 1, -15), ('yaw_right', 1, 15), ('pitch_up', 0, -10), ('pitch_down', 0, 10)), 1):
        q = [0., 0., 0., math.cos(math.radians(degrees)/2)]
        q[axis] = math.sin(math.radians(degrees)/2)
        head = dict(position_m=pose['position_m'], rotation_xyzw=q)
        plan['poses'].append(dict(id=name, head_pose=head))
        for i, original in enumerate(records[:32], 1):
            index = pose_index*32+i; row = deepcopy(original)
            row.update(capture_id=f'{index:032x}', pose_id=name, head_pose=deepcopy(head),
                reset_received_ns=str(index*1000), captured_ns=str(index*1000+1),
                cue_requested_ns=str(index*1000+2), state_sample_ns=str(index*1000))
            row['reset_reply'] = ref(root, f'reset-{index}.json', reply(index))
            row['image'] = ref(root, f'image-{index}.png', png(), True)
            row['state'] = ref(root, f'state-{index}.json', neutral['state'])
            row['audio'] = ref(root, f'audio-{index}.json', audio)
            row['view'] = ref(root, f'view-{index}.json', view)
            records.append(row)
    plan_ref = ref(root, 'plan.json', plan)
    manifest = dict(version=1, scope='actual_nonstudy_capture', plan_sha256=plan_ref['sha256'], station_id='DEMO-station',
        clock_id='f'*32, plan_seen_ns='1', captures=records, command_log=ref(root, 'commands.jsonl', b''.join(v.canonical(e) for e in events), True))
    return root, plan, manifest, events


def verify(bundle):
    root, p, m, _ = bundle
    p_ref = ref(root, 'plan.json', p); m['plan_sha256'] = p_ref['sha256']; m_ref = ref(root, 'captures.json', m)
    return v.verify(root/'plan.json', p_ref['sha256'], root/'captures.json', m_ref['sha256'])


def test_complete_synthetic_fixture_checks_all_2480_pairs_without_qualification(bundle):
    result = verify(bundle)
    assert result['screen_passed'] and result['capture_count'] == 160 and result['comparison_count'] == 2480
    assert result['lock_rejections_verified'] == 32 and result['recommendation'] == 'HOLD'
    assert result['participant_ready'] is result['g3_signed'] is False
    path = bundle[0]/'comparisons.csv'; v.write_comparison_csv(path, result)
    import csv
    with path.open(newline='') as stream: rows = list(csv.DictReader(stream))
    assert len(rows) == 2480 and all(r['within_tolerance'] == 'True' for r in rows)
    with pytest.raises(FileExistsError): v.write_comparison_csv(path, result)


@pytest.mark.parametrize('mutation', [
    lambda m: m['captures'].pop(),
    lambda m: m['captures'].__setitem__(1, deepcopy(m['captures'][0])),
    lambda m: m['captures'][1].__setitem__('capture_id', m['captures'][0]['capture_id']),
    lambda m: m['captures'][1].__setitem__('image', m['captures'][0]['image']),
    lambda m: m['captures'][1].__setitem__('reset_reply', m['captures'][0]['reset_reply']),
    lambda m: m['captures'][1].__setitem__('state', m['captures'][0]['state']),
    lambda m: m['captures'][0].__setitem__('cue_requested_ns', '1000'),
    lambda m: m['captures'][0].__setitem__('state_sample_ns', '1002'),
    lambda m: m['captures'][0]['head_pose']['position_m'].__setitem__(0, .002),
    lambda m: m.__setitem__('scope', 'synthetic'),
])
def test_incomplete_ambiguous_or_wrong_timing_refused(bundle, mutation):
    mutation(bundle[2])
    with pytest.raises(ValueError): verify(bundle)


@pytest.mark.parametrize('kind', ('pixel', 'state', 'text', 'routing'))
def test_target_conditioned_difference_is_failed_screen(bundle, kind):
    root, _, m, _ = bundle; row = m['captures'][0]
    key = {'pixel': 'image', 'state': 'state', 'text': 'view', 'routing': 'audio'}[kind]
    if kind == 'pixel':
        pixels = bytearray(decode_png(png())['pixels']); pixels[0] = 20
        value = png(bytes(pixels))
    else:
        value = json.loads((root/row[key]['path']).read_bytes())
        if kind == 'state': value['robot']['joint_positions_rad'][0] += .1
        if kind == 'text': value['visible_text'].append('DEMO changed word')
        if kind == 'routing': value['volume'] = .3
    row[key] = ref(root, row[key]['path'], value, kind == 'pixel')
    result = verify(bundle)
    assert not result['screen_passed']
    assert sum(not p['within_tolerance'] for p in result['comparisons']) == 31


@pytest.mark.parametrize('kind', ('accepted', 'unlogged', 'duplicate', 'wrong_session', 'wrong_args'))
def test_each_lock_probe_must_match_rejected_native_event(bundle, kind):
    root, _, m, events = bundle
    if kind == 'unlogged': events.pop()
    elif kind == 'wrong_args': events[0]['payload']['arguments'] = dict(action='TAG', target='container_H')
    elif kind == 'wrong_session': events[0]['payload']['reply']['health']['control_session_id'] = 'b'*32
    else: events[0]['payload']['reply'][kind] = True
    m['command_log'] = ref(root, 'commands.jsonl', b''.join(v.canonical(e) for e in events), True)
    with pytest.raises(ValueError): verify(bundle)


def test_plan_cannot_relax_reset_tolerance_or_allow_unknown_private_fields(bundle):
    bundle[1]['state_tolerances']['joint_rad'] = 1
    with pytest.raises(ValueError): verify(bundle)
    bundle[1]['state_tolerances']['joint_rad'] = v.DEFAULT_TOLERANCES['joint_rad']
    bundle[2]['hidden_answer'] = 'not a field'
    with pytest.raises(ValueError): verify(bundle)


@pytest.mark.parametrize('bad_plan', ('one_pose', 'repeated_pose', 'noise_255', 'noise_1', 'placeholder_pin'))
def test_provisional_profile_cannot_pass_vacuous_view_or_pixel_comparisons(bundle, bad_plan):
    plan = bundle[1]
    if bad_plan == 'one_pose': plan['poses'] = plan['poses'][:1]
    elif bad_plan == 'repeated_pose': plan['poses'][1]['head_pose'] = deepcopy(plan['poses'][0]['head_pose'])
    elif bad_plan == 'placeholder_pin': plan['build_sha256'] = '0' * 64
    else: plan['image']['max_channel_delta'] = 255 if bad_plan == 'noise_255' else 1
    with pytest.raises(ValueError): verify(bundle)


def test_json_nonfinite_duplicate_and_paths_refused(bundle):
    for raw in (b'{"a":1,"a":2}', b'{"x":1e999}', b'['*40+b'0'+b']'*40):
        with pytest.raises(ValueError): v.parse(raw)
    root = bundle[0]; target = root/'target'; target.write_bytes(b'x'); link = root/'hardlink'
    import os
    os.link(target, link)
    with pytest.raises(ValueError, match='UNLINKED'): v.read(target, v.sha(b'x'))
    with pytest.raises(ValueError): v.reference(root, {'path': '../target', 'sha256': v.sha(b'x')})


def test_state_pairwise_bound_not_only_distance_from_neutral(bundle):
    root, plan, m, _ = bundle
    for index, sign in ((0, -1), (1, 1)):
        row = m['captures'][index]; state = json.loads((root/row['state']['path']).read_bytes())
        state['robot']['joint_positions_rad'][0] += sign * plan['state_tolerances']['joint_rad'] * .75
        row['state'] = ref(root, row['state']['path'], state)
    result = verify(bundle)
    assert result['failures'] == []
    assert not result['screen_passed'] and sum(not c['within_tolerance'] for c in result['comparisons']) == 1
