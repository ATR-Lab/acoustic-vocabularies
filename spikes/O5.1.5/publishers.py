"""Attach one publisher to Isaac's loop; no simulation stepping or robot commands here.

Custom transport uses websockets 12 (present in inspected Isaac image), on a Unix
socket inside the network-isolated container. ROS transport requires enabled
Isaac ROS 2 bridge / rclpy and an isolated rosbridge server, not Unitree DDS.
"""
import asyncio
import json
from pathlib import Path
import threading
import time

from bridge_protocol import FrameBuilder, echo_reply, encode


class CustomPublisher:
    def __init__(self, socket_path, joint_names, source_kind="live"):
        self.builder = FrameBuilder(joint_names, source_kind)
        self.path = Path(socket_path)
        if self.path.exists():
            raise FileExistsError("Socket path already exists; inspect stale process/socket before choosing another")
        self.loop = asyncio.new_event_loop()
        self.clients = set()
        self.overwrites = 0
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("WebSocket publisher startup timed out")
        if self.error:
            raise RuntimeError("WebSocket publisher failed to start") from self.error
        self.socket_identity = self.path.stat()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            from websockets.server import unix_serve
            self.server = self.loop.run_until_complete(unix_serve(self._client, str(self.path), max_size=65536, ping_interval=20, compression=None))
        except Exception as error:
            self.error = error
            self.ready.set()
            return
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    async def _client(self, websocket):
        queue = asyncio.Queue(maxsize=1)
        self.clients.add(queue)
        async def send():
            while True:
                await websocket.send(await queue.get())
        sender = asyncio.create_task(send())
        try:
            async for raw in websocket:
                received = time.monotonic_ns()
                request = json.loads(raw)
                await websocket.send(encode(echo_reply(request, received)))
        finally:
            self.clients.discard(queue)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    def publish(self, sim_time, sim_step, joint_positions, objects=None):
        """Call from the simulation thread at fixed *host-clock* publish ticks."""
        frame = self.builder.build(sim_time, sim_step, joint_positions, objects)
        payload = encode(frame)
        def offer():
            for queue in self.clients:
                if queue.full():
                    queue.get_nowait()
                    self.overwrites += 1
                queue.put_nowait(payload)
        self.loop.call_soon_threadsafe(offer)
        return frame

    def close(self):
        async def finish():
            self.server.close()
            await self.server.wait_closed()
        future = asyncio.run_coroutine_threadsafe(finish(), self.loop)
        future.result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        # Remove only the socket this instance created, never a replacement path.
        if self.path.is_socket():
            identity = self.path.stat()
            if (identity.st_dev, identity.st_ino) == (self.socket_identity.st_dev, self.socket_identity.st_ino):
                self.path.unlink()


class RosPublisher:
    def __init__(self, joint_names, source_kind="live"):
        import rclpy
        from std_msgs.msg import String
        self.rclpy, self.String = rclpy, String
        self.builder = FrameBuilder(joint_names, source_kind)
        self.owns_context = not rclpy.ok()
        if self.owns_context:
            rclpy.init()
        self.node = rclpy.create_node("public_state_spike")
        self.state = self.node.create_publisher(String, "/spike/state", 1)
        self.echo = self.node.create_publisher(String, "/spike/echo/reply", 10)
        self.subscription = self.node.create_subscription(String, "/spike/echo/request", self._echo, 10)
        self.thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True)
        self.thread.start()

    def _echo(self, message):
        received = time.monotonic_ns()
        try:
            response = self.String()
            response.data = encode(echo_reply(json.loads(message.data), received))
            self.echo.publish(response)
        except (ValueError, TypeError, json.JSONDecodeError):
            self.node.get_logger().warning("Rejected malformed echo request")

    def publish(self, sim_time, sim_step, joint_positions, objects=None):
        frame = self.builder.build(sim_time, sim_step, joint_positions, objects)
        message = self.String()
        message.data = encode(frame)
        self.state.publish(message)
        return frame

    def close(self):
        self.node.destroy_node()
        if self.owns_context:
            self.rclpy.shutdown()
        self.thread.join(timeout=5)
