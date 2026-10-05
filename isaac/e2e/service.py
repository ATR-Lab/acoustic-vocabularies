"""One simulation-thread public state/private control service for native E2E.

This reuses the real command dispatcher, reset adapter and publisher. It provides
no study authority, demo motion or clock/timing qualification.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid


def advance_once(adapter, dispatcher, handoff, publisher, sim_step, trace=None):
    """Service commands at safe boundaries and retain all actual-state checks."""
    handoff.drain()
    if dispatcher.fault:
        raise RuntimeError('PRIVATE_DISPATCHER_FAULT')
    if dispatcher.stopped:
        return sim_step, None, True
    adapter.robot.write_data_to_sim()
    if trace is None:
        adapter.sim.step(render=False)
    else:
        with trace.measure():
            adapter.sim.step(render=False)
    adapter.robot.update(adapter.sim.get_physics_dt())
    sim_step += 1
    if not dispatcher.after_physics_step():
        raise RuntimeError('NEUTRAL_HOLD_FAULT')
    handoff.drain()
    if dispatcher.fault:
        raise RuntimeError('PRIVATE_DISPATCHER_FAULT')
    if dispatcher.stopped:
        return sim_step, None, True
    frame = publisher.after_step(adapter.sim_time, sim_step)
    if publisher.fault:
        raise RuntimeError('PUBLIC_PUBLISHER_FAULT')
    handoff.refresh_health()
    return sim_step, frame, False


def run_joined_service(reset_manager, layout, output, *, seconds, station_id,
                       host_uid, public_socket, private_socket, control_session_id=None, joint_csv=None):
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
    if type(host_uid) is not int or host_uid < 0:
        raise ValueError('Explicit host relay UID required')
    if not isinstance(control_session_id,str) or not re.fullmatch(r'[0-9a-f]{32}',control_session_id):
        raise ValueError('Explicit pinned control session32hex required')
    public_socket, private_socket = Path(public_socket), Path(private_socket)
    if not public_socket.is_absolute() or not private_socket.is_absolute() or public_socket == private_socket:
        raise ValueError('Separate absolute Unix socket paths required')
    for path in (public_socket, private_socket):
        if path.exists() or path.is_symlink():
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
    public=publisher=command_log=dispatcher=handoff=private=trace=None
    started=ended=time.monotonic_ns(); steps=0; first=last=None
    failure=None; end_reason='duration'; ready=False; cleanup_errors=[]
    def save(name, value):
        durable(output/name,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())
    try:
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
            neutral_check=reset_manager.verify_state)
        public.health_provider=publisher.health
        command_log=DurableCommandLog(output/'commands.jsonl',session_id=uuid.uuid4().hex,
            apparatus_version='joined-e2e-development',protocol_version='SIMULATION_TEST')
        dispatcher=CommandDispatcher(reset_manager,command_log,station_id=station_id,
            allowed_client=f'uid:{host_uid}',hold_robot=make_robot_hold(adapter),publisher=publisher)
        dispatcher.control_session_id=control_session_id
        handoff=CommandQueue(dispatcher)
        private=PrivateCommandTransport(handoff,socket_path=private_socket,allowed_uid=host_uid)
        os.chown(private_socket,host_uid,-1)
        trace=StepTrace(output/'physics-steps.jsonl')
        started=time.monotonic_ns(); publisher.epoch_ns=started
        while time.monotonic_ns()-started < seconds*1e9:
            steps,frame,stopped=advance_once(adapter,dispatcher,handoff,publisher,steps,trace)
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
            if stopped: end_reason='private_stop'; break
            deadline=started+steps*1_000_000_000//60
            remaining=(deadline-time.monotonic_ns())/1e9
            if remaining > 0: time.sleep(remaining)
        ended=time.monotonic_ns()
    except Exception as error:
        ended=time.monotonic_ns(); failure=type(error).__name__+': '+str(error)
        end_reason='fault'
    finally:
        # Each resource is finalized independently, even after partial startup.
        for name,resource in [('private',private),('handoff',handoff),
                              ('publisher',publisher),('public',public if publisher is None else None),
                              ('trace',trace),('command_log',command_log)]:
            if resource is not None:
                try: resource.close()
                except Exception as error: cleanup_errors.append(name+': '+type(error).__name__)
    if last is not None: save('sample-last.json',last)
    report=dict(scope='SIMULATION_TEST',participant=False,qualification=False,source_kind='live',
        station_id=station_id,scene_sha256=registry.scene_sha256,reset_snapshot_sha256=registry.reset_snapshot_sha256,
        control_session_id=dispatcher.control_session_id if dispatcher else None,
        requested_seconds=seconds,elapsed_seconds=(ended-started)/1e9,physics_steps=steps,
        frames=publisher.published if publisher else 0,missed_deadlines=publisher.missed if publisher else 0,
        command_events=command_log.sequence if command_log else 0,ready_emitted=ready,end_reason=end_reason,
        fault=failure,cleanup_errors=cleanup_errors,
        completed=ready and failure is None and not cleanup_errors,
        limitations=['Actual simulator/software E2E only; no participant authority',
            'No clock, acoustic, headset or throughput qualification',
            'No demo motion implementation enabled; teaching retains neutral robot',
            'Full dispatcher hold/readback and protected publisher guards retained'])
    report['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    save('summary.json',report)
    return report
