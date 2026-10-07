"""One durable terminal event per received command, including replay/rejection."""
from collections import namedtuple
import json
import os
import re
import threading
from pathlib import Path

# Where one fsynced event lives. Returned by every write so the dispatcher can
# evict an idempotency entry from memory and later read its outcome back from
# this log, the authority for the whole run.
CommandLogLocator = namedtuple("CommandLogLocator", "log event_seq offset length")


class DurableCommandLog:
    def __init__(self, path, *, session_id, apparatus_version, protocol_version, clock_id="host_monotonic"):
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("opaque session id required")
        if not all(isinstance(v, str) and v for v in (apparatus_version, protocol_version, clock_id)):
            raise ValueError("explicit version and clock labels required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Binary append keeps byte offsets exact for read-back; rows are UTF-8 + LF.
        self.file = self.path.open("xb")
        self.envelope = dict(schema_version="0.3.1", session_id=session_id, apparatus_version=apparatus_version,
                             protocol_version=protocol_version, clock_id=clock_id, event_type="private_command_result")
        self.lock, self.sequence, self.reader = threading.Lock(), 0, None

    def __call__(self, event):
        with self.lock:
            value = {**self.envelope, "event_seq": self.sequence,
                     "host_mono_ms": event["reply"]["host_mono_ms"], "sim_time": event["reply"]["sim_time"], "payload": event}
            data = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
            offset = self.file.tell()
            self.file.write(data)
            self.file.flush()
            os.fsync(self.file.fileno())
            self.sequence += 1
            return CommandLogLocator(self, value["event_seq"], offset, len(data))

    def read(self, locator):
        """Return the payload of one already-fsynced event; refuse any mismatch."""
        log, event_seq, offset, length = locator
        if log is not self or any(type(v) is not int or v < 0 for v in (event_seq, offset, length)):
            raise ValueError("COMMAND_LOG_LOCATOR_INVALID")
        with self.lock:
            if event_seq >= self.sequence:
                raise ValueError("COMMAND_LOG_LOCATOR_INVALID")
            # One lazily opened read handle: a replay is a seek, not a file open.
            if self.reader is None:
                self.reader = self.path.open("rb", buffering=0)  # Unbuffered: never a stale view.
            self.reader.seek(offset)
            raw = self.reader.read(length)
        if len(raw) != length or not raw.endswith(b"\n") or b"\n" in raw[:-1]:
            raise ValueError("COMMAND_LOG_READBACK_MISMATCH")
        def reject_constant(_):
            raise ValueError("nonfinite JSON constant")
        row = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
        if (not isinstance(row, dict) or type(row.get("event_seq")) is not int or row["event_seq"] != event_seq
                or any(row.get(key) != value for key, value in self.envelope.items())
                or not isinstance(row.get("payload"), dict)):
            raise ValueError("COMMAND_LOG_READBACK_MISMATCH")
        return row["payload"]

    def close(self):
        with self.lock:
            try:
                if self.reader is not None:
                    self.reader.close()
            finally:
                self.file.close()
