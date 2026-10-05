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
        return {"reset_ok": True}


@unittest.skipUnless(importlib.util.find_spec("websockets"), "requires approved preinstalled websockets")
class CommandTransportTest(unittest.TestCase):
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
                    await ws.send(json.dumps(dict(version=1, kind="private_command", control_session_id=dispatcher.control_session_id,
                                                  request_id=uuid.uuid4().hex, command="health", args={})))
                    return json.loads(await ws.recv())
            try:
                self.assertTrue(self.run_client(handoff, client())["accepted"])
            finally:
                service.close()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
