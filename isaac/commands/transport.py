"""Private WebSocket listener with peer checks before protocol upgrade.

Uses the previously approved websockets 12 image dependency. Only explicit
loopback TCP or permission-restricted Unix sockets are supported here. Station
network deployment is #57. The public state listener is a separate object/path.
"""
import asyncio
from http import HTTPStatus
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import threading


class PrivateCommandTransport:
    def __init__(self, handoff, *, socket_path=None, allowed_uid=None, host=None, port=None):
        if (socket_path is None) == (host is None):
            raise ValueError("choose exactly one Unix socket or explicit loopback TCP listener")
        if socket_path is not None:
            if type(allowed_uid) is not int or allowed_uid < 0 or handoff.dispatcher.allowed_client != f"uid:{allowed_uid}":
                raise ValueError("Unix peer uid must match configured private client")
        else:
            if not ipaddress.ip_address(host).is_loopback or type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError("only explicit loopback diagnostic TCP is implemented before #57")
            ipaddress.ip_address(handoff.dispatcher.allowed_client)
        self.path = Path(socket_path) if socket_path is not None else None
        if self.path is not None and self.path.exists():
            raise FileExistsError("inspect existing private socket before replacing it")
        self.handoff, self.host, self.port, self.allowed_uid = handoff, host, port, allowed_uid
        self.loop, self.ready, self.failed = asyncio.new_event_loop(), threading.Event(), None
        self.thread = threading.Thread(target=self._run, daemon=True, name="private-command-websocket")
        self.thread.start()
        if not self.ready.wait(10) or self.failed:
            raise RuntimeError("private listener did not start") from self.failed
        self.identity = self.path.stat() if self.path else None

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            from websockets.server import WebSocketServerProtocol, serve, unix_serve
            owner = self
            class RestrictedProtocol(WebSocketServerProtocol):
                async def process_request(self, path, headers):
                    if owner.path:
                        raw = self.transport.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        _, uid, _ = struct.unpack("3i", raw)
                        self.private_peer = f"uid:{uid}"
                    else:
                        self.private_peer = str(ipaddress.ip_address(self.remote_address[0]))
                    if self.private_peer != owner.handoff.dispatcher.allowed_client:
                        owner.handoff.deny("HTTP upgrade " + path, self.private_peer, "UNKNOWN_CLIENT")
                        return HTTPStatus.FORBIDDEN, [], b"Private client refused\n"
                    if path == "/health":
                        body = json.dumps(owner.handoff.health(), allow_nan=False).encode()
                        return HTTPStatus.OK, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], body
                    if path != "/commands":
                        return HTTPStatus.NOT_FOUND, [], b"Unknown private endpoint\n"
                    return None
            kwargs = dict(create_protocol=RestrictedProtocol, max_size=16384, max_queue=4,
                          compression=None, ping_interval=10, ping_timeout=10, close_timeout=2)
            listener = unix_serve(self._client, str(self.path), **kwargs) if self.path else serve(self._client, self.host, self.port, **kwargs)
            self.server = self.loop.run_until_complete(listener)
            if self.path:
                os.chmod(self.path, 0o600)
            self.ready.set()
            self.loop.run_forever()
        except Exception as error:
            self.failed = error
            self.ready.set()
        finally:
            self.loop.close()

    async def _client(self, websocket, _path=None):
        from websockets.exceptions import ConnectionClosed
        try:
            async for raw in websocket:
                if not isinstance(raw, str):
                    reply = self.handoff.deny("binary frame", websocket.private_peer, "MALFORMED")
                else:
                    future = self.handoff.submit(raw, websocket.private_peer)
                    # Disconnects do not cancel an admitted command or its log.
                    reply = await asyncio.shield(asyncio.wrap_future(future))
                await websocket.send(json.dumps(reply, allow_nan=False))
        except ConnectionClosed:
            pass

    def close(self):
        if self.thread.is_alive():
            async def finish():
                self.server.close()
                await self.server.wait_closed()
            asyncio.run_coroutine_threadsafe(finish(), self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("private listener did not stop")
        if self.path is not None and self.path.is_socket():
            current = self.path.stat()
            if (current.st_dev, current.st_ino) == (self.identity.st_dev, self.identity.st_ino):
                self.path.unlink()
