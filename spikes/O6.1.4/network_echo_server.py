import asyncio, hashlib, json, os, pathlib, socket, struct, time
from websockets.server import WebSocketServerProtocol, unix_serve
from websockets.exceptions import ConnectionClosed
OUT = pathlib.Path('/diagnostic')
events = []
bulk_count = 0

class Peer(WebSocketServerProtocol):

    async def process_request(self, path, headers):
        from http import HTTPStatus
        _, uid, _ = struct.unpack('3i', self.transport.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 1005:
            return (HTTPStatus.FORBIDDEN, [], b'refused')
        if path not in ('/echo', '/load'):
            return (HTTPStatus.NOT_FOUND, [], b'unknown')

async def handler(ws, path):
    global bulk_count
    try:
        if path == '/load':
            payload = 'x' * 10799
            while True:
                await ws.send(payload)
                bulk_count += 1
                await asyncio.sleep(1 / 16)
        else:
            async for raw in ws:
                received = time.monotonic_ns()
                value = json.loads(raw)
                assert set(value) == {'kind', 'request_id'} and value['kind'] == 'diagnostic_echo'
                assert len(value['request_id']) == 32 and all((c in '0123456789abcdef' for c in value['request_id']))
                reply = json.dumps({'kind': 'diagnostic_echo', 'request_id': value['request_id'], 'padding': 'x' * 500})
                before = time.monotonic_ns()
                await ws.send(reply)
                after = time.monotonic_ns()
                assert len(events) < 4000
                events.append([value['request_id'], received, before, after, len(reply)])
    except ConnectionClosed:
        pass

async def main():
    started = time.monotonic_ns()
    servers = []
    try:
        for name in ('private.sock', 'public.sock'):
            p = OUT / name
            assert not p.exists()
            server = await unix_serve(handler, str(p), create_protocol=Peer, max_size=16384, max_queue=4, compression=None, ping_interval=10, ping_timeout=10, close_timeout=1)
            servers.append(server)
            os.chmod(p, 384)
            os.chown(p, 1005, -1)
        print('ready', flush=True)
        while time.monotonic_ns() - started < 300000000000 and (not (OUT / 'stop').exists()):
            await asyncio.sleep(0.05)
    finally:
        for server in servers:
            server.close()
        for server in servers:
            await server.wait_closed()
        result = dict(scope='Synthetic non-GPU network-path echo only', participant=False, qualification=False, started_host_ns=started, ended_host_ns=time.monotonic_ns(), bulk_frames=bulk_count, bulk_frame_bytes=10799, events=events)
        with (OUT / 'server.json').open('x') as f:
            json.dump(result, f)
            f.flush()
            os.fsync(f.fileno())
asyncio.run(main())
