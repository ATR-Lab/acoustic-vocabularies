"""Bounded host-loopback to owned Unix-socket relay for isolated E2E only.

No network interface or firewall setting is changed. Bytes, including source
timestamps and private acknowledgements, are forwarded without interpretation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import stat
import time


def validate_endpoint(path, port, seconds):
    path=Path(path)
    if not path.is_absolute() or any(parent.is_symlink() for parent in (path,*path.parents)):
        raise ValueError('Explicit absolute non-symlink Unix endpoint required')
    info=path.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Relay UID must own permission0600 Unix endpoint')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Explicit unprivileged loopback port required')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 5 <= seconds <= 3600:
        raise ValueError('Bounded relay duration5..3600 seconds required')
    return info.st_dev,info.st_ino


class RelayTrace:
    """Opt-in byte-count/timestamp observer; never retains or interprets bytes.

    Rows are (host monotonic ns, connection index, direction, event, bytes):
    direction 0 is TCP client to Unix service, 1 is service to TCP client;
    event 0 is the read call returning, 1 is write drain completion.
    Capacity is fixed; overflow drops rows and marks coverage incomplete.
    """
    def __init__(self, path, capacity=200000):
        self.path=Path(path)
        if self.path.exists(): raise FileExistsError('Do not replace relay trace evidence')
        if type(capacity) is not int or not 1<=capacity<=400000: raise ValueError('Bounded trace capacity required')
        self.rows=[];self.capacity=capacity;self.dropped=0;self.started=time.monotonic_ns()

    def record(self, connection, direction, event, size):
        stamp=time.monotonic_ns()
        if len(self.rows)<self.capacity: self.rows.append((stamp,connection,direction,event,size))
        else: self.dropped+=1

    def close(self):
        report=dict(version=1,scope='relay byte-timing diagnostic',qualification=False,
            clock='Python time.monotonic_ns on relay host',fields=('host_ns','connection','direction','event','bytes'),
            directions={'0':'tcp_to_unix','1':'unix_to_tcp'},events={'0':'read_returned','1':'write_drained'},
            start_host_ns=self.started,closed_host_ns=time.monotonic_ns(),capacity=self.capacity,
            events_recorded=len(self.rows),dropped=self.dropped,complete=self.dropped==0,rows=self.rows)
        with self.path.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,separators=(',',':'));stream.write('\n');stream.flush();os.fsync(stream.fileno())


async def relay(path, port, seconds, trace=None):
    identity=validate_endpoint(path,port,seconds)
    peers=set(); tasks=set(); stopping=asyncio.Event(); count=failures=0
    async def handle(reader,writer):
        nonlocal count,failures
        task=asyncio.current_task();tasks.add(task);peers.add(writer)
        upstream=None
        try:
            if len(tasks)>16: raise RuntimeError('Relay connection limit')
            if validate_endpoint(path,port,seconds)!=identity: raise RuntimeError('Unix endpoint replaced')
            upstream_reader,upstream=await asyncio.wait_for(asyncio.open_unix_connection(str(path)),2)
            peers.add(upstream);count+=1;connection=count
            async def pump(source,target,direction):
                while True:
                    data=await source.read(65536)
                    if trace is not None: trace.record(connection,direction,0,len(data))
                    if not data: break
                    target.write(data);await target.drain()
                    if trace is not None: trace.record(connection,direction,1,len(data))
            copies=[asyncio.create_task(pump(reader,upstream,0)),asyncio.create_task(pump(upstream_reader,writer,1))]
            try:
                done,pending=await asyncio.wait(copies,return_when=asyncio.FIRST_COMPLETED)
                for work in done:work.result()
            finally:
                for work in copies:work.cancel()
                await asyncio.gather(*copies,return_exceptions=True)
        except (Exception,asyncio.CancelledError):
            failures+=1
        finally:
            for stream in (writer,upstream):
                if stream is not None:
                    peers.discard(stream);stream.close()
                    try:await asyncio.wait_for(stream.wait_closed(),2)
                    except Exception:pass
            tasks.discard(task)
    server=await asyncio.start_server(handle,'127.0.0.1',port,limit=65536)
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stopping.set)
    started=time.monotonic()
    print(json.dumps(dict(event='ready',scope='SIMULATION_TEST',participant=False,pid=os.getpid(),
        listen='127.0.0.1',port=port,seconds=seconds)),flush=True)
    try:
        try:await asyncio.wait_for(stopping.wait(),seconds)
        except asyncio.TimeoutError:pass
    finally:
        server.close();await server.wait_closed()
        for stream in tuple(peers):stream.close()
        for task in tuple(tasks):task.cancel()
        await asyncio.gather(*tuple(tasks),return_exceptions=True)
        for sig in (signal.SIGTERM,signal.SIGINT):loop.remove_signal_handler(sig)
        print(json.dumps(dict(event='closed',connections=count,connection_errors=failures,
                              elapsed_seconds=time.monotonic()-started)),flush=True)
        if trace is not None: trace.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--seconds',type=float,required=True)
    parser.add_argument('--trace',type=Path,help='Opt-in fresh JSON path for byte-timing rows (diagnostic only)')
    args=parser.parse_args()
    asyncio.run(relay(args.socket,args.port,args.seconds,RelayTrace(args.trace) if args.trace else None))


if __name__=='__main__':main()
