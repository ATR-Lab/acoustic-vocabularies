"""Real local socket smoke, skipped without Unix sockets / existing websockets.

This is synthetic verification, not evidence of Isaac or network latency.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and sys.platform != "win32" and importlib.util.find_spec("websockets"), "requires Linux and preinstalled websockets")
class UnixTransportTests(unittest.TestCase):
    def test_state_echo_and_restart_identity(self):
        from publishers import CustomPublisher
        from websockets.client import unix_connect
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "public-state.sock")
            server = CustomPublisher(path, ["public_joint"], "synthetic")
            first_session = server.builder.session_id
            async def exercise():
                async with unix_connect(path) as client:
                    server.publish(.1, 1, [.2])
                    frame = json.loads(await asyncio.wait_for(client.recv(), 2))
                    self.assertEqual(frame["source_kind"], "synthetic")
                    self.assertEqual(frame["joint_positions"], [.2])
                    await client.send('{"kind":"echo","c0_s":"1.0"}')
                    echo = json.loads(await asyncio.wait_for(client.recv(), 2))
                    self.assertEqual(echo["c0_s"], "1.0")
                    self.assertGreaterEqual(int(echo["s2_ns"]), int(echo["s1_ns"]))
            try:
                asyncio.run(exercise())
            finally:
                server.close()
            restarted = CustomPublisher(path, ["public_joint"], "synthetic")
            self.assertNotEqual(first_session, restarted.builder.session_id)
            restarted.close()
