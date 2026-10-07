"""Real dispatcher/log and approved socket library; synthetic simulator only."""
import asyncio
from copy import deepcopy
import importlib.util
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from isaac.commands.dispatcher import CommandDispatcher
from isaac.commands.event_log import DurableCommandLog
from isaac.commands import health_probe
from isaac.commands.protocol import LEGAL_PAIRS, decode
from isaac.view_capture.lock_probe import exercise, run, save, sha, verify_command_bindings


class Reset:
    adapter=SimpleNamespace(sim_time=1.)
    exposure_ready=True
    def verification_status(self):
        return dict(verified=True,host_mono_ms=time.monotonic_ns()/1e6,age_ms=0.)


class Publisher:
    closed=False
    def require_neutral(self,value):assert value is True
    def health(self):return dict(age_ms=0.,stale=False,fault=None)


def backend(tmp_path,client='uid:7'):
    log=DurableCommandLog(tmp_path/'commands.jsonl',session_id='a'*32,
        apparatus_version='synthetic',protocol_version='synthetic')
    dispatcher=CommandDispatcher(Reset(),log,station_id='fixture',allowed_client=client,publisher=Publisher())
    return dispatcher,log


class Socket:
    def __init__(self,dispatcher):self.dispatcher=dispatcher;self.sent=[];self.mode=None
    async def send(self,raw):self.sent.append(raw)
    async def recv(self):
        request=decode(self.sent[-1])
        if self.mode=='timeout':await asyncio.sleep(1)
        if request['kind']=='private_health_probe':
            reply=health_probe.reply(request,self.dispatcher.control_session_id,self.dispatcher.health)
        else:
            reply=self.dispatcher.submit(self.sent[-1],self.dispatcher.allowed_client).result()
        if self.mode=='wrong_id':reply['request_id']='b'*32
        if self.mode=='stale':reply['health']['publisher_age_ms']=251
        if self.mode=='accepted' and request['kind']=='private_command':reply['accepted']=True
        if self.mode=='wrong_reason' and request['kind']=='private_command':reply['reason']='MALFORMED'
        return json.dumps(reply)


def completed(tmp_path):
    dispatcher,log=backend(tmp_path);rows=[];socket=Socket(dispatcher)
    asyncio.run(exercise(socket,dispatcher.control_session_id,rows));log.close()
    report=dict(version=1,kind='view_lock_probe',completed=True,error=None,records=rows,
        control_session_id=dispatcher.control_session_id,station_id='fixture',client='uid:7',
        ready_sha256='a'*64,native_owner_release_declared=True,qualification=False)
    return report,log.path.read_bytes()


def test_all_pairs_use_real_dispatcher_and_durable_rejection_join(tmp_path):
    report,commands=completed(tmp_path)
    result=verify_command_bindings(report,commands)
    assert result['protected_rejections']==32 and result['command_binding_verified']
    assert result['capture_qualified'] is False
    rows=[json.loads(line) for line in commands.splitlines()]
    assert len(rows)==32
    assert {(row['payload']['arguments']['action'],row['payload']['arguments']['target']) for row in rows}==LEGAL_PAIRS


@pytest.mark.parametrize('mode,expected_commands',[
    ('timeout',0),('wrong_id',0),('stale',0),('accepted',1),('wrong_reason',1)])
def test_unexpected_or_timeout_aborts_once_and_retains_partial_exchange(tmp_path,mode,expected_commands):
    dispatcher,log=backend(tmp_path);socket=Socket(dispatcher);socket.mode=mode;rows=[]
    with pytest.raises((ValueError,asyncio.TimeoutError)):
        asyncio.run(exercise(socket,dispatcher.control_session_id,rows))
    log.close()
    assert len(socket.sent)==expected_commands+1
    assert len(log.path.read_bytes().splitlines())==expected_commands
    assert rows and rows[-1]['request_utf8']==socket.sent[-1]
    if mode=='timeout':assert rows[-1]['reply_utf8'] is None and rows[-1]['error']=='TimeoutError'


@pytest.mark.parametrize('change',['reply','station','uid','duplicate','missing','reorder','request','partial'])
def test_final_join_rejects_wrong_history_or_partial_run(tmp_path,change):
    report,raw=completed(tmp_path);events=[json.loads(line) for line in raw.splitlines()]
    if change=='reply':events[0]['payload']['reply']['accepted']=True
    elif change=='station':events[0]['payload']['station_id']='foreign'
    elif change=='uid':events[0]['payload']['client']='uid:8'
    elif change=='duplicate':events.append(deepcopy(events[0]))
    elif change=='missing':events.pop()
    elif change=='reorder':events[1]['event_seq']=0
    elif change=='request':events[0]['payload']['raw_command']='{}'
    else:report['completed']=False
    changed=b''.join((json.dumps(row)+'\n').encode() for row in events)
    with pytest.raises(ValueError):verify_command_bindings(report,changed)


def test_no_explicit_owner_release_refuses_before_any_io(tmp_path):
    with pytest.raises(ValueError,match='OWNER_NOT_RELEASED'):
        run(tmp_path/'missing','a'*64,tmp_path/'out',7,native_control_released=False)
    assert not (tmp_path/'out').exists()


def test_failed_durable_report_never_publishes_complete_name(tmp_path,monkeypatch):
    def broken(_):raise OSError('injected')
    monkeypatch.setattr(os,'fsync',broken)
    with pytest.raises(OSError):save(tmp_path/'probe.json',{'completed':True})
    assert not (tmp_path/'probe.json').exists()


@pytest.mark.parametrize('change',['extra','bool_version','unreleased','qualification','record_extra',
    'numeric_stamp','bool_rtt','tail'])
def test_offline_binding_requires_actual_closed_driver_format(tmp_path,change):
    report,commands=completed(tmp_path)
    if change=='extra':report['extra']=True
    elif change=='bool_version':report['version']=True
    elif change=='unreleased':report['native_owner_release_declared']=False
    elif change=='qualification':report['qualification']=True
    elif change=='record_extra':report['records'][0]['extra']=True
    elif change=='numeric_stamp':report['records'][0]['sent_host_ns']=1
    elif change=='bool_rtt':report['records'][0]['rtt_ms']=False
    else:commands=commands.rstrip(b'\n')
    with pytest.raises(ValueError):verify_command_bindings(report,commands)


@pytest.mark.skipif(not hasattr(os,'geteuid') or not importlib.util.find_spec('websockets'),
    reason='requires Linux and approved preinstalled websockets')
def test_actual_unix_transport_has_32_logged_rejections_no_mode_or_reset(tmp_path):
    from isaac.commands.queue import CommandQueue
    from isaac.commands.transport import PrivateCommandTransport
    client=f'uid:{os.geteuid()}';dispatcher,log=backend(tmp_path,client)
    queue=CommandQueue(dispatcher);path=tmp_path/'private.sock'
    transport=PrivateCommandTransport(queue,socket_path=path,allowed_uid=os.geteuid())
    ready=tmp_path/'ready.json'
    ready.write_text(json.dumps(dict(scope='SIMULATION_TEST',participant=False,qualification=False,
        control_session_id=dispatcher.control_session_id,station_id='fixture',private_socket=str(path))))
    results=[];failures=[]
    def client_run():
        try:results.append(run(ready,sha(ready.read_bytes()),tmp_path/'probe',os.geteuid(),native_control_released=True))
        except Exception as error:failures.append(error)
    thread=threading.Thread(target=client_run);thread.start()
    try:
        end=time.monotonic()+12
        while thread.is_alive() and time.monotonic()<end:
            queue.drain();time.sleep(.001)
        thread.join(.1)
        assert not thread.is_alive() and not failures
    finally:
        transport.close();log.close()
    report=results[0]
    assert report==json.loads((tmp_path/'probe/probe.json').read_bytes())
    assert verify_command_bindings(report,log.path.read_bytes())['protected_rejections']==32
    assert len(report['records'])==64 and not path.exists()
