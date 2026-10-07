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


async def relay(path, port, seconds):
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
            peers.add(upstream);count+=1
            async def pump(source,target):
                while True:
                    data=await source.read(65536)
                    if not data: break
                    target.write(data);await target.drain()
            copies=[asyncio.create_task(pump(reader,upstream)),asyncio.create_task(pump(upstream_reader,writer))]
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--seconds',type=float,required=True)
    args=parser.parse_args();asyncio.run(relay(args.socket,args.port,args.seconds))


if __name__=='__main__':main()
