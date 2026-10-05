"""Standard-library Windows diagnostic WS collector through an SSH local forward.

Only loopback ws:// endpoints. It does not animate Unity, qualify source age or
establish Quest/IL2CPP acceptance. All rows explicitly mark those checks false.
"""
import argparse
import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import time
from urllib.parse import urlparse
from bridge_protocol import validate_frame


class LoopbackWebSocket:
    def __init__(self, uri):
        parsed = urlparse(uri)
        if parsed.scheme != "ws" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.username or parsed.password:
            raise ValueError("Use a local SSH-forwarded ws://127.0.0.1 endpoint")
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=5)
        self.stream = self.socket.makefile("rb")
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET {parsed.path or '/'} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port or 80}\r\n"
                   f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.socket.sendall(request.encode("ascii"))
        if not self.stream.readline(4096).startswith(b"HTTP/1.1 101 "):
            raise RuntimeError("WebSocket handshake not accepted")
        headers, total = {}, 0
        while True:
            line = self.stream.readline(4096)
            total += len(line)
            if total > 65536 or not line:
                raise RuntimeError("Invalid upgrade response")
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            headers[name.lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("Bad WebSocket accept proof")

    def send(self, payload, opcode=1):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        length = len(payload)
        header = bytes([0x80 | opcode, 0x80 | (length if length < 126 else 126 if length < 65536 else 127)])
        if length >= 126:
            header += struct.pack("!H" if length < 65536 else "!Q", length)
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

    def read(self, count):
        data = self.stream.read(count)
        if len(data) != count:
            raise EOFError("WebSocket ended")
        return data

    def receive(self):
        message = bytearray()
        started = False
        while True:
            a, b = self.read(2)
            final, opcode, length = bool(a & 128), a & 15, b & 127
            if a & 112 or b & 128:
                raise ValueError("Unsupported server frame flags")
            if length >= 126:
                length = struct.unpack("!H" if length == 126 else "!Q", self.read(2 if length == 126 else 8))[0]
            if length + len(message) > 262144:
                raise ValueError("Oversized WebSocket payload")
            payload = self.read(length)
            if opcode == 8:
                raise EOFError("Peer closed")
            if opcode == 9:
                self.send(payload, 10)
                continue
            if opcode == 10:
                continue
            if (not started and opcode != 1) or (started and opcode != 0):
                raise ValueError("Unexpected WebSocket opcode")
            started = True
            message.extend(payload)
            if final:
                return message.decode("utf-8")

    def close(self):
        try:
            self.send(b"", 8)
        finally:
            self.stream.close()
            self.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ws://127.0.0.1:18765")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--candidate", choices=("custom", "rosbridge"), default="custom")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fields = "event session_id seq publish_host_ns recv_client_s sim_time sim_step c0_s s1_ns s2_ns c3_s apply_ms queue_drops source_kind source_fresh applied diagnostic".split()
    count = 0
    client = LoopbackWebSocket(args.uri)
    if args.candidate == "rosbridge":
        for topic in ("/spike/state", "/spike/echo/reply"):
            client.send(json.dumps(dict(op="subscribe", topic=topic, type="std_msgs/msg/String", queue_length=1)))
        client.send(json.dumps(dict(op="advertise", topic="/spike/echo/request", type="std_msgs/msg/String")))
    start, next_echo = time.perf_counter(), time.perf_counter()
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(dict(event="diagnostic_mode", recv_client_s=time.perf_counter(), diagnostic="true"))
        writer.writerow(dict(event="run_start", recv_client_s=time.perf_counter()))
        try:
            while time.perf_counter() - start < args.seconds:
                now = time.perf_counter()
                if now >= next_echo:
                    echo = json.dumps({"kind": "echo", "c0_s": format(now, ".17g")})
                    if args.candidate == "rosbridge":
                        echo = json.dumps(dict(op="publish", topic="/spike/echo/request", msg=dict(data=echo)))
                    client.send(echo)
                    next_echo = now + 1
                message = json.loads(client.receive())
                received = time.perf_counter()
                if args.candidate == "rosbridge":
                    if message.get("op") != "publish" or message.get("topic") not in ("/spike/state", "/spike/echo/reply"):
                        continue
                    message = json.loads(message["msg"]["data"])
                if message["kind"] == "state":
                    validate_frame(message)
                    writer.writerow(dict(event="state", session_id=message["session_id"], seq=message["seq"],
                        publish_host_ns=message["host_monotonic_ns"], recv_client_s=received, sim_time=message["sim_time"],
                        sim_step=message["sim_step"], queue_drops=0, source_kind=message["source_kind"], source_fresh="false", applied="false", diagnostic="true"))
                    count += 1
                elif message["kind"] == "echo":
                    writer.writerow(dict(event="echo", recv_client_s=received, c0_s=message["c0_s"],
                        s1_ns=message["s1_ns"], s2_ns=message["s2_ns"], c3_s=received))
            writer.writerow(dict(event="run_end", recv_client_s=time.perf_counter()))
        finally:
            stream.flush()
            client.close()
    print(json.dumps({"diagnostic_frames": count, "unity_verified": False, "quest_verified": False}))


if __name__ == "__main__":
    main()
