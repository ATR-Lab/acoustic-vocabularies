"""Bounded post-capture test-lock requests on the existing private Unix API.

The operator must first release the native control owner. This diagnostic sends
no mode/reset/stop command, never retries, and cannot establish capture admission.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
from pathlib import Path
import stat
import time
import uuid

from isaac.commands.protocol import LEGAL_PAIRS, decode, validate
from isaac.reset.snapshot import canonical_bytes
from isaac.soak.native import GUID, SHA, read_bounded, safe_path

HEALTH_KEYS = frozenset(('control_session_id','mode','paused','stopped','fault','demo_active',
    'publisher_ready','neutral_verification_age_ms','publisher_age_ms','health_sample_host_mono_ms',
    'exposure_ready','public_stream_recovered'))
REPLY_KEYS = frozenset(('version','kind','request_id','accepted','reason','mode','host_mono_ms',
    'sim_time','reset_ok','duplicate','health'))


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0


def health(value, session, rtt_ms=None):
    require(isinstance(value,dict) and set(value)==HEALTH_KEYS,'LOCK_HEALTH_SHAPE')
    require(value['control_session_id']==session and value['mode']=='test','LOCK_TEST_SESSION')
    require(all(type(value[k]) is bool for k in ('paused','stopped','demo_active','publisher_ready',
        'exposure_ready','public_stream_recovered')),'LOCK_HEALTH_TYPES')
    require(value['paused'] is False and value['stopped'] is False and value['demo_active'] is False
        and value['fault'] is None,'LOCK_HEALTH_FAULT')
    require(finite(value['health_sample_host_mono_ms']),'LOCK_HEALTH_CLOCK')
    for key in ('neutral_verification_age_ms','publisher_age_ms'):
        require(value[key] is None or finite(value[key]),'LOCK_HEALTH_AGE')
    if rtt_ms is not None:
        require(finite(rtt_ms) and rtt_ms <= 200 and value['exposure_ready'] is True
            and value['publisher_ready'] is True,'LOCK_HEALTH_STALE')
        require(all(value[key] is not None and value[key]+rtt_ms <= 250
            for key in ('neutral_verification_age_ms','publisher_age_ms')),'LOCK_HEALTH_STALE')


def check_probe(reply, request, rtt_ms):
    require(isinstance(request,dict) and set(request)=={'version','kind','control_session_id','request_id'}
        and type(request['version']) is int and request['version']==1
        and request['kind']=='private_health_probe'
        and all(isinstance(request[k],str) and GUID.fullmatch(request[k])
            for k in ('control_session_id','request_id')),'LOCK_PROBE_REQUEST')
    require(isinstance(reply,dict) and set(reply)=={'version','kind','control_session_id',
        'request_id','accepted','reason','health'},'LOCK_PROBE_SHAPE')
    require(type(reply['version']) is int and reply['version']==1
        and reply['kind']=='private_health_reply' and reply['accepted'] is True
        and reply['reason']=='HEALTH' and reply['request_id']==request['request_id']
        and reply['control_session_id']==request['control_session_id'],'LOCK_PROBE_BINDING')
    health(reply['health'],request['control_session_id'],rtt_ms)


def check_rejection(reply, request):
    validate(request)
    require(request['command']=='demo','LOCK_DEMO_ONLY')
    require(isinstance(reply,dict) and set(reply)==REPLY_KEYS,'LOCK_REPLY_SHAPE')
    require(type(reply['version']) is int and reply['version']==1 and reply['kind']=='private_reply'
        and reply['request_id']==request['request_id'] and reply['accepted'] is False
        and reply['reason']=='PROTECTED_TARGET_COMMAND' and reply['mode']=='test'
        and reply['reset_ok'] is None and reply['duplicate'] is False,'LOCK_REJECTION_MISMATCH')
    require(finite(reply['host_mono_ms']) and finite(reply['sim_time']),'LOCK_REPLY_CLOCK')
    # This is a historical durable rejection, not a fresh admission receipt.
    health(reply['health'],request['control_session_id'])


async def exercise(websocket, session, records, *, clock_ns=time.monotonic_ns):
    """One bounded request per pair; append intent before send so ambiguity survives."""
    async def exchange(request, timeout):
        raw=canonical_bytes(request).decode('utf-8')
        row=dict(request_utf8=raw,reply_utf8=None,sent_host_ns=str(clock_ns()),
            received_host_ns=None,rtt_ms=None,error=None)
        records.append(row)
        async def wire():
            await websocket.send(raw)
            return await websocket.recv()
        try:
            response=await asyncio.wait_for(wire(),timeout)
            end=clock_ns();row.update(received_host_ns=str(end),rtt_ms=(end-int(row['sent_host_ns']))/1e6)
            require(isinstance(response,str),'LOCK_BINARY_REPLY')
            row['reply_utf8']=response
            return decode(response),row['rtt_ms']
        except Exception as error:
            row['error']=type(error).__name__
            raise
    for action,target in sorted(LEGAL_PAIRS):
        probe=dict(version=1,kind='private_health_probe',control_session_id=session,request_id=uuid.uuid4().hex)
        reply,rtt=await exchange(probe,.2)
        check_probe(reply,probe,rtt)
        request=dict(version=1,kind='private_command',control_session_id=session,
            request_id=uuid.uuid4().hex,command='demo',args=dict(action=action,target=target))
        reply,_=await exchange(request,3.)
        check_rejection(reply,request)


def socket_identity(path, uid):
    require(hasattr(os,'geteuid') and os.geteuid()==uid,'LOCK_CLIENT_UID')
    path=Path(path)
    require(path.is_absolute(),'LOCK_SOCKET_ABSOLUTE')
    safe_path(path.parent,directory=True)
    value=path.lstat()
    require(stat.S_ISSOCK(value.st_mode) and value.st_uid==uid
        and value.st_mode & 0o077==0,'LOCK_SOCKET_OWNERSHIP')
    return value.st_dev,value.st_ino


def pinned_ready(path, expected):
    require(isinstance(expected,str) and SHA.fullmatch(expected),'LOCK_READY_PIN')
    raw=read_bounded(path,16384);require(sha(raw)==expected,'LOCK_READY_PIN')
    value=decode(raw.decode('utf-8'))
    require(isinstance(value,dict) and value.get('scope')=='SIMULATION_TEST'
        and value.get('participant') is False and value.get('qualification') is False
        and isinstance(value.get('control_session_id'),str)
        and GUID.fullmatch(value['control_session_id'])
        and isinstance(value.get('station_id'),str) and value['station_id']
        and isinstance(value.get('private_socket'),str),'LOCK_READY_SHAPE')
    return value


def save(path, value):
    temporary=path.with_name('.'+path.name+'.tmp')
    with temporary.open('xb') as handle:
        handle.write(canonical_bytes(value));handle.flush();os.fsync(handle.fileno())
    os.link(temporary,path)
    try:temporary.unlink()
    except OSError:pass


def run(ready_path, ready_sha256, output, uid, *, native_control_released):
    require(native_control_released is True,'LOCK_NATIVE_OWNER_NOT_RELEASED')
    ready=pinned_ready(ready_path,ready_sha256)
    path=Path(ready['private_socket']);identity=socket_identity(path,uid)
    output=safe_path(output,directory=True,missing=True);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    records=[]
    result=dict(version=1,kind='view_lock_probe',ready_sha256=ready_sha256,
        station_id=ready['station_id'],control_session_id=ready['control_session_id'],
        client=f'uid:{uid}',native_owner_release_declared=True,completed=False,error=None,
        records=records,qualification=False)
    async def work():
        from websockets.client import unix_connect
        async with unix_connect(str(path),uri='ws://localhost/commands',compression=None,
                open_timeout=3,close_timeout=2,max_size=16384,max_queue=1) as ws:
            require(socket_identity(path,uid)==identity,'LOCK_SOCKET_REPLACED')
            await exercise(ws,ready['control_session_id'],records)
    try:
        asyncio.run(asyncio.wait_for(work(),120.))
        result['completed']=True
    except Exception as error:
        # Do not expose arbitrary remote payload/error strings as diagnostics.
        result['error']=str(error) if isinstance(error,ValueError) and str(error).startswith('LOCK_') else type(error).__name__
    finally:
        save(output/'probe.json',result)
    return result


def verify_command_bindings(report, commands_raw):
    """Bind successful replies to independently pinned finalized command bytes.

The full source/native verifier must still validate finalization and capture;
this function verifies only exact test-lock command evidence.
"""
    require(report.get('kind')=='view_lock_probe' and report.get('version')==1
        and report.get('completed') is True and report.get('error') is None,'LOCK_RUN_INCOMPLETE')
    rows=report['records'];require(len(rows)==64,'LOCK_PAIR_COUNT')
    require(len(commands_raw)<=256*1024*1024,'LOCK_COMMAND_SIZE')
    lines=commands_raw.splitlines();require(len(lines)<=4096,'LOCK_COMMAND_COUNT')
    events=[decode(line.decode('utf-8')) for line in lines]
    require(all(event.get('event_seq')==index and type(event.get('event_seq')) is int
        for index,event in enumerate(events)),'LOCK_JOURNAL_ORDER')
    ids=set();pairs=[];prior_seq=-1;previous_received=-1
    for index in range(0,len(rows),2):
        probe,row=rows[index:index+2]
        for value in (probe,row):
            require(value['error'] is None and value['reply_utf8'] is not None,'LOCK_EXCHANGE_INCOMPLETE')
            require(int(value['received_host_ns'])>=int(value['sent_host_ns'])>=previous_received,'LOCK_EXCHANGE_CLOCK')
            require(value['rtt_ms']==(int(value['received_host_ns'])-int(value['sent_host_ns']))/1e6,'LOCK_EXCHANGE_CLOCK')
            require(value['rtt_ms']<=3000,'LOCK_EXCHANGE_DEADLINE')
            previous_received=int(value['received_host_ns'])
        probe_request=decode(probe['request_utf8']);request=decode(row['request_utf8'])
        for value in (probe_request,request):
            require(value['control_session_id']==report['control_session_id']
                and value['request_id'] not in ids,'LOCK_REQUEST_REUSED')
            ids.add(value['request_id'])
        check_probe(decode(probe['reply_utf8']),probe_request,probe['rtt_ms'])
        reply=decode(row['reply_utf8']);check_rejection(reply,request)
        matches=[event for event in events if event.get('payload',{}).get('reply',{}).get('request_id')==request['request_id']]
        require(len(matches)==1,'LOCK_EVENT_CARDINALITY')
        event=matches[0];payload=event['payload']
        require(event['schema_version']=='0.3.1' and event['event_type']=='private_command_result'
            and type(event['event_seq']) is int and event['event_seq']>prior_seq,'LOCK_EVENT_ORDER')
        prior_seq=event['event_seq']
        require(payload['station_id']==report['station_id'] and payload['client']==report['client']
            and payload['mode']=='test' and payload['command']=='demo' and payload['arguments']==request['args']
            and payload['raw_command']==row['request_utf8']
            and canonical_bytes(payload['reply'])==canonical_bytes(reply),'LOCK_EVENT_BINDING')
        pairs.append((request['args']['action'],request['args']['target']))
    require(pairs==sorted(LEGAL_PAIRS),'LOCK_PAIR_COVERAGE')
    return dict(version=1,kind='view_lock_probe_binding',command_journal_sha256=sha(commands_raw),
        protected_rejections=32,command_binding_verified=True,capture_qualified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    live=commands.add_parser('run')
    live.add_argument('--ready',required=True);live.add_argument('--ready-sha256',required=True)
    live.add_argument('--output',required=True);live.add_argument('--uid',type=int,required=True)
    live.add_argument('--native-control-released',action='store_true',required=True)
    check=commands.add_parser('join')
    for key in ('probe','probe-sha256','commands','commands-sha256','output'):
        check.add_argument('--'+key,required=True)
    args=parser.parse_args()
    if args.command=='run':
        return 0 if run(args.ready,args.ready_sha256,args.output,args.uid,
            native_control_released=args.native_control_released)['completed'] else 1
    raw=read_bounded(args.probe,4*1024*1024)
    require(sha(raw)==args.probe_sha256,'LOCK_PROBE_PIN')
    command_raw=read_bounded(args.commands,256*1024*1024)
    require(sha(command_raw)==args.commands_sha256,'LOCK_COMMAND_PIN')
    result=verify_command_bindings(decode(raw.decode('utf-8')),command_raw)
    result['probe_sha256']=sha(raw)
    output=safe_path(args.output,missing=True);save(output,result)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
