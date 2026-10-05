"""Actual approved WebSocket library transport, synthetic command backend only."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from isaac.commands import CommandDispatcher, CommandQueue
from isaac.commands.transport import PrivateCommandTransport


class SyntheticReset:
    adapter = SimpleNamespace(sim_time=0.)
    exposure_ready = False
    def reset(self):
        self.exposure_ready = True
        self.verified_at = time.monotonic_ns()/1e6
        return {"reset_ok": True}
    def verify_current(self):
        return {"reset_ok": True}
    def verification_status(self):
        stamp = getattr(self, "verified_at", None)
        return {"verified": stamp is not None, "host_mono_ms": stamp,
                "age_ms": None if stamp is None else time.monotonic_ns()/1e6-stamp}


@unittest.skipUnless(importlib.util.find_spec("websockets"), "requires approved preinstalled websockets")
class CommandTransportTest(unittest.TestCase):
    @staticmethod
    def probe(dispatcher, **changes):
        value = dict(version=1, kind="private_health_probe",
                     control_session_id=dispatcher.control_session_id, request_id=uuid.uuid4().hex)
        value.update(changes)
        return value

    def run_client(self, handoff, coroutine):
        errors, results = [], []
        def client():
            try:
                results.append(asyncio.run(coroutine))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=client)
        thread.start()
        deadline = time.monotonic()+5
        while thread.is_alive() and time.monotonic() < deadline:
            handoff.drain()
            time.sleep(.005)
        thread.join(.1)
        self.assertFalse(thread.is_alive(), "bounded client deadline exceeded")
        if errors:
            raise errors[0]
        return results[0]

    def test_tcp_peer_restriction_and_private_command(self):
        from websockets.client import connect
        from websockets.exceptions import InvalidStatusCode
        events = []
        dispatcher = CommandDispatcher(SyntheticReset(), events.append, station_id="engineering-fixture", allowed_client="127.0.0.1")
        handoff = CommandQueue(dispatcher)
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        service = PrivateCommandTransport(handoff, host="127.0.0.1", port=port)
        async def clients():
            uri = f"ws://127.0.0.1:{port}/commands"
            async with connect(uri) as ws:
                await ws.send(json.dumps(dict(version=1, kind="private_command", control_session_id=dispatcher.control_session_id,
                                              request_id=uuid.uuid4().hex, command="reset", args={})))
                response = json.loads(await ws.recv())
                self.assertTrue(response["reset_ok"])
            with self.assertRaises(InvalidStatusCode) as refused:
                async with connect(uri, local_addr=("127.0.0.2", 0)):
                    pass
            self.assertEqual(refused.exception.status_code, 403)
            with self.assertRaises(InvalidStatusCode) as wrong_path:
                async with connect(f"ws://127.0.0.1:{port}/state"):
                    pass
            self.assertEqual(wrong_path.exception.status_code, 404)
            return response
        try:
            self.run_client(handoff, clients())
        finally:
            service.close()
        self.assertEqual([e["reply"]["reason"] for e in events], ["RESET_COMPLETE", "UNKNOWN_CLIENT"])

    def test_read_only_probes_need_no_owner_drain_or_command_capacity(self):
        from websockets.client import connect
        events = []
        reset = SyntheticReset(); reset.reset()
        dispatcher = CommandDispatcher(reset, events.append, station_id="engineering-fixture",
                                       allowed_client="127.0.0.1", cache_size=2)
        handoff = CommandQueue(dispatcher)
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0)); port = free.getsockname()[1]
        service = PrivateCommandTransport(handoff, host="127.0.0.1", port=port)
        async def clients():
            uri = f"ws://127.0.0.1:{port}/commands"
            async with connect(uri) as ws:
                first = None
                for _ in range(1100):  # Exceeds both default1024 and this fixture's two-entry cache.
                    value = self.probe(dispatcher)
                    await ws.send(json.dumps(value))
                    response = json.loads(await asyncio.wait_for(ws.recv(), 1))
                    self.assertEqual(set(response), {"version", "kind", "control_session_id", "request_id", "accepted", "reason", "health"})
                    self.assertEqual(response["kind"], "private_health_reply")
                    self.assertEqual(response["request_id"], value["request_id"])
                    self.assertEqual(response["control_session_id"], dispatcher.control_session_id)
                    self.assertTrue(response["accepted"])
                    self.assertEqual(response["reason"], "HEALTH")
                    if first is None: first = response
                await asyncio.sleep(.3)  # Owner has not drained or reverified.
                await ws.send(json.dumps(value))  # Read-only ID replay cannot freshen verification.
                stale = json.loads(await asyncio.wait_for(ws.recv(), 1))
                self.assertGreaterEqual(stale["health"]["neutral_verification_age_ms"], 300)
                self.assertGreater(stale["health"]["health_sample_host_mono_ms"], first["health"]["health_sample_host_mono_ms"])
                self.assertFalse(stale["health"]["exposure_ready"])
                for change, reason in [({"version":1.0}, "MALFORMED_PROBE"),
                        ({"command":"reset"}, "MALFORMED_PROBE"),
                        ({"target":"tray_A"}, "MALFORMED_PROBE"),
                        ({"control_session_id":"0"*32}, "CONTROL_SESSION_MISMATCH")]:
                    bad = self.probe(dispatcher, **change)
                    await ws.send(json.dumps(bad))
                    refused = json.loads(await asyncio.wait_for(ws.recv(), 1))
                    self.assertEqual(refused["request_id"], bad["request_id"])
                    self.assertFalse(refused["accepted"])
                    self.assertEqual(refused["reason"], reason)
                    self.assertIsNone(refused["health"])
            async with connect(uri) as abandoned:
                await abandoned.send(json.dumps(self.probe(dispatcher)))
                # Deliberately abandon its reply; this never admits a command.
            async with connect(uri) as recovered:
                fresh = self.probe(dispatcher)
                await recovered.send(json.dumps(fresh))
                self.assertEqual(json.loads(await recovered.recv())["request_id"], fresh["request_id"])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            http = await asyncio.wait_for(reader.read(), 2)
            writer.close(); await writer.wait_closed()
            header, body = http.split(b"\r\n\r\n", 1)
            self.assertTrue(header.startswith(b"HTTP/1.1 200 "))
            self.assertEqual(json.loads(body)["control_session_id"], dispatcher.control_session_id)
        try:
            asyncio.run(asyncio.wait_for(clients(), 15))  # No run_client()/handoff.drain() here.
            self.assertEqual(events, [])
            self.assertEqual(len(dispatcher.cache), 0)
            self.assertEqual(handoff.sequence, 0)
            self.assertTrue(handoff.queue.empty())
            self.assertIsNone(dispatcher.fault)
        finally:
            service.close()

    def test_probe_command_interleaving_and_existing_wire_refusals(self):
        from websockets.client import connect
        from websockets.exceptions import ConnectionClosedError
        events = []
        dispatcher = CommandDispatcher(SyntheticReset(), events.append, station_id="engineering-fixture", allowed_client="127.0.0.1")
        handoff = CommandQueue(dispatcher)
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0)); port = free.getsockname()[1]
        service = PrivateCommandTransport(handoff, host="127.0.0.1", port=port)
        async def clients():
            uri = f"ws://127.0.0.1:{port}/commands"
            async with connect(uri) as ws:
                value = dict(version=1, kind="private_command", control_session_id=dispatcher.control_session_id,
                             request_id=uuid.uuid4().hex, command="reset", args={})
                await ws.send(json.dumps(value))
                response = json.loads(await ws.recv())
                self.assertEqual(response["request_id"], value["request_id"])
                self.assertEqual(response["reason"], "RESET_COMPLETE")
                for _ in range(3):
                    probe = self.probe(dispatcher)
                    await ws.send(json.dumps(probe))
                    self.assertEqual(json.loads(await ws.recv())["request_id"], probe["request_id"])
                await ws.send('{"kind":"private_health_probe","kind":"private_health_probe"}')
                self.assertEqual(json.loads(await ws.recv())["reason"], "MALFORMED")
                await ws.send(b'{"kind":"private_health_probe"}')
                self.assertEqual(json.loads(await ws.recv())["reason"], "MALFORMED")
            async with connect(uri) as oversized:
                await oversized.send("x"*16385)
                with self.assertRaises(ConnectionClosedError) as closed:
                    await oversized.recv()
                self.assertEqual(closed.exception.code, 1009)
        try:
            self.run_client(handoff, clients())
        finally:
            service.close()
        self.assertEqual([event["reply"]["reason"] for event in events], ["RESET_COMPLETE", "MALFORMED", "MALFORMED"])
        self.assertEqual(len(dispatcher.cache), 1)

    @unittest.skipUnless(hasattr(socket, "SO_PEERCRED"), "Linux Unix peer credentials required")
    def test_unix_permissions_and_same_uid(self):
        from websockets.client import unix_connect
        events = []
        dispatcher = CommandDispatcher(SyntheticReset(), events.append, station_id="engineering-fixture", allowed_client=f"uid:{os.getuid()}")
        handoff = CommandQueue(dispatcher)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"private.sock"
            service = PrivateCommandTransport(handoff, socket_path=path, allowed_uid=os.getuid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            async def client():
                async with unix_connect(str(path), uri="ws://localhost/commands") as ws:
                    await ws.send(json.dumps(self.probe(dispatcher)))
                    return json.loads(await ws.recv())
            try:
                self.assertTrue(self.run_client(handoff, client())["accepted"])
            finally:
                service.close()
            self.assertFalse(path.exists())
        self.assertEqual(events, [])

    @unittest.skipUnless(hasattr(socket, "SO_PEERCRED"), "Linux Unix peer credentials required")
    def test_unix_wrong_uid_is_refused_before_probe_admission(self):
        from websockets.client import unix_connect
        from websockets.exceptions import InvalidStatusCode
        events = []
        expected_uid = os.getuid()+1
        dispatcher = CommandDispatcher(SyntheticReset(), events.append, station_id="engineering-fixture", allowed_client=f"uid:{expected_uid}")
        handoff = CommandQueue(dispatcher)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"private.sock"
            service = PrivateCommandTransport(handoff, socket_path=path, allowed_uid=expected_uid)
            async def client():
                with self.assertRaises(InvalidStatusCode) as refused:
                    async with unix_connect(str(path), uri="ws://localhost/commands"):
                        self.fail("Wrong UID passed handshake")
                self.assertEqual(refused.exception.status_code, 403)
            try:
                asyncio.run(client())
                self.assertTrue(handoff.queue.empty())
            finally:
                service.close()
            self.assertFalse(path.exists())
        self.assertEqual([event["reply"]["reason"] for event in events], ["UNKNOWN_CLIENT"])


if __name__ == "__main__":
    unittest.main()
