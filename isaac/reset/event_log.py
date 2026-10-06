"""Append-only, fsync-before-return reset events using the ADR-007 envelope.

The 0.2.0 reset payload is an engineering extension, not a substitute for the
unavailable private logging templates. It is independent of public state frames.
"""
import json
import os
import re
from pathlib import Path


class DurableResetLog:
    def __init__(self, path, *, session_id, apparatus_version, protocol_version, clock_id="host_monotonic"):
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("opaque session id required")
        if not all(isinstance(v, str) and v for v in (apparatus_version, protocol_version, clock_id)):
            raise ValueError("explicit envelope versions/clock identity required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Fresh file per process. A prior/interrupted log is never overwritten.
        self.file = self.path.open("x", encoding="utf-8", newline="\n")
        self.envelope = dict(schema_version="0.2.0", session_id=session_id,
                             apparatus_version=apparatus_version, protocol_version=protocol_version,
                             clock_id=clock_id)
        self.event_seq = 0

    def __call__(self, result):
        event = {**self.envelope, "event_seq": self.event_seq,
                 "event_type": "neutral_reset" if result["reset_ok"] else "reset_fault",
                 "host_mono_ms": result["host_mono_ms"], "sim_time": result["sim_time"], "payload": result}
        self.file.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())
        self.event_seq += 1

    def close(self):
        self.file.close()
