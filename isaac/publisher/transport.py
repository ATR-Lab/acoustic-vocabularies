"""websockets 12 transport, already present in the approved Isaac image.

An isolated Unix listener is preferred. A TCP listener requires explicit host
and port; never changes host networking. Only state, echo and public health live
here. The private command dispatcher must use its own authorized channel.
"""
from __future__ import annotations

import asyncio
from http import HTTPStatus
from pathlib import Path
import threading
import time

from .protocol import encode, number, strict_loads


class WebSocketTransport:
    def __init__(self, *, socket_path=None, host=None, port=None):
        if ((socket_path is not None and (host is not None or port is not None)) or
                (socket_path is None and (host is None or port is None))):
            raise ValueError("Choose one explicit Unix socket or TCP host/port")
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
            raise ValueError("Invalid station port")
        self.path = Path(socket_path) if socket_path is not None else None
        if self.path is not None and self.path.exists():
            raise FileExistsError("Inspect existing socket before replacing it")
        self.host, self.port = host, port
        self.loop = asyncio.new_event_loop()
        self.lock = threading.Lock()
        self.pending = None
        self.scheduled = False
        self.clients = set()
        self.overwrites = 0
        self.failed = None
        self.closed = False
        self.health_provider = lambda: {"version": 1, "kind": "publisher_health", "fault": "STARTING"}
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="public-scene-websocket", daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.failed:
            raise RuntimeError("Public state listener failed to start") from self.failed
        self.socket_identity = self.path.stat() if self.path is not None else None

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            from websockets.server import serve, unix_serve
            settings = dict(process_request=self._request, max_size=1024, max_queue=4,
                            ping_interval=10, ping_timeout=10, close_timeout=2, compression=None)
            listener = unix_serve(self._client, str(self.path), **settings) if self.path else serve(self._client, self.host, self.port, **settings)
            self.server = self.loop.run_until_complete(listener)
            self.ready.set()
            self.loop.run_forever()
        except Exception as error:
            self.failed = error
            self.ready.set()
        finally:
            self.loop.close()

    async def _request(self, path, headers):
        if path == "/health":
            body = encode(self.health_provider()).encode("utf-8")
            return HTTPStatus.OK, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], body
        if path != "/state":
            return HTTPStatus.NOT_FOUND, [], b"Unknown endpoint\n"
        return None

    async def _client(self, websocket, _path=None):
        from websockets.exceptions import ConnectionClosed
        outgoing = asyncio.Queue(maxsize=1)
        with self.lock:
            self.clients.add(outgoing)

        async def sender():
            try:
                while True:
                    await websocket.send(await outgoing.get())
            except ConnectionClosed:
                pass

        send_task = asyncio.create_task(sender())
        try:
            async for raw in websocket:
                received = time.monotonic_ns()
                try:
                    request = strict_loads(raw)
                    if not isinstance(request, dict) or set(request) != {"kind", "c0_s"} or request["kind"] != "echo":
                        raise ValueError("Only echo requests are public")
                    if not number(request["c0_s"]) or request["c0_s"] < 0:
                        raise ValueError("Invalid echo clock")
                    await websocket.send(encode(dict(kind="echo", c0_s=request["c0_s"], s1_ns=str(received), s2_ns=str(time.monotonic_ns()))))
                except (ValueError, TypeError):
                    await websocket.close(code=1008, reason="Invalid public request")
                    break
        except ConnectionClosed:
            pass
        finally:
            with self.lock:
                self.clients.discard(outgoing)
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)

    def submit(self, payload):
        with self.lock:
            if self.closed or self.failed or not self.thread.is_alive():
                raise RuntimeError("Public transport unavailable")
            if self.pending is not None:
                self.overwrites += 1
            self.pending = payload
            if not self.scheduled:
                self.scheduled = True
                self.loop.call_soon_threadsafe(self._drain)

    def _drain(self):
        with self.lock:
            payload, self.pending = self.pending, None
            self.scheduled = False
            clients = tuple(self.clients)
        for outgoing in clients:
            if outgoing.full():
                outgoing.get_nowait()
                with self.lock:
                    self.overwrites += 1
            outgoing.put_nowait(payload)

    def metrics(self):
        with self.lock:
            return dict(connected_clients=len(self.clients), queue_overwrites=self.overwrites,
                        failed=self.failed is not None or (not self.closed and not self.thread.is_alive()))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if self.thread.is_alive():
            async def finish():
                self.server.close()
                await self.server.wait_closed()
            asyncio.run_coroutine_threadsafe(finish(), self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("Public transport did not stop")
        if self.path is not None and self.path.is_socket():
            identity = self.path.stat()
            if (identity.st_dev, identity.st_ino) == (self.socket_identity.st_dev, self.socket_identity.st_ino):
                self.path.unlink()
