"""Real-time, real-articulation publisher evidence inside the isolated simulator.

This module does not launch Isaac, change host networking, or install packages.
The scene runner supplies its initialized reset adapter and verified snapshot.
The pacing check is intentionally separate from unpaced simulator throughput.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from isaac.reset.benchmark import durable
from isaac.reset.snapshot import load_snapshot
from .analyze import analyze
from .protocol import PublicRegistry, strict_loads, validate_frame
from .runtime import StatePublisher
from .transport import WebSocketTransport


def registry_from_snapshot(layout, snapshot, snapshot_hash, station_id, joint_csv):
    with Path(joint_csv).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if [int(row["index"]) for row in rows] != list(range(43)):
        raise ValueError("The measured joint map must contain canonical indices 0..42")
    names = tuple(row["name"] for row in rows)
    if tuple(snapshot["state"]["robot"]["joint_names"]) != names:
        raise ValueError("Loaded neutral joint order differs from measured joint map")
    states = tuple(sorted((obj["id"], tuple(sorted(obj["state"]))) for obj in layout["objects"]))
    if {key for key, _ in states} != set(snapshot["state"]["objects"]):
        raise ValueError("Layout and captured neutral inventory differ")
    return PublicRegistry(station_id, snapshot["scene_sha256"], snapshot_hash, names,
                          states, tuple(layout["anchor_ids"]))


class LocalCollector:
    """Read-only, bounded local socket client; never accesses simulator objects."""
    def __init__(self, socket_path, registry):
        self.socket_path, self.registry = socket_path, registry
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.count = self.sequence_gaps = 0
        self.first = self.last = None
        self.thread = threading.Thread(target=self._run, name="publisher-local-collector", daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.error:
            raise RuntimeError("Local collector did not connect") from self.error

    def _run(self):
        async def collect():
            from websockets.client import unix_connect
            async with unix_connect(str(self.socket_path), uri="ws://localhost/state",
                                    max_size=1_048_576, compression=None) as client:
                self.ready.set()
                while not self.stop.is_set():
                    try:
                        raw = await asyncio.wait_for(client.recv(), .1)
                    except asyncio.TimeoutError:
                        continue
                    frame = validate_frame(strict_loads(raw), self.registry)
                    if self.last is not None and frame["seq"] != self.last["seq"] + 1:
                        self.sequence_gaps += 1
                    if self.first is None:
                        self.first = frame
                    self.last = frame
                    self.count += 1
        try:
            asyncio.run(collect())
        except Exception as error:
            self.error = error
            self.ready.set()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Local collector did not stop")


def run_publisher_check(adapter, layout, snapshot_path, output, *, expected_snapshot_sha256,
                        seconds=3600, rate_hz=30, station_id="station-01", socket_path=None,
                        joint_csv=None, collector_mode="thread"):
    """Advance actual physics at 60 Hz and read/publish at 30 or 60 Hz.

    Explicitly an unprotected engineering rate run: gravity/actuator settling
    may occur. Protected neutral-hold verification belongs to the command lock
    integration. A short run can never produce a passing one-hour rate screen.
    """
    if collector_mode not in ("thread", "process"):
        raise ValueError("Explicit thread or process diagnostic collector required")
    from .process_collector import ProcessCollector
    collector_factory = LocalCollector if collector_mode == "thread" else ProcessCollector
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Positive finite run duration required")
    if type(rate_hz) is not int or rate_hz not in (30, 60):
        raise ValueError("30 or 60 Hz required")
    if not math.isclose(adapter.sim.get_physics_dt(), 1/60, rel_tol=0, abs_tol=1e-8):
        raise ValueError("This benchmark requires an explicit 60 Hz physics step")
    snapshot = load_snapshot(snapshot_path, expected_snapshot_sha256)
    if snapshot["scene_sha256"] != adapter.scene_sha256:
        raise ValueError("Loaded scene differs from captured neutral snapshot")
    joint_csv = joint_csv or Path(__file__).resolve().parents[2]/"docs/spikes/isaac/joint_inventory.csv"
    registry = registry_from_snapshot(layout, snapshot, expected_snapshot_sha256, station_id, joint_csv)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    socket_path = Path(socket_path) if socket_path else output/"state.sock"
    transport = WebSocketTransport(socket_path=socket_path)
    publisher = collector = None
    start = end = time.monotonic_ns()
    steps = 0
    failure = None
    processing_ms = []
    try:
        def sample():
            if tuple(adapter.robot.joint_names) != registry.joint_names:
                raise ValueError("Runtime articulation order changed")
            # This run is explicitly unprotected. Read the complete public
            # projection each tick without traversing collision geometry or
            # collecting private reset-only root velocities/body frames.
            positions = adapter.robot.root_physx_view.get_dof_positions()[0].tolist()
            objects = adapter.accessors.read_public_state()
            return positions, objects, None
        publisher = StatePublisher(registry, sample, transport, output/"publish.csv", rate_hz=rate_hz)
        transport.health_provider = publisher.health
        collector = collector_factory(socket_path, registry)
        # Setup and connection latency are excluded; startup remains unqualified.
        start = time.monotonic_ns()
        publisher.epoch_ns = start
        period_ns = 1_000_000_000/rate_hz
        tick = 0
        while time.monotonic_ns() - start < seconds*1e9:
            began = time.monotonic_ns()
            for _ in range(60//rate_hz):
                adapter.robot.write_data_to_sim()
                adapter.sim.step(render=False)
                adapter.robot.update(adapter.sim.get_physics_dt())
                steps += 1
            physics_done = time.monotonic_ns()
            deadline = start + int(tick*period_ns)
            remaining = (deadline-time.monotonic_ns())/1e9
            if remaining > 0:
                time.sleep(remaining)
            publishing_started = time.monotonic_ns()
            frame = publisher.after_step(adapter.sim_time, steps)
            processing_ms.append((physics_done-began + time.monotonic_ns()-publishing_started)/1e6)
            if frame is None or publisher.fault or collector.error:
                raise RuntimeError(publisher.fault or "PUBLISH_OR_COLLECTOR_FAILURE")
            tick = max(tick+1, publisher.deadline_index)
        end = time.monotonic_ns()
    except Exception as error:
        end = time.monotonic_ns()
        failure = type(error).__name__ + ": " + str(error)
    finally:
        if collector:
            # Let the independently scheduled local receiver drain the last frame.
            limit = time.monotonic() + 2
            while collector.count < publisher.published and not collector.error and time.monotonic() < limit:
                time.sleep(.005)
            collector.close()
        if publisher:
            publisher.close()
        else:
            transport.close()
    if publisher is None:
        raise RuntimeError(failure or "Publisher setup failed")
    if collector and collector.error and not failure:
        failure = "LOCAL_COLLECTOR_FAILURE"
    metadata = dict(rate_hz=rate_hz, collector_mode=collector_mode, requested_seconds=seconds, start_host_ns=str(start), end_host_ns=str(end),
                    completed=failure is None and (end-start)/1e9 >= seconds, source_kind="live",
                    schema_validated_frames=publisher.published, fault=publisher.fault or failure,
                    station_id=station_id, scene_sha256=registry.scene_sha256,
                    reset_snapshot_sha256=registry.reset_snapshot_sha256,
                    physics_steps=steps, physics_step_hz=60, protected_mode=False,
                    pacing="absolute_host_deadline; real physics integration; unprotected engineering run",
                    local_received=collector.count if collector else 0,
                    local_sequence_gaps=collector.sequence_gaps if collector else 0,
                    validation="strict runtime contract for every frame; JSON Schema separately checked on retained samples",
                    processing_ms_max=max(processing_ms, default=None))
    durable(output/"metadata.json", (json.dumps(metadata, indent=2, allow_nan=False)+"\n").encode())
    summary = analyze(output/"publish.csv", metadata)
    if collector:
        for name, frame in (("sample-first.json", collector.first), ("sample-last.json", collector.last)):
            if frame is not None:
                durable(output/name, (json.dumps(frame, indent=2, allow_nan=False)+"\n").encode())
    durable(output/"health.json", (json.dumps(publisher.health(), indent=2)+"\n").encode())
    summary["local_receive_complete"] = bool(collector and collector.count == publisher.published and not collector.error and collector.sequence_gaps == 0)
    summary["hashes"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    durable(output/"summary.json", (json.dumps(summary, indent=2, allow_nan=False)+"\n").encode())
    return summary
