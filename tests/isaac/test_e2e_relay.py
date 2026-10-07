"""Actual bounded Linux loopback/Unix relay checks, without simulator or GPU."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from isaac.e2e.relay import RelayTrace, relay, validate_endpoint


@unittest.skipUnless(os.name=='posix','Linux Unix-socket diagnostic required')
class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'service.sock'
        async def echo(reader,writer):
            try:
                while data:=await reader.read(65536):
                    writer.write(data);await writer.drain()
            finally:
                writer.close();await writer.wait_closed()
        self.server=await asyncio.start_unix_server(echo,str(self.path))
        os.chmod(self.path,0o600)

    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()
        self.directory.cleanup()

    async def test_bytes_forward_unchanged_and_cancel_cleans_only_tcp_listener(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        task=asyncio.create_task(relay(self.path,port,5))
        writer=None
        try:
            for _ in range(100):
                try:reader,writer=await asyncio.open_connection('127.0.0.1',port);break
                except ConnectionRefusedError:await asyncio.sleep(.01)
            self.assertIsNotNone(writer)
            payload=b'GET /commands HTTP/1.1\r\n\r\n'+bytes(range(256))
            writer.write(payload);await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)),2),payload)
        finally:
            if writer:writer.close();await writer.wait_closed()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.path.is_socket())
        with self.assertRaises(ConnectionRefusedError):await asyncio.open_connection('127.0.0.1',port)

    async def test_opt_in_trace_records_counts_and_times_without_payload(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        out=Path(self.directory.name)/'relay-trace.json'
        trace=RelayTrace(out)
        task=asyncio.create_task(relay(self.path,port,5,trace))
        writer=None
        try:
            for _ in range(100):
                try:reader,writer=await asyncio.open_connection('127.0.0.1',port);break
                except ConnectionRefusedError:await asyncio.sleep(.01)
            payload=b'secret-probe-bytes'
            writer.write(payload);await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)),2),payload)
        finally:
            if writer:writer.close();await writer.wait_closed()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        report=json.loads(out.read_text())
        self.assertTrue(report['complete']);self.assertFalse(report['qualification'])
        events={(row[2],row[3]) for row in report['rows'] if row[4]==len(payload)}
        self.assertEqual(events,{(0,0),(0,1),(1,0),(1,1)})
        self.assertNotIn('secret-probe-bytes',out.read_text())
        with self.assertRaises(FileExistsError):RelayTrace(out)

    async def test_trace_overflow_is_counted_not_silent(self):
        trace=RelayTrace(Path(self.directory.name)/'small.json',capacity=1)
        trace.record(1,0,0,10);trace.record(1,0,1,10)
        self.assertEqual((len(trace.rows),trace.dropped),(1,1))
        trace.close()
        self.assertFalse(json.loads((Path(self.directory.name)/'small.json').read_text())['complete'])

    async def test_reject_world_readable_or_symlink_endpoint(self):
        os.chmod(self.path,0o666)
        with self.assertRaises(ValueError):validate_endpoint(self.path,18766,5)
        os.chmod(self.path,0o600)
        alias=Path(self.directory.name)/'alias.sock';alias.symlink_to(self.path)
        with self.assertRaises(ValueError):validate_endpoint(alias,18766,5)

    async def test_explicit_port_and_duration_bounds(self):
        for port,seconds in [(80,5),(18766,float('inf')),(18766,3601),(18766,0)]:
            with self.assertRaises(ValueError):validate_endpoint(self.path,port,seconds)
