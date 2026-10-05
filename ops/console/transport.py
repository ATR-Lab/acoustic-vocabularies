"""Single local writer protocol; no LAN listener or headset UI."""
from __future__ import annotations

import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import ConsoleFault, encoded, hash_value, instant, require, strict_json, utc_now


@contextmanager
def shared_binary_reader(path):
    """Read one file generation without preventing Windows rename publication."""
    if os.name != "nt":
        with Path(path).open("rb") as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ|WRITE|DELETE; OPEN_EXISTING. The descriptor
    # retains the opened generation even if the writer replaces its directory entry.
    handle = create(str(Path(path).absolute()), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


class Mailbox:
    def __init__(self, directory, clock=utc_now, mono=time.monotonic, timeout=2.0, receipt_sink=None):
        self.directory = Path(directory)
        require(self.directory.is_dir() and not self.directory.is_symlink(), "transport_missing")
        self.clock, self.mono, self.timeout = clock, mono, timeout
        self.nonce = None
        self.sequence = 0
        self.last_heartbeat = None
        self.last_change = mono()
        self.pending = None
        self.visit = None
        self.reload_required = False
        self.receipt_sink = receipt_sink

    def snapshot(self, for_load=False):
        try:
            p = self.directory / "state.json"
            require(not p.is_symlink(), "engine_unavailable")
            with shared_binary_reader(p) as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "engine_unavailable")
                data = stream.read(65537)
            require(len(data) <= 65536, "engine_unavailable")
            state = strict_json(data)
            keys = {"version", "session_nonce", "sequence", "utc", "receipt", "run_sheet_manifest_sha256",
                    "schedule_sha256", "package_sha256", "engine_state", "completed_counts", "admission", "health"}
            require(set(state) == keys and type(state["version"]) is int and state["version"] == 1, "engine_invalid")
            nonce = state["session_nonce"]
            require(isinstance(nonce, str) and len(nonce) == 32 and all(c in "0123456789abcdef" for c in nonce), "engine_invalid")
            require(type(state["sequence"]) is int and state["sequence"] > 0, "engine_invalid")
            for key in ("run_sheet_manifest_sha256", "schedule_sha256", "package_sha256"):
                hash_value(state[key])
            age = (self.clock()-instant(state["utc"])).total_seconds()
            require(-0.5 <= age <= 2, "engine_stale")
            if self.nonce != nonce:
                self.nonce, self.sequence, self.pending = nonce, 0, None
                self.last_heartbeat, self.last_change = None, self.mono()
                if self.visit is not None:
                    self.visit = None
                    self.reload_required = True
            require(for_load or not self.reload_required, "engine_restarted_reload")
            require(self.last_heartbeat is None or state["sequence"] >= self.last_heartbeat, "engine_replayed")
            if self.last_heartbeat != state["sequence"]:
                self.last_heartbeat, self.last_change = state["sequence"], self.mono()
            require(self.mono()-self.last_change <= 2, "engine_stale")
            receipt = state["receipt"]
            if receipt is not None:
                require(set(receipt) == {"request_id", "sequence", "status", "code"} and
                        type(receipt["sequence"]) is int and receipt["sequence"] >= 1 and
                        receipt["status"] in ("accepted", "rejected"), "receipt_invalid")
                if self.pending and receipt["request_id"] == self.pending["request_id"]:
                    require(receipt["sequence"] == self.pending["sequence"], "receipt_invalid")
                    require(self.receipt_sink is not None, "receipt_sink_missing")
                    self.receipt_sink(dict(request_id=self.pending["request_id"], sequence=self.pending["sequence"],
                                           command=self.pending["command"], status=receipt["status"]))
                    self.pending = None
            return state
        except (OSError, KeyError, TypeError):
            raise ConsoleFault("engine_unavailable") from None

    def bind(self, visit):
        state = self.snapshot(for_load=True)
        require((state["run_sheet_manifest_sha256"], state["schedule_sha256"], state["package_sha256"]) ==
                (visit.manifest_hash, visit.schedule_hash, visit.package_hash), "hash_mismatch")
        self.visit = visit
        self.reload_required = False
        try:
            self.command("load")
        except Exception:
            self.visit = None
            self.reload_required = True
            raise

    def command(self, action):
        state = self.snapshot()
        require(self.visit is not None, "visit_not_loaded")
        require(self.receipt_sink is not None, "receipt_sink_missing")
        require(self.pending is None or action == "stop", "command_uncertain")
        superseded = self.pending["sequence"] if self.pending else None
        # A restarted console cannot repeat a previous sequence from this engine.
        if state["receipt"] is not None:
            self.sequence = max(self.sequence, state["receipt"]["sequence"])
        self.sequence += 1
        request = dict(version=1, session_nonce=self.nonce, request_id=uuid.uuid4().hex,
                       sequence=self.sequence, command=action,
                       run_sheet_manifest_sha256=self.visit.manifest_hash, schedule_sha256=self.visit.schedule_hash)
        data = encoded(request)
        temp = self.directory / (request["request_id"]+".tmp")
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self.pending = request
        os.replace(temp, self.directory / "command.json")
        end = self.mono()+self.timeout
        while self.mono() < end:
            receipt = self.snapshot()["receipt"]
            if receipt and receipt["request_id"] == request["request_id"]:
                require(receipt["status"] == "accepted", "engine_rejected")
                return dict(request_id=request["request_id"], sequence=self.sequence, status="accepted", superseded_sequence=superseded)
            time.sleep(0.025)
        raise ConsoleFault("command_uncertain")


class DemoEngine:
    """Explicit browser/test fixture. Cannot bind a non-DEMO visit or play audio."""
    def __init__(self, mono=time.monotonic):
        self.mono = mono
        self.visit = None
        self.state = "awaiting_operator"
        self.pause_pending = False
        self.counts = []
        self.boundary = 0
        self.fault = None

    def bind(self, visit):
        require(visit.demo, "demo_only")
        self.visit, self.state = visit, "awaiting_operator"
        self.counts = [0]*len(visit.rows)

    def command(self, action):
        if action in ("start", "resume"):
            require(self.state in ("awaiting_operator", "paused"), "engine_rejected")
            self.state, self.boundary = "running", self.mono()+3
        elif action == "pause":
            self.pause_pending = self.state == "running"
        elif action == "stop":
            self.state = "stopped"
        else:
            raise ConsoleFault("engine_rejected")
        return dict(status="accepted", demo=True)

    def snapshot(self):
        if self.state == "running" and self.mono() >= self.boundary:
            for i, row in enumerate(self.visit.rows):
                if self.counts[i] < int(row["expected_count"]):
                    self.counts[i] += 1
                    break
            if self.pause_pending:
                self.state, self.pause_pending = "paused", False
            elif self.counts == [int(row["expected_count"]) for row in self.visit.rows]:
                self.state = "complete"
            self.boundary = self.mono()+3
        h = dict(headset=True, audio=True, reset=True, input=True, bridge_age_ms=8, frame_ms=13.9, max_gap_ms=14)
        if self.fault == "bridge_stale":
            h["bridge_age_ms"] = 300
        elif self.fault == "headset_unavailable":
            h["headset"] = False
        elif self.fault == "frame_freeze":
            h["max_gap_ms"] = 300
        return dict(engine_state=self.state, completed_counts=list(self.counts),
                    run_sheet_manifest_sha256=self.visit.manifest_hash, schedule_sha256=self.visit.schedule_hash,
                    package_sha256=self.visit.package_hash, admission=dict(verified=True, old_hashes_ok=True, locks_ok=True), health=h)
