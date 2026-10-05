"""Pure evidence checks for the optional dedicated backend fault driver."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from isaac.e2e.fault_check import check_public_neutral, complete_rows, validate_binding, verify_replay, verify_arrivals, append_event, require_lease


def fixture():
    snapshot=json.loads((Path(__file__).resolve().parents[2]/'isaac/snapshots/neutral_v1.json').read_text())
    state=snapshot['state']
    frame=dict(joint_names=deepcopy(state['robot']['joint_names']),joint_positions=deepcopy(state['robot']['joint_positions_rad']),
        objects=[dict(id=key,**{field:deepcopy(value[field]) for field in ('position_m','rotation_xyzw','visible','enabled','state')})
                 for key,value in sorted(state['objects'].items())])
    return frame,snapshot


def test_actual_snapshot_projection_is_checked_with_existing_tolerances():
    frame,snapshot=fixture()
    assert check_public_neutral(frame,snapshot)==dict(joint_rad=0.,position_m=0.,orientation_rad=0.)
    frame['objects'][0]['position_m'][0]+=.0005
    assert check_public_neutral(frame,snapshot)['position_m']==pytest.approx(.0005)
    frame['objects'][0]['position_m'][0]+=.002
    with pytest.raises(ValueError,match='pose'):check_public_neutral(frame,snapshot)


@pytest.mark.parametrize('mutation',[
    lambda f:f['joint_names'].reverse(),lambda f:f['joint_positions'].pop(),
    lambda f:f['joint_positions'].__setitem__(0,2.),lambda f:f['objects'].pop(),
    lambda f:f['objects'].append(deepcopy(f['objects'][0])),
    lambda f:f['objects'][0].__setitem__('visible',not f['objects'][0]['visible']),
    lambda f:f['objects'][0].__setitem__('enabled',not f['objects'][0]['enabled']),
    lambda f:next(x for x in f['objects'] if 'card_face' in x['state'])['state'].__setitem__('card_face',1),
])
def test_corrupt_projection_cannot_be_reported_neutral(mutation):
    frame,snapshot=fixture();mutation(frame)
    with pytest.raises(ValueError):check_public_neutral(frame,snapshot)


def replies():
    first=dict(request_id='a'*32,accepted=True,reset_ok=True,reason='RESET_COMPLETE',duplicate=False)
    replay={**first,'duplicate':True}
    conflict={**first,'accepted':False,'reset_ok':None,'reason':'REQUEST_ID_CONFLICT'}
    return first,replay,conflict


def test_replay_requires_independent_exactly_once_execution_count():
    verify_replay(*replies(),[3,4,4,4])


@pytest.mark.parametrize('counts',[[3,3,3,3],[3,4,5,5],[3,4,4,5],[3,5,5,5]])
def test_no_execution_or_extra_execution_refuses_replay_screen(counts):
    with pytest.raises(ValueError,match='executed'):verify_replay(*replies(),counts)


@pytest.mark.parametrize('index,key,value',[(0,'reset_ok',False),(0,'accepted',False),(0,'duplicate',True),
    (1,'duplicate',False),(1,'request_id','b'*32),(2,'reason','MALFORMED'),(2,'accepted',True)])
def test_receipt_flags_and_exact_conflict_reason_are_required(index,key,value):
    records=list(replies());records[index][key]=value
    with pytest.raises(ValueError):verify_replay(*records,[3,4,4,4])


def test_live_journal_reads_only_complete_rows_and_caps_memory(tmp_path):
    path=tmp_path/'journal.jsonl';path.write_bytes(b'{"event_seq":0}\n{"event_seq":1')
    assert complete_rows(path)==[{'event_seq':0}]
    with pytest.raises(ValueError,match='cap'):complete_rows(path,maximum=10)
    path.write_bytes(b'{"event_seq":0}\n\n')
    with pytest.raises(ValueError,match='Empty'):complete_rows(path)
    path.write_bytes(b'{"event_seq":0,"event_seq":1}\n')
    with pytest.raises(ValueError):complete_rows(path)


@pytest.mark.parametrize('change',[{'scope':'participant'},{'participant':True},{'qualification':True},
    {'station_id':'station-01'},{'control_session_id':'b'*32},{'scene_sha256':'b'*64},{'reset_snapshot_sha256':'b'*64}])
def test_wrong_or_native_station_binding_fails_before_socket_access(change):
    ready=dict(scope='SIMULATION_TEST',participant=False,qualification=False,station_id='fault-01',
        control_session_id='a'*32,scene_sha256='a'*64,reset_snapshot_sha256='a'*64,public_socket='not-accessed',private_socket='not-accessed')
    ready.update(change)
    with pytest.raises(ValueError,match='binding'):
        validate_binding(ready,scene_sha256='a'*64,snapshot_sha256='a'*64,control_session_id='a'*32,station_id='fault-01')


def test_killed_receiver_prefix_still_requires_every_durable_sequence_and_clock():
    rows=[dict(seq=i,sim_step=i+10,source_host_ns=str(100+i),receive_host_ns=str(200+i)) for i in range(12)]
    verify_arrivals(rows)
    with pytest.raises(ValueError,match='insufficient'):verify_arrivals(rows[:3])
    for key in ('seq','sim_step','source_host_ns','receive_host_ns'):
        broken=deepcopy(rows);broken[6][key]=broken[5][key]
        with pytest.raises(ValueError,match='progression'):verify_arrivals(broken)


def test_receiver_name_is_metadata_and_cannot_replace_event_clock(tmp_path):
    path=tmp_path/'timeline.jsonl'
    row=append_event(path,'receiver_connected',name='receiver-before',pid=123)
    assert complete_rows(path)==[row] and row['name']=='receiver-before' and row['event']=='receiver_connected'
    assert int(row['host_ns'])>0
    with pytest.raises(ValueError,match='authority'):append_event(path,'fake',host_ns='0')
    assert complete_rows(path)==[row]


def test_driver_requires_remaining_same_host_source_budget_before_fault_commands():
    ready=dict(requested_seconds=90.,source_ready_host_ns='1000000000')
    assert require_lease(ready,2_000_000_000)==89.
    assert require_lease(ready,16_000_000_000)==75.
    with pytest.raises(ValueError,match='remaining'):require_lease(ready,17_000_000_000)
    with pytest.raises(ValueError,match='monotonic'):require_lease(ready,0)
    for bad in (True,float('inf'),0,3601):
        with pytest.raises(ValueError,match='lease'):require_lease({**ready,'requested_seconds':bad},2_000_000_000)
