"""Synthetic in-memory contract tests, never actual eight-hour evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from isaac.soak.analyze import analyze_events, analyze_resources, analyze_manifest, bound_file, load_json


def event(t, kind, **fields):
    return dict(t_s=t, kind=kind, source_kind='live', clock_domain='unity_monotonic', **fields)


@pytest.fixture(scope='module')
def valid_events():
    # These declarations exercise validation only; wall time is not advanced.
    result=[event(0,'session_start',monitor='continuous_receiver_stale_and_freeze_detector'),
            event(0,'record_commit',sha256='a'*64),event(1,'reset',reset_id='initial',reset_ok=True),
            event(2,'trial_begin',reset_id='initial'),event(3,'lock_probe',block='protected',block_id='p',rejected=True,logged=True)]
    for index,kind in enumerate(('isaac_crash','wifi_drop','uplink_disconnect'),1):
        t=index*100;identifier=str(index)
        result += [event(t+.1,'fault',fault_id=identifier,fault_type=kind,last_committed_sha256='a'*64),
                   event(t+.2,'pause',fault_id=identifier),event(t+.3,'reset',reset_id=identifier,reset_ok=True,fault_id=identifier),
                   event(t+.4,'resume',fault_id=identifier,reset_id=identifier,last_committed_sha256='a'*64,operator_initiated=True)]
    result += [event(t,'heartbeat',block='protected',block_id='p',state_age_ms=0,frame_age_ms=0,mirrored_frames=t*30) for t in range(28801)]
    result.append(event(28800,'session_end',completed=True))
    return sequence(result)


def sequence(events):
    result=sorted(events,key=lambda x:x['t_s'])
    for index,row in enumerate(result):row['seq']=index
    return result


def test_complete_declared_contract_is_candidate_only(valid_events):
    result=analyze_events(valid_events)
    assert result['duration_s']==28800 and result['faults']==3 and result['failures']==[]


@pytest.mark.parametrize('mutation',[
    lambda rows:rows[-1].update(completed=False),
    lambda rows:rows[-1].update(t_s=28799),
    lambda rows:rows[5].update(source_kind='synthetic'),
    lambda rows:rows[5].update(clock_domain='host_monotonic'),
    lambda rows:rows[5].update(seq=999),
    lambda rows:rows[0].update(monitor='unknown'),
    lambda rows:next(x for x in rows if x['kind']=='resume').update(reset_id='wrong'),
    lambda rows:next(x for x in rows if x['kind']=='resume').update(last_committed_sha256='b'*64),
])
def test_incomplete_or_unverifiable_evidence_rejected(valid_events,mutation):
    rows=deepcopy(valid_events);mutation(rows)
    with pytest.raises(ValueError):analyze_events(rows)


@pytest.mark.parametrize('extra,reason',[
    ([event(500,'stale_gap',block='protected',duration_ms=251)],'protected_stale_gap'),
    ([event(500,'frame_freeze',block='protected',duration_ms=251)],'protected_frame_freeze'),
    ([event(500,'reset',reset_id='failed',reset_ok=False)],'reset_failed_outside_fault'),
    ([event(500,'lock_probe',block='protected',block_id='p',rejected=False,logged=True)],'lock_probe_accepted_or_unlogged'),
    ([event(100.15,'exposure')],'exposure_before_recovery'),
    ([event(90,'cue_playback',cue_id='cue',audible=True,treated_as_unheard=True),event(500,'cue_playback',cue_id='cue',audible=True,treated_as_unheard=True)],'audible_cue_replayed_as_unheard'),
])
def test_measured_failures_are_no_go(valid_events,extra,reason):
    result=analyze_events(sequence(deepcopy(valid_events)+extra))
    assert reason in result['failures']


def test_missing_receiver_window_rejected(valid_events):
    rows=[x for x in deepcopy(valid_events) if not (x['kind']=='heartbeat' and 400 <= x['t_s'] <= 410)]
    with pytest.raises(ValueError,match='logging gap'):analyze_events(sequence(rows))


def resources(growth=0):
    return [dict(t_s=t,ram_mb=1000+growth*t/3600,ram_capacity_mb=1100,vram_mb=1000,vram_capacity_mb=24000,step_ms=2) for t in range(0,28801,60)]


def test_resource_growth_projected_and_incomplete_rejected():
    assert not analyze_resources(resources())['ram']['projected_exhaustion']
    assert analyze_resources(resources(10))['ram']['projected_exhaustion']
    with pytest.raises(ValueError):analyze_resources(resources()[:-2])


def test_file_hash_and_path_escape_rejected(tmp_path):
    path=tmp_path/'evidence';path.write_text('actual bytes')
    reference=dict(path='evidence',sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert bound_file(tmp_path,reference)[1]==b'actual bytes'
    path.write_text('changed')
    with pytest.raises(ValueError):bound_file(tmp_path,reference)
    with pytest.raises(ValueError):bound_file(tmp_path,dict(path='../outside',sha256='a'*64))


@pytest.mark.parametrize('kind',['template','synthetic','short_diagnostic'])
def test_manifest_cannot_promote_other_evidence_kinds(tmp_path,kind):
    path=tmp_path/'manifest.json';path.write_text(json.dumps(dict(version=1,run_kind=kind,source_kind='live')))
    with pytest.raises(ValueError,match='Actual live'):analyze_manifest(path)


def test_duplicate_and_nonfinite_json_rejected():
    for raw in ('{"version":1,"version":2}','{"x":NaN}'):
        with pytest.raises(ValueError):load_json(raw)


def test_failed_recovery_reset_invalidates_earlier_success(valid_events):
    rows=sequence(deepcopy(valid_events)+[event(100.35,'reset',reset_id='later-failure',reset_ok=False,fault_id='1')])
    with pytest.raises(ValueError,match='verified reset'):analyze_events(rows)


def test_static_mirror_cannot_pass_on_heartbeat_alone(valid_events):
    rows=deepcopy(valid_events)
    for row in rows:
        if row['kind']=='heartbeat':row['mirrored_frames']=min(row['mirrored_frames'],30)
    assert 'no_mirror_progress_while_active' in analyze_events(rows)['failures']


def test_manifest_end_to_end_hashes_and_common_station_window(tmp_path,valid_events):
    def write(name,content):
        path=tmp_path/name;path.write_text(content,encoding='utf-8')
        return dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    native={name:write(name+'.log','Synthetic unit fixture for '+name) for name in ('unity','publisher','command','host')}
    events=write('events.jsonl','\n'.join(json.dumps(x) for x in valid_events)+'\n')
    data=resources(); keys=list(data[0])
    resources_ref=write('resources.csv',','.join(keys)+'\n'+'\n'.join(','.join(str(row[k]) for k in keys) for row in data)+'\n')
    station=dict(station_id='unit-test',client_kind='headset_equivalent',substitute_justification='Synthetic contract fixture, not hardware evidence',
                 scene_sha256='a'*64,snapshot_sha256='b'*64,receiver_detector_source_sha256='c'*64,
                 source_logs=native,events=events,resources=resources_ref,coordinator_start_s=0,coordinator_end_s=28800)
    manifest=dict(version=1,run_kind='actual_soak',source_kind='live',schedule_kind='synthetic_nonstudy',participants=False,
                  expected_station_ids=['unit-test'],stations=[station],coordinator_clock_id='unit-fixture')
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest))
    report=analyze_manifest(path)
    assert report['recommendation']=='CANDIDATE_GO' and report['g2_signed'] is False
    # Even individually long station sessions must have a shared eight-hour window.
    second=deepcopy(station);second.update(station_id='other',coordinator_start_s=5,coordinator_end_s=28805)
    manifest['expected_station_ids'].append('other');manifest['stations'].append(second)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='common eight-hour'):analyze_manifest(path)
