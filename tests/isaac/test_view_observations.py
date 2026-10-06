"""Synthetic evidence checks; these never claim an actual rendered capture."""
from copy import deepcopy
import json
import threading

import pytest

from isaac.commands.event_log import DurableCommandLog
from isaac.publisher.protocol import PublicRegistry, StateEncoder, encode
from isaac.publisher.runtime import StatePublisher
from isaac.reset.snapshot import canonical_bytes
from isaac.view_capture.observations import ObservationJournal, digest, validate_options
from test_reset import fixture


def setup(tmp_path, **limits):
    state = fixture()
    registry = PublicRegistry('station-01','a'*64,'b'*64,
        tuple(state['robot']['joint_names']),
        tuple((key,tuple(sorted(obj['state']))) for key,obj in sorted(state['objects'].items())),
        ('fixture_anchor',))
    now = [1_000_000_000]
    log = DurableCommandLog(tmp_path/'commands.jsonl',session_id='c'*32,
        apparatus_version='synthetic',protocol_version='synthetic')
    journal = ObservationJournal(tmp_path/'capture',plan_sha256='d'*64,source_commit='e'*40,
        registry=registry,public_session_id='f'*32,control_session_id='1'*32,allowed_client='uid:7',
        commands_path=log.path,clock_ns=lambda:now[0],
        **dict(max_records=100,max_serialized_bytes=1_000_000,max_seconds=10,**limits))
    journal.start()
    encoder = StateEncoder(registry,clock_ns=lambda:now[0]);encoder.session_id='f'*32
    return journal,log,state,now,encoder


def event(request_id='2'*32, command='reset', **reply_changes):
    request=dict(version=1,kind='private_command',control_session_id='1'*32,
        request_id=request_id,command=command,args={})
    reply=dict(version=1,kind='private_reply',request_id=request_id,accepted=True,
        reason='RESET_COMPLETE',mode='test',host_mono_ms=1000.,sim_time=0.,reset_ok=True,
        duplicate=False,health={'control_session_id':'1'*32})
    reply.update(reply_changes)
    return dict(station_id='station-01',mode='test',client='uid:7',raw_command=json.dumps(request),
        command=command,arguments={},reply=reply)


def publish(journal,state,now,encoder,step=1,**kw):
    now[0]+=10_000_000
    observed=now[0]
    now[0]+=1_000
    frame=encoder.build(state['robot']['joint_positions_rad'],state['objects'],step/60,step)
    journal.after_publish(frame,encode(frame),state,observed,protected=True,neutral_valid=True,**kw)
    return frame


def finish(journal,log):
    log.close()
    return journal.close()


def test_actual_sample_and_wire_are_detached_and_final_files_are_exact(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    ack=event();journal.command_sink(log)(ack)
    frame=publish(journal,state,now,encoder)
    expected=deepcopy(state)
    state['robot']['joint_velocities_rad_s'][0]=7
    state['environment']['lights']['fixture_light']['intensity']=2
    ack['reply']['host_mono_ms']=0
    assert not journal.output.exists()  # No live writes/fsync.
    report=finish(journal,log)
    assert report['complete'] and report['fault'] is None
    rows=[json.loads(s) for s in (journal.output/'source-observations.jsonl').read_text().splitlines()]
    assert rows[1]['state']==expected and rows[1]['frame']==frame
    assert rows[1]['frame_utf8']==encode(frame)
    assert rows[1]['frame_sha256']==digest(encode(frame).encode())
    reply=json.loads((journal.output/'reset-replies.jsonl').read_text())
    assert reply['reply_canonical_sha256']==digest(canonical_bytes(reply['reply']))
    for file in report['files'].values():
        raw=(journal.output/file['path']).read_bytes()
        assert len(raw)==file['bytes'] and digest(raw)==file['sha256']
    assert (journal.output/'commands.jsonl').read_bytes()==log.path.read_bytes()
    assert journal.close() is report


def test_failed_durable_write_cannot_update_lineage(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    def broken(_):raise OSError('injected durable error')
    with pytest.raises(OSError):journal.command_sink(broken)(event())
    publish(journal,state,now,encoder)
    assert not journal.replies and not journal.observations
    assert not finish(journal,log)['complete']


def test_later_durable_failure_invalidates_previously_retained_capture(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    def broken(_):raise OSError('injected')
    with pytest.raises(OSError):journal.command_sink(broken)(event('3'*32))
    report=finish(journal,log)
    assert not report['complete'] and report['observation_count']==1
    assert report['fault']=='OBS_COMMAND_LOG_FAILED'


@pytest.mark.parametrize('command',['reset','hold_neutral','set_mode','pause','resume','stop'])
@pytest.mark.parametrize('accepted',[True,False])
def test_terminal_transition_clears_prior_lineage_even_when_rejected(tmp_path,command,accepted):
    journal,log,state,now,encoder=setup(tmp_path)
    sink=journal.command_sink(log);sink(event())
    sink(event('3'*32,command,accepted=accepted,duplicate=True))
    publish(journal,state,now,encoder)
    assert journal.lineage is None and not journal.observations
    assert not finish(journal,log)['complete']


def test_nonmutating_rejection_cannot_create_but_does_not_clear_proof(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    sink=journal.command_sink(log)
    denied=event(command='demo',accepted=False,reason='PROTECTED_TARGET_COMMAND',reset_ok=None)
    sink(denied);publish(journal,state,now,encoder)
    assert not journal.observations
    sink(event());sink(denied);publish(journal,state,now,encoder,2)
    assert finish(journal,log)['complete']


@pytest.mark.parametrize('change',['frame','joints','objects','nan','unprotected','future_stamp'])
def test_mismatched_sample_or_wire_latches_incomplete_preserving_prior_rows(tmp_path,change):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    now[0]+=10_000_000
    frame=encoder.build(state['robot']['joint_positions_rad'],state['objects'],2/60,2)
    payload=encode(frame);observed=now[0];protected=True
    if change=='frame':payload=payload.replace('"seq":1','"seq":99')
    elif change=='joints':state['robot']['joint_positions_rad'][0]=1
    elif change=='objects':state['objects']['engineering_object_0']['visible']=False
    elif change=='nan':state['robot']['joint_velocities_rad_s'][0]=float('nan')
    elif change=='unprotected':protected=False
    elif change=='future_stamp':observed+=1
    journal.after_publish(frame,payload,state,observed,protected=protected,neutral_valid=True)
    assert len(journal.observations)==1
    assert finish(journal,log)['fault']=='OBS_STATE_FRAME_INVALID'


@pytest.mark.parametrize('limit',['rows','bytes','seconds'])
def test_capacity_and_duration_never_silently_wrap(tmp_path,limit):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event())
    if limit=='rows':journal.max_records=2
    elif limit=='bytes':journal.max_bytes=journal.bytes_retained+1
    else:now[0]+=11_000_000_000
    publish(journal,state,now,encoder)
    assert not finish(journal,log)['complete']
    assert len(journal.replies)==1


def test_foreign_owner_latches_incomplete(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event())
    worker=threading.Thread(target=lambda:publish(journal,state,now,encoder))
    worker.start();worker.join()
    assert finish(journal,log)['fault']=='OBS_OWNER_CHANGED'


def test_independent_finalization_attempts_survive_one_write_failure(tmp_path,monkeypatch):
    from pathlib import Path
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    original=Path.open
    def injected(path,*args,**kw):
        if path.name=='source-observations.jsonl':raise OSError('injected')
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'open',injected)
    report=finish(journal,log)
    assert not report['complete'] and report['files']['observations'] is None
    assert report['files']['commands'] and report['files']['reset_replies']
    assert (journal.output/'manifest.json').exists()


def test_options_reject_extra_task_content_or_unbounded_capture():
    good=dict(plan_sha256='a'*64,source_commit='b'*40,max_records=4096,
        max_serialized_bytes=256*1024*1024,max_seconds=900)
    validate_options(good,900)
    for changes in [{'target':'tray_A'},{'max_records':4097},{'max_seconds':901},
                    {'max_serialized_bytes':256*1024*1024+1},{'plan_sha256':'bad'}]:
        with pytest.raises(ValueError):validate_options(good|changes,900)


def test_options_default_off_and_explicit_profile_scope():
    import argparse
    from isaac.view_capture.options import add_arguments,profile_options
    parser=argparse.ArgumentParser();add_arguments(parser)
    args=parser.parse_args([])
    assert profile_options(args) is None
    args.view_observation_plan_sha256='a'*64
    with pytest.raises(ValueError,match='All observation'):profile_options(args)
    values=vars(parser.parse_args(['--view-observation-plan-sha256','a'*64,
        '--view-observation-source-commit','b'*40,'--view-observation-max-records','100',
        '--view-observation-max-bytes','1000000','--view-observation-max-seconds','30']))
    values.update(reset_check=True,skip_reach=True,e2e_seconds=40,publisher_seconds=0,
        command_check=False,published_command_check=False,disconnect_check=False,demo_check=False,
        demo_preflight=False,grip_check=False,protected_stream_seconds=0,same_iteration_check=False,
        e2e_private_timing_seconds=0)
    assert profile_options(argparse.Namespace(**values))['max_seconds']==30
    for change in ({'e2e_seconds':0},{'e2e_private_timing_seconds':10},{'e2e_handle_cache':True}):
        with pytest.raises(ValueError,match='ordinary joined source'):
            profile_options(argparse.Namespace(**(values|change)))


def test_manifest_write_failure_cannot_return_complete(tmp_path,monkeypatch):
    from pathlib import Path
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    original=Path.open
    def injected(path,*args,**kw):
        if path.name=='.manifest.json.tmp':raise OSError('injected')
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'open',injected)
    result=finish(journal,log)
    assert not result['complete'] and result['fault']=='OBS_MANIFEST_FAILED'
    assert all(result['files'].values())


def test_manifest_fsync_failure_does_not_publish_complete_file(tmp_path,monkeypatch):
    import os
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    original=os.fsync;count=[0]
    def injected(fd):
        count[0]+=1
        if count[0]==4:raise OSError('injected manifest fsync')
        return original(fd)
    monkeypatch.setattr(os,'fsync',injected)
    report=finish(journal,log)
    assert not report['complete'] and report['fault']=='OBS_MANIFEST_FAILED'
    assert not (journal.output/'manifest.json').exists()


def test_clock_regression_and_reused_reset_refuse(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    sink=journal.command_sink(log);sink(event());publish(journal,state,now,encoder)
    sink(event())
    assert journal.fault=='OBS_RESET_INVALID'
    assert not finish(journal,log)['complete']


def test_clock_regression_cannot_be_captured(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    now[0]-=1_000_000_000
    publish(journal,state,now,encoder,2)
    assert finish(journal,log)['fault']=='OBS_CLOCK_REGRESSED'


def test_output_appearing_before_close_is_not_written(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    journal.output.mkdir();sentinel=journal.output/'sentinel';sentinel.write_bytes(b'unchanged')
    report=finish(journal,log)
    assert report['fault']=='OBS_OUTPUT_FAILED' and not report['complete']
    assert all(value is None for value in report['files'].values())
    assert list(journal.output.iterdir())==[sentinel] and sentinel.read_bytes()==b'unchanged'


def test_linked_output_appearing_before_close_is_not_written(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    journal.command_sink(log)(event());publish(journal,state,now,encoder)
    target=tmp_path/'unrelated';target.mkdir()
    try:journal.output.symlink_to(target,target_is_directory=True)
    except OSError:
        log.close();pytest.skip('Windows symlink privilege unavailable')
    report=finish(journal,log)
    assert report['fault']=='OBS_OUTPUT_FAILED' and not report['complete']
    assert not list(target.iterdir())


def test_hidden_request_field_cannot_become_a_reset_lineage(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    value=event();request=json.loads(value['raw_command']);request['target']='tray_A'
    value['raw_command']=json.dumps(request)
    journal.command_sink(log)(value);publish(journal,state,now,encoder)
    assert not journal.observations
    assert finish(journal,log)['fault']=='OBS_RESET_INVALID'


def test_publisher_callback_failure_keeps_normal_guards_and_reports_capture_failure(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    class Transport:
        def submit(self,payload):self.payload=payload
        def metrics(self):return dict(connected_clients=0,queue_overwrites=0)
        def close(self):pass
    def broken(*a,**kw):raise RuntimeError('injected callback failure')
    journal.after_publish=broken
    publisher=StatePublisher(journal.registry,
        lambda:(state['robot']['joint_positions_rad'],state['objects'],state),
        Transport(),tmp_path/'publish.csv',clock_ns=lambda:now[0],
        neutral_check=lambda actual:actual is state,observation=journal)
    publisher.require_neutral(True)
    assert publisher.after_step(1/60,1) is not None and publisher.fault is None
    assert journal.fault=='OBS_CALLBACK_FAILED'
    publisher.close();finish(journal,log)


def test_broken_failure_latch_faults_publisher_instead_of_silently_continuing(tmp_path):
    journal,log,state,now,encoder=setup(tmp_path)
    class Transport:
        def submit(self,payload):pass
        def metrics(self):return dict(connected_clients=0,queue_overwrites=0)
        def close(self):pass
    def broken(*a,**kw):raise RuntimeError('injected broken observer')
    journal.after_publish=broken;journal.fail=broken
    publisher=StatePublisher(journal.registry,
        lambda:(state['robot']['joint_positions_rad'],state['objects'],state),Transport(),
        tmp_path/'publish.csv',clock_ns=lambda:now[0],neutral_check=lambda _:True,observation=journal)
    publisher.require_neutral(True)
    assert publisher.after_step(1/60,1) is None and publisher.fault=='PUBLISHER_FAILURE'
    publisher.close();log.close()
