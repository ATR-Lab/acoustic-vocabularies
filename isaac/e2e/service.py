"""One simulation-thread public state/private control service for native E2E.

This reuses the real command dispatcher, reset adapter and publisher. It provides
no study authority, demo motion or clock/timing qualification.
"""
from __future__ import annotations

import gc
import hashlib
import json
import csv
import math
import os
from pathlib import Path
import re
import time
import uuid


def advance_once(adapter, dispatcher, handoff, publisher, sim_step, trace=None, timing=None):
    """Service commands at safe boundaries and retain all actual-state checks."""
    if timing is not None: timing.record('drain_begin', a=sim_step, b=0)
    handoff.drain()
    if timing is not None: timing.record('drain_end', a=sim_step, b=0)
    if dispatcher.fault:
        raise RuntimeError('PRIVATE_DISPATCHER_FAULT')
    if dispatcher.stopped:
        return sim_step, None, True
    adapter.robot.write_data_to_sim()
    if timing is not None: timing.record('physics_begin', a=sim_step)
    if trace is None:
        adapter.sim.step(render=False)
    else:
        with trace.measure():
            adapter.sim.step(render=False)
    adapter.robot.update(adapter.sim.get_physics_dt())
    if timing is not None: timing.record('physics_end', a=sim_step)
    sim_step += 1
    if timing is not None: timing.record('hold_begin', a=sim_step)
    if not dispatcher.after_physics_step():
        raise RuntimeError('NEUTRAL_HOLD_FAULT')
    if timing is not None: timing.record('hold_end', a=sim_step)
    if timing is not None: timing.record('drain_begin', a=sim_step, b=1)
    handoff.drain()
    if timing is not None: timing.record('drain_end', a=sim_step, b=1)
    if dispatcher.fault:
        raise RuntimeError('PRIVATE_DISPATCHER_FAULT')
    if dispatcher.stopped:
        return sim_step, None, True
    frame = publisher.after_step(adapter.sim_time, sim_step)
    if publisher.fault:
        raise RuntimeError('PUBLIC_PUBLISHER_FAULT')
    handoff.refresh_health()
    return sim_step, frame, False


def freeze_startup_heap():
    """Keep the loaded simulator heap out of later full collections (#148).

    After scene load the process holds a large long-lived Python object graph.
    Each generation-2 collection rescanned it and stopped every Python thread
    for roughly 370-470 ms (traced), including the private health-probe loop,
    so an in-flight probe got no reply within its 200 ms deadline. One full
    collection runs here, before READY and before any client is relayed; its
    survivors then move to the permanent generation. Collection stays enabled
    with unchanged thresholds and new objects are still collected.
    """
    before = (gc.isenabled(), gc.get_threshold())
    started = time.monotonic_ns()
    collected = gc.collect()
    gc.freeze()
    if (gc.isenabled(), gc.get_threshold()) != before:
        raise RuntimeError('GC_SETTINGS_CHANGED')
    return dict(collected=collected, frozen_objects=gc.get_freeze_count(),
                duration_ms=(time.monotonic_ns()-started)/1e6, enabled=before[0], thresholds=list(before[1]))


def run_joined_service(reset_manager, layout, output, *, seconds, station_id,
                       host_uid, public_socket, private_socket, control_session_id=None, joint_csv=None,
                       private_timing_seconds=0, view_observation=None):
    """Bounded service; fresh output and explicitly owned private Unix paths.

Both sockets are permission0600, owned by the explicitly supplied host relay UID.
The container remains --network none. Host relays/SSH forwarding are separate,
loopback-only operator actions; this function never creates a host TCP listener.
"""
    from isaac.commands.dispatcher import CommandDispatcher
    from isaac.commands.event_log import DurableCommandLog
    from isaac.commands.hold import make_robot_hold
    from isaac.commands.queue import CommandQueue
    from isaac.commands.transport import PrivateCommandTransport
    from isaac.publisher.benchmark import registry_from_snapshot
    from isaac.publisher.runtime import StatePublisher
    from isaac.publisher.transport import WebSocketTransport
    from isaac.reset.benchmark import durable
    from isaac.soak.host import StepTrace

    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 5 <= seconds <= 3600:
        raise ValueError('Explicit bounded service duration5..3600 seconds required')
    if type(private_timing_seconds) not in (int, float) or not math.isfinite(private_timing_seconds) or not (
            private_timing_seconds == 0 or 1 <= private_timing_seconds <= min(900, seconds)):
        raise ValueError('Private timing is disabled0 or explicitly bounded1..900s within the service lease')
    if type(host_uid) is not int or host_uid < 0:
        raise ValueError('Explicit host relay UID required')
    if view_observation is not None:
        from isaac.view_capture.observations import validate_options
        validate_options(view_observation,seconds)
    if not isinstance(control_session_id,str) or not re.fullmatch(r'[0-9a-f]{32}',control_session_id):
        raise ValueError('Explicit pinned control session32hex required')
    public_socket, private_socket = Path(public_socket), Path(private_socket)
    if not public_socket.is_absolute() or not private_socket.is_absolute() or public_socket == private_socket:
        raise ValueError('Separate absolute Unix socket paths required')
    for path in (public_socket, private_socket):
        if path.exists() or any(parent.is_symlink() for parent in (path,*path.parents)):
            raise FileExistsError('Inspect existing endpoint before starting')
        directory = path.parent
        if not directory.is_dir() or directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError('Socket parent must be an existing private directory')
    adapter = reset_manager.adapter
    if not math.isclose(adapter.sim.get_physics_dt(), 1/60, abs_tol=1e-8, rel_tol=0):
        raise ValueError('Actual60Hz physics configuration required')
    neutral = reset_manager.neutral_state
    registry = registry_from_snapshot(layout, {'state':neutral,'scene_sha256':adapter.scene_sha256},
        reset_manager.reset_snapshot_sha256, station_id,
        joint_csv or Path(__file__).resolve().parents[2]/'docs/spikes/isaac/joint_inventory.csv')
    output=Path(output); output.mkdir(parents=True, exist_ok=False)
    public=publisher=command_log=dispatcher=handoff=private=trace=timing=observation=heap=None
    started=ended=time.monotonic_ns(); steps=0; first=last=None
    failure=None; end_reason='duration'; ready=False; cleanup_errors=[]
    def save(name, value):
        durable(output/name,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())
    try:
        if private_timing_seconds:
            from isaac.e2e.private_timing import PrivateTiming
            timing=PrivateTiming(output/'private-timing.json',private_timing_seconds)
        result=reset_manager.reset()
        save('initial-reset.json',result)
        if result.get('reset_ok') is not True: raise RuntimeError('INITIAL_RESET_FAILED')
        public=WebSocketTransport(socket_path=public_socket)
        os.chown(public_socket,host_uid,-1); os.chmod(public_socket,0o600)
        def sample():
            state=adapter.read_state()
            if tuple(state['robot']['joint_names']) != registry.joint_names:
                raise RuntimeError('CANONICAL_ORDER_CHANGED')
            return state['robot']['joint_positions_rad'],state['objects'],state
        publisher=StatePublisher(registry,sample,public,output/'publish.csv',rate_hz=30,
            neutral_check=reset_manager.verify_state,timing=timing)
        public.health_provider=publisher.health
        command_log=DurableCommandLog(output/'commands.jsonl',session_id=uuid.uuid4().hex,
            apparatus_version='joined-e2e-development',protocol_version='SIMULATION_TEST')
        command_sink=command_log
        if view_observation is not None:
            from isaac.view_capture.observations import ObservationJournal
            observation=ObservationJournal(output/'view-observation',registry=registry,
                public_session_id=publisher.encoder.session_id,control_session_id=control_session_id,
                allowed_client=f'uid:{host_uid}',commands_path=output/'commands.jsonl',**view_observation)
            publisher.observation=observation
            command_sink=observation.command_sink(command_log)
        dispatcher=CommandDispatcher(reset_manager,command_sink,station_id=station_id,
            allowed_client=f'uid:{host_uid}',hold_robot=make_robot_hold(adapter),publisher=publisher)
        dispatcher.control_session_id=control_session_id
        handoff=CommandQueue(dispatcher,timing=timing)
        private=PrivateCommandTransport(handoff,socket_path=private_socket,allowed_uid=host_uid,timing=timing)
        os.chown(private_socket,host_uid,-1)
        trace=StepTrace(output/'physics-steps.jsonl')
        heap=freeze_startup_heap()
        started=time.monotonic_ns(); publisher.epoch_ns=started
        while time.monotonic_ns()-started < seconds*1e9:
            steps,frame,stopped=advance_once(adapter,dispatcher,handoff,publisher,steps,trace,timing)
            if frame is not None:
                if first is None: first=frame; save('sample-first.json',frame)
                last=frame
                if not ready:
                    health=handoff.health()
                    if health['exposure_ready'] is not True: raise RuntimeError('INITIAL_NEUTRAL_NOT_READY')
                    save('ready.json',dict(scope='SIMULATION_TEST',participant=False,qualification=False,
                        station_id=station_id,control_session_id=dispatcher.control_session_id,
                        public_session_id=publisher.encoder.session_id,scene_sha256=registry.scene_sha256,
                        reset_snapshot_sha256=registry.reset_snapshot_sha256,requested_seconds=seconds,
                        public_socket=str(public_socket),private_socket=str(private_socket),
                        source_pid=os.getpid(),source_ready_host_ns=str(time.monotonic_ns()),health=health))
                    ready=True
                    if timing is not None: timing.start()
                    if observation is not None: observation.start()
            if stopped: end_reason='private_stop'; break
            deadline=started+steps*1_000_000_000//60
            remaining=(deadline-time.monotonic_ns())/1e9
            if remaining > 0: time.sleep(remaining)
        ended=time.monotonic_ns()
    except Exception as error:
        ended=time.monotonic_ns(); failure=type(error).__name__+': '+str(error)
        end_reason='fault'
        if observation is not None: observation.fail('OBS_SOURCE_FAILED')
    finally:
        # Each resource is finalized independently, even after partial startup.
        for name,resource in [('private',private),('handoff',handoff),
                              ('publisher',publisher),('public',public if publisher is None else None),
                              ('trace',trace),('command_log',command_log),('private_timing',timing),
                              ('view_observation',observation)]:
            if resource is not None:
                try: resource.close()
                except Exception as error:
                    cleanup_errors.append(name+': '+type(error).__name__)
                    if observation is not None: observation.fail('OBS_SOURCE_CLEANUP_FAILED')
    if last is not None: save('sample-last.json',last)
    publish_rows=list(csv.DictReader((output/'publish.csv').open(newline=''))) if (output/'publish.csv').exists() else []
    command_rows=[json.loads(line) for line in (output/'commands.jsonl').read_text().splitlines()] if (output/'commands.jsonl').exists() else []
    accepted=[row['payload'] for row in command_rows if row['payload']['reply']['accepted']]
    report=dict(scope='SIMULATION_TEST',participant=False,qualification=False,source_kind='live',
        station_id=station_id,scene_sha256=registry.scene_sha256,reset_snapshot_sha256=registry.reset_snapshot_sha256,
        control_session_id=dispatcher.control_session_id if dispatcher else None,
        requested_seconds=seconds,elapsed_seconds=(ended-started)/1e9,physics_steps=steps,
        frames=publisher.published if publisher else 0,missed_deadlines=publisher.missed if publisher else 0,
        command_events=command_log.sequence if command_log else 0,ready_emitted=ready,end_reason=end_reason,
        command_journal_session_id=command_log.envelope['session_id'] if command_log else None,
        public_frames_with_connected_client=sum(int(row['connected_clients'])>0 for row in publish_rows),
        max_public_clients_observed=max((int(row['connected_clients']) for row in publish_rows),default=0),
        accepted_private_reset_events=sum(row['command']=='reset' and row['reply']['reset_ok'] is True for row in accepted),
        accepted_private_mode_events=sum(row['command']=='set_mode' for row in accepted),
        fault=failure,cleanup_errors=cleanup_errors,startup_heap=heap,
        service_completed=ready and failure is None and not cleanup_errors,native_visit_completed=False,
        limitations=['Actual simulator/software E2E only; no participant authority',
            'No clock, acoustic, headset or throughput qualification',
            'No demo motion implementation enabled; teaching retains neutral robot',
            'Client/command counts may include explicit diagnostics; they do not prove a native visit',
            'Full dispatcher hold/readback and protected publisher guards retained'])
    if observation is not None: report['view_observation']=observation.result
    report['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    save('summary.json',report)
    return report
