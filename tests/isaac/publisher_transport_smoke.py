"""Real network-free transport checks, run with approved Isaac websockets 12.

python tests/isaac/publisher_transport_smoke.py --output <ignored-file>
Synthetic schema fixtures only; not performance or live-source evidence.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from isaac.publisher.protocol import strict_loads
from isaac.publisher.transport import WebSocketTransport


async def check(socket_path, fixture):
    from websockets.client import unix_connect
    from websockets.exceptions import ConnectionClosed
    transport = WebSocketTransport(socket_path=socket_path)
    transport.health_provider = lambda: {"kind":"publisher_health", "fault":None}
    messages = 0
    try:
        async with unix_connect(socket_path, uri="ws://localhost/state", compression=None) as client:
            await client.send('{"kind":"echo","c0_s":1.25}')
            reply = strict_loads(await asyncio.wait_for(client.recv(), 3))
            assert reply["c0_s"] == 1.25 and int(reply["s2_ns"]) >= int(reply["s1_ns"])
            for sequence in range(10):
                fixture["seq"] = sequence
                transport.submit(json.dumps(fixture))
                received = strict_loads(await asyncio.wait_for(client.recv(), 3))
                assert received["seq"] == sequence
                messages += 1
            await client.send('{"kind":"demo","target":"synthetic"}')
            try:
                await asyncio.wait_for(client.recv(), 3)
                raise AssertionError("Command accepted on public channel")
            except ConnectionClosed as error:
                assert error.code == 1008
        async with unix_connect(socket_path, uri="ws://localhost/state", compression=None) as client:
            await client.send('{"kind":"echo","kind":"demo","c0_s":1}')
            try:
                await asyncio.wait_for(client.recv(), 3)
                raise AssertionError("Duplicate JSON field accepted")
            except ConnectionClosed as error:
                assert error.code == 1008
        async with unix_connect(socket_path, uri="ws://localhost/state", compression=None) as client:
            await client.send('{"kind":"echo","c0_s":2.0}')
            assert strict_loads(await asyncio.wait_for(client.recv(), 3))["c0_s"] == 2.0
        reader, writer = await asyncio.open_unix_connection(socket_path)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 3)
        writer.close()
        await writer.wait_closed()
        assert b"200 OK" in response and b"publisher_health" in response
        return dict(source_kind="synthetic", passed=True, received_states=messages,
                    checks=["state delivery", "correlated echo", "private command rejected",
                            "duplicate field rejected", "reconnect", "HTTP health", "owned socket cleanup"],
                    elapsed_s=time.monotonic()-started, device_acceptance=False)
    finally:
        transport.close()
        assert not Path(socket_path).exists()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads((Path(__file__).resolve().parents[1]/"fixtures/publisher-state.json").read_text())
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="publisher-check-") as folder:
        result = asyncio.run(check(str(Path(folder)/"state.sock"), fixture))
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))
