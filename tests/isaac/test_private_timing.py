"""Bounded observer failures cannot silently turn into complete evidence."""
import asyncio
from copy import deepcopy
import gc
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac.commands.queue import CommandQueue
from isaac.commands.transport import PrivateCommandTransport
from isaac.e2e.private_timing import PrivateTiming
from isaac.e2e.service import advance_once, run_joined_service
from test_e2e_service import fixture
from test_health_probe import status
from isaac.publisher.protocol import PublicRegistry
from isaac.publisher.runtime import StatePublisher


def recorder(tmp_path, capacity=200):
    now=[1_000_000_000]
    trace=PrivateTiming(tmp_path/'trace.json',1,capacity=capacity,clock_ns=lambda:now[0])
    return trace,now


def test_unstarted_observer_has_no_callbacks_or_events_and_is_incomplete(tmp_path):
    before=list(gc.callbacks)
    trace,now=recorder(tmp_path)
    trace.record('ignored')
    assert gc.callbacks==before and trace.count==0
    trace.close()
    assert gc.callbacks==before
    assert json.loads(trace.path.read_text())['complete'] is False


def test_window_capacity_overflow_and_no_writes_until_close(tmp_path):
    trace,now=recorder(tmp_path,2);trace.start()
    try:
        trace.record('one');trace.record('two');trace.record('over-capacity')
        assert not trace.path.exists() and trace.count==2 and trace.dropped==1
        now[0]+=1_000_000_000
        trace.record('outside-window')
        assert trace.dropped==1 and trace.count==2
    finally:trace.close()
    data=json.loads(trace.path.read_text())
    assert not data['complete'] and data['dropped']==1
    assert [row[1] for row in data['rows']]==['one','two']


def test_gc_reentry_never_waits_on_recorder_lock_and_marks_incomplete(tmp_path):
    trace,now=recorder(tmp_path);trace.start()
    try:
        with trace.lock:
            trace._gc_callback('start',{'generation':2})
            trace._gc_callback('stop',{'generation':2,'collected':7})
        assert trace.count==0 and trace.dropped==2
        trace._gc_callback('start',{'generation':2})
        now[0]+=351_000_000
        trace._gc_callback('stop',{'generation':2,'collected':7,'uncollectable':0})
        now[0]+=1_000_000_000
    finally:trace.close()
    data=json.loads(trace.path.read_text())
    assert data['rows'][1][0]-data['rows'][0][0]==351_000_000
    assert data['rows'][1][5:]==[2,7,0] and not data['complete']
    assert trace.callback not in gc.callbacks and data['gc_before']==data['gc_after']


def test_write_failure_still_removes_callback_and_stops_recording(tmp_path,monkeypatch):
    trace,now=recorder(tmp_path);trace.start()
    monkeypatch.setattr('isaac.e2e.private_timing.os.fsync',lambda _: (_ for _ in ()).throw(OSError('test')))
    with pytest.raises(OSError):trace.close()
    assert trace.callback not in gc.callbacks and trace.closed
    count=trace.count;trace.record('late');assert trace.count==count


def test_real_gc_callbacks_keep_settings_and_serialize_only_metadata(tmp_path):
    trace,now=recorder(tmp_path,2000);before=gc.isenabled(),gc.get_threshold();trace.start()
    try:
        gc.collect(0)
        now[0]+=1_000_000_000
    finally:trace.close()
    data=json.loads(trace.path.read_text())
    assert data['complete'] and before==(gc.isenabled(),gc.get_threshold())
    assert any(row[1]=='gc_start' for row in data['rows'])
    assert any(row[1]=='gc_stop' for row in data['rows'])
    assert all(len(row)==len(PrivateTiming.FIELDS) for row in data['rows'])


def test_loop_watch_records_scheduler_lag_without_changing_clock(tmp_path):
    trace,now=recorder(tmp_path);trace.start()
    async def exercise():
        task=asyncio.create_task(trace.loop_watch())
        await asyncio.sleep(.005)
        now[0]+=120_000_000
        await asyncio.sleep(.03)
        now[0]+=1_000_000_000
        await task
    try:asyncio.run(exercise())
    finally:trace.close()
    assert any(row[1]=='loop_lag' and row[5]>=100_000_000 for row in json.loads(trace.path.read_text())['rows'])


def test_health_bytes_identical_and_lock_wait_is_measured(tmp_path,monkeypatch):
    monkeypatch.setattr('isaac.commands.queue.time.monotonic_ns',lambda:1_100_000_000)
    class Dispatcher:
        reset_manager=SimpleNamespace(adapter=SimpleNamespace(sim_time=0.))
        def _thread(self):pass
        def health(self):return deepcopy(status())
    plain=CommandQueue(Dispatcher())
    trace=PrivateTiming(tmp_path/'trace.json',1,clock_ns=time.perf_counter_ns);trace.start()
    timed=CommandQueue(Dispatcher(),timing=trace)
    try:
        assert json.dumps(plain.health(),sort_keys=True)==json.dumps(timed.health(),sort_keys=True)
        timed.lock.acquire()
        done=threading.Event()
        def read():timed.health();done.set()
        worker=threading.Thread(target=read);worker.start()
        time.sleep(.025);timed.lock.release();worker.join(1)
        assert done.is_set()
    finally:trace.close()
    rows=json.loads(trace.path.read_text())['rows']
    waits=[r for r in rows if r[1]=='cache_wait' and r[2]==worker.ident]
    acquired=[r for r in rows if r[1]=='cache_acquired' and r[2]==worker.ident]
    assert acquired[0][0]-waits[0][0]>=20_000_000


def test_observer_does_not_change_actual_owner_sequence(tmp_path):
    baseline,a,d,h,p=fixture();expected=advance_once(a,d,h,p,7)
    calls,a,d,h,p=fixture();trace,now=recorder(tmp_path);trace.start()
    try:actual=advance_once(a,d,h,p,7,timing=trace)
    finally:trace.close()
    assert actual==expected and calls==baseline


def test_provider_exception_retained_and_not_replaced_by_health(tmp_path):
    trace,now=recorder(tmp_path);trace.start()
    owner=object.__new__(PrivateCommandTransport);owner.timing=trace
    def fail():raise RuntimeError('provider')
    owner.handoff=SimpleNamespace(health=fail)
    try:
        with pytest.raises(RuntimeError,match='provider'):owner._traced_health()
    finally:trace.close()
    assert [r[1] for r in json.loads(trace.path.read_text())['rows']]==['provider_begin','provider_error']


def test_full_publisher_comparison_and_exact_bytes_survive_observer(tmp_path):
    trace,now=recorder(tmp_path);trace.start()
    payloads=[];checks=[]
    class Transport:
        def submit(self,payload):payloads.append(payload)
        def metrics(self):return {'connected_clients':1,'queue_overwrites':0}
        def close(self):pass
    def check(complete):checks.append(complete);return True
    frame=json.loads((Path(__file__).parents[1]/'fixtures/publisher-state.json').read_text())
    registry=PublicRegistry(frame['station_id'],frame['scene_sha256'],frame['reset_snapshot_sha256'],
        tuple(frame['joint_names']),(("synthetic_card",("card_face",)),))
    def state():
        obj=deepcopy(frame['objects'][0]);obj.pop('id')
        return [0.]*43,{'synthetic_card':obj},{'synthetic_neutral':True}
    try:
        for index,timing in enumerate((None,trace)):
            publisher=StatePublisher(registry,state,Transport(),tmp_path/f'{index}.csv',
                neutral_check=check,source_kind='synthetic',clock_ns=lambda:1_000_000_000,timing=timing)
            publisher.encoder.session_id='a'*32
            publisher.require_neutral(True)
            try:
                assert publisher.after_step(.1,6) is not None and publisher.fault is None
            finally:publisher.close()
    finally:trace.close()
    assert len(checks)==2 and checks[0]==checks[1]
    assert len(payloads)==2 and payloads[0]==payloads[1]
    kinds=[r[1] for r in json.loads(trace.path.read_text())['rows']]
    assert kinds==['sample_begin','sample_end','neutral_begin','neutral_end','encode_begin','encode_end']


def test_send_failure_is_recorded_and_still_raises(tmp_path):
    pytest.importorskip('websockets',reason='actual approved WebSocket exception types required')
    from test_health_probe import request
    trace,now=recorder(tmp_path);trace.start()
    owner=object.__new__(PrivateCommandTransport);owner.timing=trace
    owner.control_session_id='a'*32
    owner.handoff=SimpleNamespace(health=status)
    class Socket:
        def __aiter__(self):return self
        async def __anext__(self):return json.dumps(request())
        async def send(self,payload):raise OSError('send failure')
    try:
        with pytest.raises(OSError,match='send failure'):asyncio.run(owner._client(Socket()))
    finally:trace.close()
    rows=json.loads(trace.path.read_text())['rows']
    assert rows[-1][1]=='send_error' and rows[-1][3]=='b'*32
    assert not any(r[1]=='send_end' for r in rows)


@pytest.mark.parametrize('seconds',[True,-1,.5,901,float('nan'),float('inf')])
def test_diagnostic_duration_refuses_before_startup(tmp_path,seconds):
    with pytest.raises(ValueError,match='Private timing'):
        run_joined_service(None,None,tmp_path/'out',seconds=3600,station_id='sim',host_uid=1000,
            public_socket=tmp_path/'a',private_socket=tmp_path/'b',private_timing_seconds=seconds)
