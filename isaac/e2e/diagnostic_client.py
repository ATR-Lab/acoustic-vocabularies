"""Separate-process strict receiver/control client for bounded engineering phases."""
import asyncio
import csv
import json
import os
from pathlib import Path
import sys
import time
import uuid


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())


def run(directory):
    from websockets.client import unix_connect
    from isaac.publisher.protocol import PublicRegistry, strict_loads, validate_frame
    directory = Path(directory)
    config = json.loads((directory/'client-config.json').read_text())
    value = config['registry']
    value['joint_names'] = tuple(value['joint_names'])
    value['object_states'] = tuple((key, tuple(fields)) for key, fields in value['object_states'])
    value['anchor_ids'] = tuple(value['anchor_ids'])
    registry = PublicRegistry(**value)
    report = dict(count=0, sequence_gaps=0, error=None, commands=[], first=None, last=None)
    async def work():
        async with unix_connect(config['public_socket'], uri='ws://localhost/state', compression=None, max_size=1048576) as public, unix_connect(config['private_socket'], uri='ws://localhost/commands', compression=None, max_size=65536) as private:
            save(directory/'client-ready.json', {'ready':True})
            while not (directory/'go').exists():
                if (directory/'stop').exists(): return
                await asyncio.sleep(.01)
            started = int((directory/'go').read_text())
            async def receive():
                with (directory/'arrivals.csv').open('x',newline='') as stream:
                    writer=csv.writer(stream); writer.writerow(['seq','receive_host_ns','source_host_ns','sim_step'])
                    while not (directory/'stop').exists():
                        try: raw=await asyncio.wait_for(public.recv(),.1)
                        except asyncio.TimeoutError: continue
                        received=time.monotonic_ns()
                        frame=validate_frame(strict_loads(raw),registry)
                        if report['last'] is not None and frame['seq'] != report['last']['seq']+1:
                            report['sequence_gaps'] += 1
                        if report['first'] is None: report['first']=frame
                        report['last']=frame; report['count']+=1
                        writer.writerow([frame['seq'],received,frame['host_monotonic_ns'],frame['sim_step']])
                    stream.flush();os.fsync(stream.fileno())
            async def commands():
                for offset,command,args,accepted in [
                    (1,'set_mode',{'mode':'teaching'},True),
                    (3,'set_mode',{'mode':'test'},True),
                    (5,'reset',{},True),(7,'pause',{},True),(8,'resume',{},True),
                    (9,'demo',{'action':'ADD_ONE','target':'tray_A'},False)]:
                    while time.monotonic_ns() < started+int(offset*1e9):
                        if (directory/'stop').exists(): return
                        await asyncio.sleep(.01)
                    request=dict(version=1,kind='private_command',control_session_id=config['control_session_id'],
                        request_id=uuid.uuid4().hex,command=command,args=args)
                    before=time.monotonic_ns();await private.send(json.dumps(request))
                    reply=json.loads(await asyncio.wait_for(private.recv(),2.))
                    after=time.monotonic_ns()
                    row=dict(request=request,reply=reply,rtt_ms=(after-before)/1e6,
                        request_host_ns=str(before),response_host_ns=str(after))
                    report['commands'].append(row)
                    if reply.get('request_id')!=request['request_id'] or reply.get('accepted') is not accepted:
                        raise RuntimeError('Unexpected actual command reply')
                    if command in ('reset',) or command=='set_mode' and args=={'mode':'test'}:
                        if reply.get('reset_ok') is not True: raise RuntimeError('Actual reset acknowledgement missing')
            await asyncio.gather(receive(),commands())
    try: asyncio.run(work())
    except Exception as error: report['error']=type(error).__name__+': '+str(error)
    finally: save(directory/'client-result.json',report)
    return 1 if report['error'] else 0


if __name__=='__main__': raise SystemExit(run(sys.argv[1]))
