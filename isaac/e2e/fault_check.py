"""Bounded dedicated-source recovery diagnostic, never a native/soak substitute.

Uses existing private Unix transports. It kills only its own receiver child,
discards one reset acknowledgement at the application boundary, and records real
reconnect/replay outcomes. No simulator, network setting or unrelated PID is killed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import uuid

from isaac.publisher.protocol import PublicRegistry, strict_loads, validate_frame
from isaac.reset.benchmark import durable
from isaac.reset.manager import Tolerances, angle

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):
    durable(Path(path),(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())


def append_event(path,kind,**values):
    if set(values)&{'event','host_ns'}:raise ValueError('Event authority fields cannot be replaced')
    row=dict(event=kind,host_ns=str(time.monotonic_ns()),**values)
    with Path(path).open('ab') as stream:
        stream.write((json.dumps(row,allow_nan=False)+'\n').encode());stream.flush();os.fsync(stream.fileno())
    return row


def require_lease(ready,now_ns):
    seconds=ready.get('requested_seconds');stamp=ready.get('source_ready_host_ns')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 5<=seconds<=3600:
        raise ValueError('Bounded source lease required')
    if not isinstance(stamp,str) or not stamp.isdecimal() or type(now_ns) is not int or now_ns<int(stamp):
        raise ValueError('Same-host monotonic readiness clock required')
    remaining=seconds-(now_ns-int(stamp))/1e9
    if remaining<75:raise ValueError('At least75s remaining source lease required for60s driver and cleanup')
    return remaining


def complete_rows(path, maximum=16*1024*1024):
    """Only newline-terminated durable-prefix rows while the owner appends."""
    with Path(path).open('rb') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum:raise ValueError('Diagnostic journal cap exceeded')
    pieces=raw.split(b'\n')
    if any(not line for line in pieces[:-1]):raise ValueError('Empty committed journal row')
    return [strict_loads(line.decode()) for line in pieces[:-1]]


def check_public_neutral(frame,snapshot):
    wanted=snapshot['state'];limits=Tolerances()
    if frame['joint_names']!=wanted['robot']['joint_names']:
        raise ValueError('Joint identity changed')
    if len(frame['joint_positions'])!=len(wanted['robot']['joint_positions_rad']):
        raise ValueError('Joint shape changed')
    joint=max(abs(a-b) for a,b in zip(frame['joint_positions'],wanted['robot']['joint_positions_rad']))
    objects={item['id']:item for item in frame['objects']}
    if set(objects)!=set(wanted['objects']) or len(frame['objects'])!=len(objects):raise ValueError('Object identity changed')
    position=orientation=0.
    for identifier,item in objects.items():
        neutral=wanted['objects'][identifier]
        if any(item[k]!=neutral[k] for k in ('visible','enabled','state')):
            raise ValueError('Nonneutral public visual state')
        position=max(position,math.dist(item['position_m'],neutral['position_m']))
        orientation=max(orientation,angle(item['rotation_xyzw'],neutral['rotation_xyzw']))
    if joint>limits.joint_rad or position>limits.position_m or orientation>limits.orientation_rad:
        raise ValueError('Nonneutral public pose')
    return dict(joint_rad=joint,position_m=position,orientation_rad=orientation)


def registry_from_dict(value):
    value=dict(value)
    value['joint_names']=tuple(value['joint_names'])
    value['object_states']=tuple((key,tuple(fields)) for key,fields in value['object_states'])
    value['anchor_ids']=tuple(value['anchor_ids'])
    return PublicRegistry(**value)


def receiver_main(config_path):
    """Fsync every validated arrival; abrupt kill intentionally has no terminal."""
    from websockets.client import unix_connect
    config=json.loads(Path(config_path).read_text());directory=Path(config_path).parent
    registry=registry_from_dict(config['registry']);snapshot=config['snapshot']
    result=dict(count=0,sequence_gaps=0,error=None,completed=False)
    last=None
    async def receive():
        nonlocal last
        async with unix_connect(config['socket'],uri='ws://localhost/state',compression=None,max_size=1048576) as client:
            save(directory/'connected.json',dict(pid=os.getpid(),host_ns=str(time.monotonic_ns())))
            with (directory/'arrivals.jsonl').open('xb') as stream:
                while not (directory/'stop').exists():
                    try:raw=await asyncio.wait_for(client.recv(),.1)
                    except asyncio.TimeoutError:continue
                    received=time.monotonic_ns();frame=validate_frame(strict_loads(raw),registry)
                    if frame['session_id']!=config['public_session_id'] or frame['source_kind']!='live':raise ValueError('Live public session changed')
                    deviation=check_public_neutral(frame,snapshot)
                    if last is not None and frame['seq']!=last['seq']+1:result['sequence_gaps']+=1
                    if result['count']==0:save(directory/'first.json',frame)
                    row=dict(seq=frame['seq'],sim_step=frame['sim_step'],sim_time=frame['sim_time'],
                        source_host_ns=frame['host_monotonic_ns'],receive_host_ns=str(received),
                        frame_sha256=hashlib.sha256(raw.encode()).hexdigest(),deviation=deviation)
                    stream.write((json.dumps(row,separators=(',',':'))+'\n').encode());stream.flush();os.fsync(stream.fileno())
                    last=frame;result['count']+=1
            result['completed']=True
    try:asyncio.run(receive())
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if last is not None:save(directory/'last.json',last)
        save(directory/'terminal.json',result)
    return 0 if result['completed'] and result['error'] is None else 1


def validate_binding(ready,*,scene_sha256,snapshot_sha256,control_session_id,station_id):
    if (ready.get('scope')!='SIMULATION_TEST' or ready.get('participant') is not False or ready.get('qualification') is not False
            or not station_id.startswith('fault-') or ready.get('station_id')!=station_id
            or ready.get('scene_sha256')!=scene_sha256 or ready.get('reset_snapshot_sha256')!=snapshot_sha256
            or ready.get('control_session_id')!=control_session_id):
        raise ValueError('Dedicated simulation source binding refused')
    for key in ('public_socket','private_socket'):
        path=Path(ready[key])
        if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('Private absolute non-symlink endpoint required')
        info=path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Dedicated receiver must own permission0600 endpoint')


def health(path):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(3);client.connect(str(path))
        client.sendall(b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        raw=b''
        while True:
            block=client.recv(4096)
            if not block:break
            raw+=block
            if len(raw)>65536:raise ValueError('Health response cap exceeded')
    header,body=raw.split(b'\r\n\r\n',1)
    if not header.startswith(b'HTTP/1.1 200 '):raise ValueError('Actual health endpoint refused')
    return strict_loads(body.decode())


def request(session,command,args=None,request_id=None):
    return dict(version=1,kind='private_command',control_session_id=session,
        request_id=request_id or uuid.uuid4().hex,command=command,args={} if args is None else args)


async def exchange(path,value):
    from websockets.client import unix_connect
    async with unix_connect(str(path),uri='ws://localhost/commands',compression=None,max_size=65536) as client:
        before=time.monotonic_ns();await client.send(json.dumps(value))
        reply=strict_loads(await asyncio.wait_for(client.recv(),3))
        after=time.monotonic_ns()
    if reply.get('request_id')!=value['request_id']:raise ValueError('Uncorrelated actual acknowledgement')
    return dict(request=value,reply=reply,sent_host_ns=str(before),received_host_ns=str(after),rtt_ms=(after-before)/1e6)


def verify_replay(first,replay,conflict,reset_counts):
    """A cached reply is evidence only with independent reset journal counts."""
    for reply in (first,replay):
        if reply.get('accepted') is not True or reply.get('reset_ok') is not True or reply.get('reason')!='RESET_COMPLETE':
            raise ValueError('Actual successful reset receipt required')
    if first.get('duplicate') is not False or replay.get('duplicate') is not True:
        raise ValueError('Actual replay marker missing')
    if first.get('request_id')!=replay.get('request_id') or conflict.get('request_id')!=first.get('request_id'):
        raise ValueError('Replay identity mismatch')
    if conflict.get('accepted') is not False or conflict.get('reason')!='REQUEST_ID_CONFLICT':
        raise ValueError('Changed request was not refused')
    before,executed,replayed,changed=reset_counts
    if executed!=before+1 or replayed!=executed or changed!=executed:
        raise ValueError('Reset replay executed again or original execution absent')


def verify_arrivals(rows):
    if len(rows)<10:raise ValueError('Receiver evidence insufficient')
    for previous,current in zip(rows,rows[1:]):
        if (current['seq']!=previous['seq']+1 or current['sim_step']<=previous['sim_step'] or
                int(current['source_host_ns'])<=int(previous['source_host_ns']) or
                int(current['receive_host_ns'])<=int(previous['receive_host_ns'])):
            raise ValueError('Receiver sequence or clock progression failed')


def run_fault_check(args):
    from isaac.publisher.benchmark import registry_from_snapshot
    from isaac.reset.snapshot import load_snapshot
    from isaac.e2e.diagnostic import close_phase_resources
    source=Path(args.source);ready_path=source/'joined-e2e/ready.json'
    raw=ready_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.ready_sha256:raise ValueError('Ready file raw SHA mismatch')
    ready=strict_loads(raw.decode())
    validate_binding(ready,scene_sha256=args.scene_sha256,snapshot_sha256=args.snapshot_sha256,
        control_session_id=args.control_session_id,station_id=args.station_id)
    remaining_lease=require_lease(ready,time.monotonic_ns())
    snapshot=load_snapshot(source/'reset-check/neutral_v1.json',args.snapshot_sha256)
    layout=json.loads((ROOT/'apparatus/workcell_layout.json').read_text())
    registry=registry_from_snapshot(layout,snapshot,args.snapshot_sha256,args.station_id,ROOT/'docs/spikes/isaac/joint_inventory.csv')
    output=Path(args.output);output.mkdir(parents=True,exist_ok=False)
    children=[];events=[];cleanup=[];failure=None;checks={};started=time.monotonic_ns()
    def event(kind,**values):
        events.append(append_event(output/'timeline.jsonl',kind,**values))
    def spawn(name):
        folder=output/name;folder.mkdir()
        save(folder/'config.json',dict(socket=ready['public_socket'],registry=asdict(registry),snapshot=snapshot,public_session_id=ready['public_session_id']))
        log=(folder/'stderr.txt').open('xb');env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join(str(x) for x in sys.path if x)
        process=subprocess.Popen([sys.executable,'-m','isaac.e2e.fault_check','--receiver',str(folder/'config.json')],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log,env=env)
        children.append((process,folder,log))
        deadline=time.monotonic()+8
        while not (folder/'connected.json').exists():
            if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Strict receiver did not connect')
            time.sleep(.02)
        event('receiver_connected',name=name,pid=process.pid)
        return process,folder
    def observe(seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            time.sleep(min(.1,max(0.,deadline-time.monotonic())))
    def command(value):
        record=asyncio.run(exchange(ready['private_socket'],value));event('private_ack',**record);return record['reply']
    reset_log=source/'e2e-reset-events.jsonl';command_log=source/'joined-e2e/commands.jsonl'
    def reset_count():return len(complete_rows(reset_log))
    try:
        first,first_dir=spawn('receiver-before');observe(10)
        before_rows=complete_rows(first_dir/'arrivals.jsonl')
        if len(before_rows)<10:raise RuntimeError('Initial receiver evidence insufficient')
        before_health=health(ready['public_socket']);event('before_receiver_kill',health=before_health,last_arrival=before_rows[-1])
        first.kill();first.wait(timeout=5)
        if first.returncode!=-9:raise RuntimeError('Owned receiver did not exit via injected SIGKILL')
        before_rows=complete_rows(first_dir/'arrivals.jsonl');verify_arrivals(before_rows)
        if (first_dir/'terminal.json').exists():raise RuntimeError('Killed receiver unexpectedly has graceful terminal')
        checks['receiver_before_durable_frames']=len(before_rows)
        event('owned_receiver_killed',pid=first.pid,exit_code=first.returncode)
        paused=command(request(args.control_session_id,'pause'))
        if not paused['accepted'] or paused['reason']!='PAUSED_AT_SAFE_POINT' or paused['health']['paused'] is not True:
            raise RuntimeError('Explicit diagnostic operator pause refused')
        observe(10)
        absent_health=health(ready['public_socket']);event('receiver_absent',health=absent_health)
        if absent_health['published']<=before_health['published'] or absent_health['connected_clients']!=0 or absent_health['fault'] is not None:
            raise RuntimeError('Source did not continue independently of receiver')
        checks['source_continued_without_receiver']=True
        second,second_dir=spawn('receiver-after');observe(10)
        after_rows=complete_rows(second_dir/'arrivals.jsonl')
        verify_arrivals(after_rows)
        if len(after_rows)<10 or after_rows[0]['seq']<=before_rows[-1]['seq'] or after_rows[0]['sim_step']<=before_rows[-1]['sim_step']:
            raise RuntimeError('Reconnected source did not progress')
        checks['reconnect_progression']=True
        original=request(args.control_session_id,'reset');counts=[reset_count()]
        async def discard():
            from websockets.client import unix_connect
            async with unix_connect(ready['private_socket'],uri='ws://localhost/commands',compression=None,max_size=65536) as client:
                await client.send(json.dumps(original));event('reset_sent_ack_not_consumed',request=original)
                deadline=time.monotonic()+3
                while True:
                    matching=[x for x in complete_rows(command_log) if x['payload']['reply']['request_id']==original['request_id']]
                    if matching:return matching[-1]['payload']['reply']
                    if time.monotonic()>deadline:raise TimeoutError('Reset terminal journal row missing')
                    await asyncio.sleep(.02)
                # Deliberately never call recv(); socket closes with ACK unconsumed.
        first_reply=asyncio.run(discard());counts.append(reset_count());event('reset_ack_discarded',journal_reply=first_reply)
        replay=command(original);counts.append(reset_count())
        changed=command(request(args.control_session_id,'hold_neutral',request_id=original['request_id']));counts.append(reset_count())
        verify_replay(first_reply,replay,changed,counts)
        checks['reset_executed_once_despite_ack_discard']=True;checks['reset_journal_counts']=counts
        probe=command(request(args.control_session_id,'demo',{'action':'ADD_ONE','target':'tray_A'}))
        if probe['accepted'] is not False or probe['reason']!='PROTECTED_TARGET_COMMAND':raise RuntimeError('Protected lock probe failed')
        checks['protected_probe_rejected']=True
        resumed=command(request(args.control_session_id,'resume'))
        if resumed['accepted'] is not True or resumed['reason']!='RESUMED_EXPLICITLY':raise RuntimeError('Explicit resume refused')
        observe(max(0.,60-(time.monotonic_ns()-started)/1e9))
        durable(second_dir/'stop',b'stop');second.wait(timeout=5)
        terminal=json.loads((second_dir/'terminal.json').read_text())
        if second.returncode or not terminal['completed'] or terminal['error'] or terminal['sequence_gaps']:
            raise RuntimeError('Reconnected receiver did not finalize cleanly')
        after_rows=complete_rows(second_dir/'arrivals.jsonl');verify_arrivals(after_rows)
        if len(after_rows)!=terminal['count']:raise RuntimeError('Receiver terminal count disagrees with durable rows')
        checks['receiver_after_durable_frames']=len(after_rows)
        checks['reconnected_receiver_complete']=True
    except Exception as error:failure=type(error).__name__+': '+str(error)
    finally:
        # All sources here are independently bound, dedicated fault-* simulations.
        try:
            stopped=command(request(args.control_session_id,'stop'))
            checks['final_stop_reset_ok']=stopped['accepted'] is True and stopped['reset_ok'] is True and stopped['reason']=='STOPPED'
        except Exception as error:cleanup.append('source stop: '+type(error).__name__+': '+str(error))
        for process,folder,log in children:
            if process.poll() is None:
                close_phase_resources(process,folder/'stop',durable,[('receiver log',log)],cleanup)
            else:
                try:log.close()
                except Exception as error:cleanup.append('receiver log: '+type(error).__name__)
    report=dict(scope='actual_backend_fault_diagnostic',participant=False,qualification=False,
        native_visit_completed=False,source_ready_sha256=args.ready_sha256,scene_sha256=args.scene_sha256,
        reset_snapshot_sha256=args.snapshot_sha256,elapsed_seconds=(time.monotonic_ns()-started)/1e9,
        admitted_remaining_lease_seconds=remaining_lease,
        checks=checks,fault=failure,cleanup_errors=cleanup,
        completed=failure is None and not cleanup and checks.get('final_stop_reset_ok') is True,
        limitations=['Explicit supervisor pause; no Unity automatic pause or cue-replay claim.',
            'Receiver process killed, not Isaac, headset, Wi-Fi or host uplink.',
            'Reset ACK deliberately unconsumed by application; no packet-loss claim.',
            'No timing, acoustic, clock, multi-station or eight-hour qualification.'])
    report['hashes']={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}
    save(output/'summary.json',report)
    return report


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--receiver':return receiver_main(sys.argv[2])
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','ready-sha256','scene-sha256','snapshot-sha256','control-session-id','station-id'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args();report=run_fault_check(args);print(json.dumps(report))
    return 0 if report['completed'] else 1


if __name__=='__main__':raise SystemExit(main())
