"""Strict diagnostic receiver in ordinary Python, outside Kit's process/GIL."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


class ProcessCollector:
    """LocalCollector-compatible evidence client; never imports Isaac or reads USD."""
    mode = "separate_process"

    def __init__(self, socket_path, registry):
        self.count = self.sequence_gaps = 0
        self.first = self.last = None
        self.error = None
        self.ready = threading.Event()
        self.closed = False
        self._directory = tempfile.TemporaryDirectory(prefix="av-state-receiver-")
        self.directory = Path(self._directory.name)
        (self.directory/"config.json").write_text(json.dumps({"socket": str(socket_path), "registry": asdict(registry)}))
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(str(x) for x in sys.path if x)
        self._stderr = (self.directory/"stderr.txt").open("w")
        self.process = subprocess.Popen([sys.executable, "-m", "isaac.publisher.process_collector", str(self.directory)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self._stderr, text=True, bufsize=1, env=env)
        self.thread = threading.Thread(target=self._monitor, name="receiver-small-progress", daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.error:
            self.close()
            raise RuntimeError("Separate strict receiver did not connect") from self.error

    def _monitor(self):
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                if event["event"] == "ready":
                    self.ready.set()
                elif event["event"] == "progress":
                    self.count, self.sequence_gaps = event["count"], event["sequence_gaps"]
                elif event["event"] == "error":
                    self.error = RuntimeError(event["detail"])
                    self.ready.set()
                else:
                    raise ValueError("Unexpected receiver progress event")
            status = self.process.wait()
            if status != 0 and self.error is None:
                self.error = RuntimeError("Separate strict receiver exited " + str(status))
            if not self.closed and not (self.directory/"stop").exists() and self.error is None:
                self.error = RuntimeError("Separate strict receiver stopped unexpectedly")
        except Exception as error:
            self.error = error
        finally:
            self.ready.set()

    def close(self):
        if self.closed:
            return
        (self.directory/"stop").write_text("stop")
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.error = self.error or RuntimeError("Separate strict receiver did not stop")
        self.thread.join(timeout=5)
        result_path = self.directory/"result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            self.count, self.sequence_gaps = result["count"], result["sequence_gaps"]
            if result["error"]:
                self.error = self.error or RuntimeError(result["error"])
            for name in ("first", "last"):
                path = self.directory/(name+".json")
                if path.exists():
                    setattr(self, name, json.loads(path.read_text()))
        elif self.error is None:
            self.error = RuntimeError("Separate receiver evidence missing")
        self.closed = True
        self.process.stdout.close()
        self._stderr.close()
        self._directory.cleanup()


def run_child(directory):
    from .protocol import PublicRegistry, strict_loads, validate_frame
    from websockets.client import unix_connect
    directory = Path(directory)
    config = json.loads((directory/"config.json").read_text())
    value = config["registry"]
    value["joint_names"] = tuple(value["joint_names"])
    value["object_states"] = tuple((key, tuple(fields)) for key, fields in value["object_states"])
    value["anchor_ids"] = tuple(value["anchor_ids"])
    registry = PublicRegistry(**value)
    result = dict(count=0, sequence_gaps=0, error=None)
    first = last = None
    def emit(value):
        print(json.dumps(value, separators=(",", ":")), flush=True)
    async def receive():
        nonlocal first, last
        async with unix_connect(config["socket"], uri="ws://localhost/state", max_size=1048576, compression=None) as client:
            emit({"event": "ready"})
            while not (directory/"stop").exists():
                try:
                    raw = await asyncio.wait_for(client.recv(), .1)
                except asyncio.TimeoutError:
                    continue
                frame = validate_frame(strict_loads(raw), registry)
                if last is not None and frame["seq"] != last["seq"]+1:
                    result["sequence_gaps"] += 1
                if first is None:
                    first = frame
                last = frame
                result["count"] += 1
                emit(dict(event="progress", count=result["count"], sequence_gaps=result["sequence_gaps"]))
    try:
        asyncio.run(receive())
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        emit(dict(event="error", detail=result["error"]))
    finally:
        for name, value in (("first", first), ("last", last), ("result", result)):
            if value is not None:
                with (directory/(name+".json")).open("x") as stream:
                    json.dump(value, stream, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
    return 1 if result["error"] else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Expected a private receiver configuration directory")
    raise SystemExit(run_child(sys.argv[1]))
