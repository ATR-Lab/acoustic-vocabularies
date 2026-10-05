"""Synthetic byte/clock fixtures only; no actual elapsed-time qualification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from isaac.soak.native import NativeReader, plan_bytes, receiver_events, safe_path
from isaac.soak.collect import Capture, CoordinatorJournal, causal_overlap, collect, encoded, load_plan
from isaac.soak.analyze import analyze_events


def native_plan(seconds=6):
    return dict(version=1, scope='synthetic_nonstudy', participants=False, station_id='station-01',
                build_id='DEMO-build', scene_sha256='a'*64, snapshot_sha256='b'*64,
                schedule_sha256='c'*64, source_kind='live', seconds=seconds,
                client_kind='headset_equivalent', substitute_justification='Unit-test declaration only')


class Fixture:
    def __init__(self, plan=None):
        self.plan = plan or native_plan()
        self.raw = encoded(self.plan)
        self.pin = hashlib.sha256(self.raw).hexdigest()
        self.lines = []; self.previous = '0'*64

    def row(self, kind, t, payload):
        r = dict(version=1, seq=len(self.lines), clock_epoch='e'*32,
                 clock_domain='unity_stopwatch_seconds', stopwatch_frequency_hz=10000000,
                 t_s=t, kind=kind, payload=payload, previous_sha256=self.previous)
        self.previous = hashlib.sha256(encoded(r)).hexdigest()
        r['sha256'] = self.previous
        line = encoded(r); self.lines.append(line)
        return line

    def start(self):
        return self.row('session_start', 10., dict(plan=self.plan, plan_sha256=self.pin,
            monitor='continuous_receiver_stale_and_freeze_detector', arrival_age_basis='latest_accepted_sample_local_arrival',
            mirror_count_basis='distinct_applied_frame_session_sequence', render_basis='application_onBeforeRender_not_photons'))

    def heartbeat(self, t, frames=1, block='paused'):
        return self.row('heartbeat', t, dict(block=block, block_id='idle', state_age_ms=0., frame_age_ms=0.,
            mirrored_frames=frames, applied_session='f'*32, applied_sequence=frames, render_frame_index=frames))

    def end(self, t=16., completed=True, fault=None):
        return self.row('session_end', t, dict(completed=completed, requested_seconds=self.plan['seconds'],
            elapsed_seconds=t-10., monitor_fault=fault, g2_signed=False))

    def reader(self):
        return NativeReader(plan_bytes(self.raw, self.pin), self.pin)


def complete_fixture():
    f = Fixture(); f.start()
    for i in range(25): f.heartbeat(10+i*.25, i+1)
    f.end(); return f


def test_incremental_exact_native_hash_with_partial_tail():
    f=complete_fixture(); r=f.reader(); raw=b''.join(f.lines)
    for i in range(0,len(raw),17): r.feed(raw[i:i+17])
    result=r.finish()
    assert result['receiver_window_complete'] and result['native_rows']==27
    assert result['qualification']=='native_capture_only' and result['g2_signed'] is False
    with pytest.raises(ValueError,match='eight'): analyze_events(receiver_events(r))


@pytest.mark.parametrize('mutation',[
    lambda b:b.replace(b'"mirrored_frames":1,',b'"mirrored_frames":9,',1),
    lambda b:b.replace(b'"t_s":10.0',b'"t_s":1e1',1),
    lambda b:b.replace(b'"previous_sha256":"000',b'"previous_sha256":"100',1),
    lambda b:b.replace(b'"seq":1,',b'"seq":2,',1),
])
def test_changed_exact_bytes_refused(mutation):
    f=complete_fixture()
    with pytest.raises(ValueError): f.reader().feed(mutation(b''.join(f.lines)))


def test_truncated_or_missing_terminal_refused():
    f=complete_fixture(); r=f.reader(); r.feed(b''.join(f.lines)[:-1])
    with pytest.raises(ValueError,match='incomplete'):r.finish()
    r=f.reader();r.feed(b''.join(f.lines[:-1]))
    with pytest.raises(ValueError,match='incomplete'):r.finish()


def test_false_completion_regressing_count_and_runtime_binding_refused():
    f=Fixture();f.start();f.heartbeat(10);f.end(11)
    r=f.reader();r.feed(b''.join(f.lines))
    with pytest.raises(ValueError,match='False'):r.finish()
    f=Fixture();f.start();f.heartbeat(10,4);f.heartbeat(11,2);f.end()
    r=f.reader()
    with pytest.raises(ValueError,match='counter'):r.feed(b''.join(f.lines))
    f=Fixture();f.start();r=NativeReader({**f.plan,'station_id':'wrong'},f.pin)
    with pytest.raises(ValueError,match='binding'):r.feed(f.lines[0])


def test_postterminal_and_mixed_epoch_refused():
    f=complete_fixture(); extra=f.heartbeat(17); r=f.reader()
    with pytest.raises(ValueError,match='terminal'):r.feed(b''.join(f.lines))
    f=Fixture();f.start();line=f.heartbeat(11)
    row=json.loads(line);row.pop('sha256');row['clock_epoch']='f'*32
    row['sha256']=hashlib.sha256(encoded(row)).hexdigest()
    r=f.reader();r.feed(f.lines[0])
    with pytest.raises(ValueError,match='Mixed'):r.feed(encoded(row))


@pytest.mark.parametrize('field,value',[('scope','participant'),('participants',True),('source_kind','synthetic'),('seconds',True),('seconds',36001)])
def test_plan_never_upgrades_participant_or_synthetic(field,value):
    p=native_plan();p[field]=value;b=encoded(p)
    with pytest.raises(ValueError):plan_bytes(b,hashlib.sha256(b).hexdigest())


def test_causal_overlap_uses_receive_start_send_end():
    rs=[dict(causal_windows={'start':{'sent_ns':'10','received_ns':'30'},'end':{'sent_ns':'110','received_ns':'190'}}),
        dict(causal_windows={'start':{'sent_ns':'15','received_ns':'40'},'end':{'sent_ns':'100','received_ns':'200'}})]
    assert causal_overlap(rs)==dict(start_ns='40',end_ns='100',seconds=60/1e9)
    rs[0]['causal_windows']['end']['sent_ns']='35'
    with pytest.raises(ValueError):causal_overlap(rs)


def test_streaming_reader_keeps_bounded_rows_and_same_summary():
    f=complete_fixture();raw=b''.join(f.lines);a=f.reader();a.feed(raw)
    b=NativeReader(f.plan,f.pin,retain_rows=False)
    for line in f.lines:b.feed(line)
    assert len(b.rows)==2 and b.finish()==a.finish()
    with pytest.raises(ValueError,match='streamed'):receiver_events(b)


def prepare(tmp_path):
    f=Fixture();f.start();capture=tmp_path/'capture';capture.mkdir()
    (capture/'soak-native.jsonl').write_bytes(b''.join(f.lines))
    (tmp_path/'station-plan.json').write_bytes(f.raw)
    logs={}
    for role in ('publisher','command','host'):
        (tmp_path/(role+'.log')).write_bytes(b'actual role is not claimed by this test\n');logs[role]=role+'.log'
    plan=dict(version=1,scope='synthetic_nonstudy',participants=False,seconds=1,stations=[dict(station_id='station-01',
        plan=dict(path='station-plan.json',sha256=f.pin),capture_directory='capture',source_logs=logs)])
    raw=encoded(plan);path=tmp_path/'plan.json';path.write_bytes(raw)
    loaded,_=load_plan(path,hashlib.sha256(raw).hexdigest())
    return f,capture,loaded,raw


def test_collector_drives_real_file_protocol_with_explicit_synthetic_clock(tmp_path):
    f,capture,plan,raw=prepare(tmp_path);ns=[1_000_000_000];last_probe=[None];ended=[False];frames=[0]
    def sleep(seconds):
        ns[0]+=int(seconds*1e9);t=10+(ns[0]-1_000_000_000)/1e9
        lines=[];p=capture/'coordinator-probe.json'
        if p.exists():
            probe=json.loads(p.read_bytes())
            if probe['request_id']!=last_probe[0]:
                last_probe[0]=probe['request_id'];lines.append(f.row('coordinator_probe',t,probe))
        if not ended[0]:
            frames[0]+=1;lines.append(f.heartbeat(t,frames[0]))
            if t>=16:lines.append(f.end(t));ended[0]=True
        if lines:
            with (capture/'soak-native.jsonl').open('ab') as s:s.write(b''.join(lines))
    result=collect(plan,raw,tmp_path/'out',clock=lambda:ns[0],sleep=sleep)
    assert result['collection_error'] is None and result['shared_causal_window']['seconds']>=1
    assert result['recommendation']=='NO_GO' and result['g2_signed'] is False
    assert result['stations'][0]['receiver']['receiver_window_complete']
    assert (tmp_path/'out'/'station-01'/'command.native').exists()
    assert not (capture/'soak-collector.lock').exists()
    assert (capture/'coordinator-probe.json').exists()


def test_collector_timeout_retains_incomplete_without_unlocking_other_owner(tmp_path):
    f,capture,plan,raw=prepare(tmp_path);(capture/'soak-collector.lock').write_text('another-owner')
    result=collect(plan,raw,tmp_path/'out')
    assert result['collection_error'] and (capture/'soak-collector.lock').read_text()=='another-owner'
    assert result['recommendation']=='NO_GO'


def test_prefix_rewrite_refused_at_retention(tmp_path):
    f,d,plan,raw=prepare(tmp_path);j=CoordinatorJournal(tmp_path/'coord.jsonl','coord-test')
    c=Capture(plan['stations'][0],'coord-test',j)
    try:
        c.acquire();c.poll();path=d/'soak-native.jsonl';content=path.read_bytes();path.write_bytes(content.replace(b'DEMO-build',b'DEMO-other'))
        with pytest.raises(ValueError,match='prefix'):c.retain(tmp_path/'retained')
    finally:c.close();j.close()


def test_output_overlap_refused_before_mutation(tmp_path):
    _,capture,plan,raw=prepare(tmp_path)
    with pytest.raises(ValueError,match='overlaps'):collect(plan,raw,capture/'out')
    assert not (capture/'out').exists()


def test_existing_probe_is_not_overwritten(tmp_path):
    _,capture,plan,raw=prepare(tmp_path);(capture/'coordinator-probe.json').write_bytes(b'keep')
    result=collect(plan,raw,tmp_path/'out')
    assert result['collection_error'] and (capture/'coordinator-probe.json').read_bytes()==b'keep'
    assert not (capture/'soak-collector.lock').exists()


def test_symlinked_native_source_refused(tmp_path):
    target=tmp_path/'target';target.write_bytes(b'x');link=tmp_path/'linked'
    try:link.symlink_to(target)
    except OSError as error:
        if getattr(error,'winerror',None)==1314:pytest.skip('Windows symlink privilege unavailable')
        raise
    with pytest.raises(ValueError,match='Linked'):safe_path(link)
