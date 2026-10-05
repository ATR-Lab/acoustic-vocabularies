"""One durable terminal event per received command, including replay/rejection."""
import json
import os
import re
import threading
from pathlib import Path


class DurableCommandLog:
    def __init__(self, path, *, session_id, apparatus_version, protocol_version, clock_id="host_monotonic"):
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("opaque session id required")
        if not all(isinstance(v, str) and v for v in (apparatus_version, protocol_version, clock_id)):
            raise ValueError("explicit version and clock labels required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("x", encoding="utf-8", newline="\n")
        self.envelope = dict(schema_version="0.3.1", session_id=session_id, apparatus_version=apparatus_version,
                             protocol_version=protocol_version, clock_id=clock_id, event_type="private_command_result")
        self.lock, self.sequence = threading.Lock(), 0

    def __call__(self, event):
        with self.lock:
            value = {**self.envelope, "event_seq": self.sequence,
                     "host_mono_ms": event["reply"]["host_mono_ms"], "sim_time": event["reply"]["sim_time"], "payload": event}
            self.file.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
            self.file.flush()
            os.fsync(self.file.fileno())
            self.sequence += 1

    def close(self):
        with self.lock:
            self.file.close()
