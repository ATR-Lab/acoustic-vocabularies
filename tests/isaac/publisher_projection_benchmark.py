"""Bounded ABBA encoding diagnostic, imported by an isolated scene runner.

Supply the exact baseline protocol.py exported from the recorded Git revision.
This helper changes neither physics/hold nor validation/cache/GC settings.
"""
from __future__ import annotations

import csv
import gc
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import uuid


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def baseline_module(path, expected_hash):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('Baseline source hash mismatch')
    name = 'pinned_publisher_baseline_' + expected_hash
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_projection_profile(adapter, layout, snapshot_path, snapshot_hash, output,
                           baseline_path, baseline_hash, *, seconds=12):
    from isaac.commands.hold import make_robot_hold
    from isaac.publisher import protocol
    from isaac.publisher.analyze import analyze
    from isaac.publisher.benchmark import registry_from_snapshot
    from isaac.publisher.process_collector import ProcessCollector
    from isaac.publisher.runtime import StatePublisher
    from isaac.publisher.transport import WebSocketTransport
    from isaac.reset.event_log import DurableResetLog
    from isaac.reset.manager import ResetManager
    from isaac.reset.snapshot import load_snapshot

    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 5 <= seconds <= 30:
        raise ValueError('Diagnostic phases must last 5..30 seconds')
    if not math.isclose(adapter.sim.get_physics_dt(), 1/60, abs_tol=1e-8, rel_tol=0):
        raise ValueError('Actual 60 Hz physics required')
    output = Path(output); output.mkdir(exist_ok=False)
    baseline = baseline_module(baseline_path, baseline_hash)
    snapshot = load_snapshot(snapshot_path, snapshot_hash)
    registry = registry_from_snapshot(layout, snapshot, snapshot_hash, 'projection-diagnostic',
        Path(__file__).resolve().parents[2]/'docs/spikes/isaac/joint_inventory.csv')
    event_log = DurableResetLog(output/'reset-events.jsonl', session_id=uuid.uuid4().hex,
        apparatus_version='projection-diagnostic', protocol_version='unresolved-methodology')
    manager = ResetManager(adapter, snapshot, snapshot_hash, event_log)
    hold = make_robot_hold(adapter)
    neutral = manager.neutral_state
    report = dict(qualification=False, source_kind='live', scene_sha256=adapter.scene_sha256,
        snapshot_sha256=snapshot_hash, baseline_protocol_sha256=baseline_hash,
        candidate_protocol_sha256=hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest(),
        requested_seconds_per_phase=seconds, gc_enabled=gc.isenabled(), gc_thresholds=gc.get_threshold(),
        handle_cache_enabled=bool(getattr(adapter.accessors, '_cache_guard', None)),
        phases=[], limitations=['Short instrumented ABBA diagnostic, not hour acceptance',
            'Same-process phases share loaded GPU/runtime; no host isolation/tuning changes',
            'Inclusive timers overlap: do not sum physics, sample and nested calls'])
    try:
        if manager.reset().get('reset_ok') is not True:
            raise RuntimeError('Initial reset failed')
        state = adapter.read_state()
        encoders = [kind(registry, 'live', lambda: 123456789) for kind in (baseline.StateEncoder, protocol.StateEncoder)]
        for encoder in encoders: encoder.session_id = '1'*32
        frames = [encoder.build(state['robot']['joint_positions_rad'], state['objects'], adapter.sim_time, 0)
                  for encoder in encoders]
        payloads = [protocol.encode(frame) for frame in frames]
        if payloads[0] != payloads[1]:
            raise RuntimeError('Actual readback exact byte equivalence failed')
        save(output/'actual-equivalence-frame.json', frames[0])
        report['actual_readback_exact_bytes_equal'] = True
        report['actual_equivalent_payload_sha256'] = hashlib.sha256(payloads[0].encode()).hexdigest()
        # Repeated encoder-only work is deliberately outside the timed physics phases.
        micro = []
        for label, kind in [('baseline', baseline.StateEncoder), ('candidate', protocol.StateEncoder)]*2:
            encoder = kind(registry, 'live', lambda: 123456789)
            rows = []
            for index in range(400):
                started = time.perf_counter_ns()
                encoder.build(state['robot']['joint_positions_rad'], state['objects'], 0., index)
                rows.append((time.perf_counter_ns()-started)/1e6)
            micro.append(dict(implementation=label, builds=len(rows), median_ms=statistics.median(rows),
                              p95_ms=sorted(rows)[int(.95*(len(rows)-1))], max_ms=max(rows)))
        report['actual_readback_encoder_microbench'] = micro
        for protected in (False, True):
            for index, (label, kind) in enumerate([('baseline', baseline.StateEncoder), ('candidate', protocol.StateEncoder),
                                                  ('candidate', protocol.StateEncoder), ('baseline', baseline.StateEncoder)]):
                directory = output/f'{"protected" if protected else "unprotected"}-{index}-{label}'
                directory.mkdir()
                if manager.reset().get('reset_ok') is not True:
                    raise RuntimeError('Phase reset failed')
                times, gc_events, gc_start = {}, [], {}
                def timed(name, callback, *args, **kwargs):
                    begin = time.perf_counter_ns()
                    try: return callback(*args, **kwargs)
                    finally: times.setdefault(name, []).append((time.perf_counter_ns()-begin)/1e6)
                def sample():
                    if protected:
                        current = timed('full_read', adapter.read_state)
                        return current['robot']['joint_positions_rad'], current['objects'], current
                    q = adapter.robot.root_physx_view.get_dof_positions()[0].tolist()
                    return q, timed('public_read', adapter.accessors.read_public_state), None
                def verify(current): return timed('neutral_verify', manager.verify_state, current)
                def gc_event(phase, info):
                    generation = info['generation']; now = time.monotonic_ns()
                    if phase == 'start': gc_start[generation] = now
                    elif generation in gc_start:
                        gc_events.append(dict(generation=generation, elapsed_ms=(now-gc_start.pop(generation))/1e6,
                            collected=info['collected'], uncollectable=info['uncollectable']))
                transport = publisher = receiver = None
                started = ended = time.monotonic_ns(); steps = ticks = 0; failure = None
                try:
                    transport = WebSocketTransport(socket_path='/tmp/av-projection-'+uuid.uuid4().hex[:12]+'.sock')
                    publisher = StatePublisher(registry, sample, transport, directory/'publish.csv',
                        neutral_check=verify if protected else None)
                    publisher.encoder = kind(registry, 'live')
                    original_build = publisher.encoder.build
                    publisher.encoder.build = lambda *args, **kwargs: timed('encoder_build', original_build, *args, **kwargs)
                    publisher.require_neutral(protected)
                    transport.health_provider = publisher.health
                    os.chmod(transport.path, 0o600)
                    receiver = ProcessCollector(transport.path, registry)
                    gc.callbacks.append(gc_event)
                    started = time.monotonic_ns(); publisher.epoch_ns = started
                    while time.monotonic_ns()-started < seconds*1e9:
                        for _ in range(2):
                            timed('target_flush', adapter.robot.write_data_to_sim)
                            timed('physics', adapter.sim.step, render=False)
                            adapter.robot.update(adapter.sim.get_physics_dt())
                            if protected: timed('robot_hold', hold, neutral['robot'])
                            steps += 1
                        remaining = (started+ticks*1_000_000_000//30-time.monotonic_ns())/1e9
                        if remaining > 0: time.sleep(remaining)
                        frame = publisher.after_step(adapter.sim_time, steps)
                        if frame is None: raise RuntimeError(publisher.fault or 'PUBLISH_FAILED')
                        if receiver.error: raise RuntimeError('RECEIVER_FAILED')
                        ticks = max(ticks+1, publisher.deadline_index)
                    ended = time.monotonic_ns()
                    drain_until = time.monotonic()+2
                    while receiver.count < publisher.published and receiver.error is None and time.monotonic() < drain_until:
                        time.sleep(.01)
                except Exception as error:
                    ended = time.monotonic_ns(); failure = repr(error)
                finally:
                    if gc_event in gc.callbacks: gc.callbacks.remove(gc_event)
                    if receiver: receiver.close()
                    if publisher: publisher.close()
                    elif transport: transport.close()
                if receiver:
                    if receiver.error: failure = failure or repr(receiver.error)
                    if receiver.count != publisher.published or receiver.sequence_gaps:
                        failure = failure or 'RECEIVER_COUNT_OR_SEQUENCE_MISMATCH'
                    for name in ('first', 'last'):
                        if getattr(receiver, name) is not None: save(directory/f'sample-{name}.json', getattr(receiver, name))
                metadata = dict(rate_hz=30, start_host_ns=str(started), end_host_ns=str(ended),
                    completed=failure is None, source_kind='live', schema_validated_frames=receiver.count if receiver else 0,
                    fault=failure)
                save(directory/'metadata.json', metadata)
                result = dict(implementation=label, protected=protected, order=index, steps=steps,
                    elapsed_seconds=(ended-started)/1e9, steps_per_second=steps/((ended-started)/1e9),
                    fault=failure, gc_events=gc_events,
                    receiver_count=receiver.count if receiver else 0, receiver_gaps=receiver.sequence_gaps if receiver else None,
                    timers={key:dict(calls=len(values),median_ms=statistics.median(values),
                        total_ms=sum(values),p95_ms=sorted(values)[int(.95*(len(values)-1))],max_ms=max(values))
                        for key, values in times.items()},
                    timing=analyze(directory/'publish.csv', metadata, required_seconds=seconds))
                with (directory/'timers.csv').open('x', newline='') as stream:
                    writer=csv.writer(stream); writer.writerow(['metric','index','elapsed_ms'])
                    for key, values in times.items():
                        for ordinal, value in enumerate(values): writer.writerow([key,ordinal,value])
                    stream.flush(); os.fsync(stream.fileno())
                save(directory/'summary.json', result); report['phases'].append(result)
                if failure: raise RuntimeError(failure)
        report['final_reset'] = manager.reset()
        if report['final_reset'].get('reset_ok') is not True: raise RuntimeError('Final reset failed')
    finally:
        event_log.close()
        save(output/'summary.json', report)
    return report
