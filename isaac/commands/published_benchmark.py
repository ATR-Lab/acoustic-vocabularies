"""Bounded real-publication verification after protected command rejection."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from .dispatcher import CommandDispatcher
from .event_log import DurableCommandLog
from .hold import make_robot_hold
from .protocol import LEGAL_PAIRS
from ..publisher.benchmark import LocalCollector, registry_from_snapshot
from ..publisher.protocol import validate_frame
from ..publisher.runtime import StatePublisher
from ..publisher.transport import WebSocketTransport
from ..reset.benchmark import durable
from ..reset.manager import angle
from ..reset.snapshot import sha256


def public_neutral(frame, registry, neutral, tolerances):
    """Independently compare every published render field with pinned neutral."""
    validate_frame(frame, registry)
    errors=dict(joint_rad=max(abs(a-b) for a,b in zip(frame['joint_positions'],neutral['robot']['joint_positions_rad'])),
                position_m=0.,orientation_rad=0.)
    semantic=True
    for actual in frame['objects']:
        wanted=neutral['objects'][actual['id']]
        errors['position_m']=max(errors['position_m'],math.dist(actual['position_m'],wanted['position_m']))
        errors['orientation_rad']=max(errors['orientation_rad'],angle(actual['rotation_xyzw'],wanted['rotation_xyzw']))
        semantic &= all(type(actual[k]) is type(wanted[k]) and actual[k]==wanted[k] for k in ('visible','enabled','state'))
    return dict(neutral=semantic and all(value<=getattr(tolerances,key) for key,value in errors.items()),
                exact_visual_state=semantic,max_deviation=errors)


def run_published_command_check(reset_manager, layout, output, *, socket_path, station_id='engineering-fixture', joint_csv=None):
    """No demo execution hooks: all target-bearing requests must be rejected.

    Receives each actual encoded payload over the real Unix WebSocket transport.
    Source/readback/neutral checks stay on the simulation owner thread.
    """
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    adapter=reset_manager.adapter;neutral=reset_manager.neutral_state
    joint_csv=joint_csv or Path(__file__).resolve().parents[2]/'docs/spikes/isaac/joint_inventory.csv'
    registry=registry_from_snapshot(layout,{'state':neutral,'scene_sha256':adapter.scene_sha256},
                                    reset_manager.reset_snapshot_sha256,station_id,joint_csv)
    events=DurableCommandLog(output/'command-events.jsonl',session_id=uuid.uuid4().hex,
                            apparatus_version='workcell-development-v1',protocol_version='unresolved-methodology')
    transport=publisher=collector=None;rows=[];frames=[];steps=0;fault=None;initial=None;drift=None;recovery=None
    started=time.monotonic_ns()
    try:
        transport=WebSocketTransport(socket_path=socket_path);os.chmod(socket_path,0o600)
        def sample():
            state=adapter.read_state()
            if tuple(state['robot']['joint_names'])!=registry.joint_names:raise RuntimeError('CANONICAL_ORDER_CHANGED')
            return state['robot']['joint_positions_rad'],state['objects'],state
        publisher=StatePublisher(registry,sample,transport,output/'publish.csv',neutral_check=reset_manager.verify_state)
        transport.health_provider=publisher.health
        dispatcher=CommandDispatcher(reset_manager,events,station_id=station_id,allowed_client='127.0.0.1',
                                     hold_robot=make_robot_hold(adapter),publisher=publisher)
        def send(command,args):
            request=dict(version=1,kind='private_command',control_session_id=dispatcher.control_session_id,
                         request_id=uuid.uuid4().hex,command=command,args=args)
            future=dispatcher.submit(json.dumps(request),'127.0.0.1')
            for _ in range(3):
                if future.done():break
                dispatcher.advance()
            return request,future.result(timeout=1)
        _,initial=send('reset',{})
        if not initial['reset_ok']:raise RuntimeError('INITIAL_RESET_FAILED')
        collector=LocalCollector(socket_path,registry)
        def publish():
            nonlocal steps
            for _ in range(2):
                adapter.robot.write_data_to_sim();adapter.sim.step(render=False)
                adapter.robot.update(adapter.sim.get_physics_dt());steps+=1
                if not dispatcher.after_physics_step():raise RuntimeError('NEUTRAL_HOLD_FAILED')
            due=publisher.epoch_ns+publisher.deadline_index*1_000_000_000//publisher.rate_hz
            remaining=(due-time.monotonic_ns())/1e9
            if remaining>0:time.sleep(remaining)
            frame=publisher.after_step(adapter.sim_time,steps)
            if frame is None:raise RuntimeError(publisher.fault or 'PUBLISH_FAILED')
            deadline=time.monotonic()+5
            while collector.last is None or collector.last['seq']<frame['seq']:
                if collector.error or time.monotonic()>deadline:raise RuntimeError('RECEIVE_FAILED')
                time.sleep(.001)
            received=collector.last
            if received!=frame:raise RuntimeError('RECEIVED_PAYLOAD_DIFFERS')
            comparison=public_neutral(received,registry,neutral,reset_manager.tolerances)
            if not comparison['neutral']:raise RuntimeError('PUBLISHED_NONNEUTRAL')
            frames.append(received)
            return received,comparison
        publish()
        for action,target in sorted(LEGAL_PAIRS):
            before=sha256(adapter.read_state());request,reply=send('demo',dict(action=action,target=target))
            after=sha256(adapter.read_state())
            if reply['accepted'] or reply['reason']!='PROTECTED_TARGET_COMMAND' or before!=after:
                raise RuntimeError('PROTECTED_REJECTION_FAILED')
            received,comparison=publish()
            rows.append(dict(action=action,target=target,request=request,reply=reply,full_state_sha256_before=before,
                full_state_sha256_after=after,received_seq=received['seq'],received_payload_sha256=sha256(received),**comparison))
        # A real visible-state mutation must stop publication, not be overwritten
        # by the robot-only hold or silently projected back to neutral.
        changed=adapter.accessors.read_state();card=next(x for x in changed.values() if 'card_face' in x['state'])
        card['state']['card_face']=1-card['state']['card_face'];adapter.accessors.apply_state(changed)
        before_count=publisher.published
        adapter.robot.write_data_to_sim();adapter.sim.step(render=False);adapter.robot.update(adapter.sim.get_physics_dt());steps+=1
        hold_rejected=not dispatcher.after_physics_step()
        due=publisher.epoch_ns+publisher.deadline_index*1_000_000_000//publisher.rate_hz
        remaining=(due-time.monotonic_ns())/1e9
        if remaining>0:time.sleep(remaining)
        suppressed=publisher.after_step(adapter.sim_time,steps) is None
        drift=dict(hold_rejected=hold_rejected,publication_suppressed=suppressed,
                   count_unchanged=publisher.published==before_count,publisher_fault=publisher.fault,
                   object_not_silently_restored=adapter.accessors.read_state()==changed)
        _,recovery=send('reset',{})
    except Exception as error:
        fault=type(error).__name__+': '+str(error)
    finally:
        for resource in (collector,publisher,events):
            if resource is not None:
                try:resource.close()
                except Exception as error:fault=fault or 'EVIDENCE_FINALIZATION_FAILED: '+type(error).__name__
        if publisher is None and transport is not None:
            try:transport.close()
            except Exception as error:fault=fault or 'TRANSPORT_CLOSE_FAILED: '+type(error).__name__
    for name,value in (('rejections.json',rows),('received-frames.json',frames)):
        durable(output/name,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())
    report=dict(source_kind='live',source='loaded_isaac_articulation_and_real_unix_websocket',
        scene_sha256=registry.scene_sha256,reset_snapshot_sha256=registry.reset_snapshot_sha256,
        initial_reset_ok=bool(initial and initial['reset_ok']),protected_rejections=len(rows),
        received_frames=len(frames),received_after_rejection=len(rows),joint_count=len(registry.joint_names),
        object_count=len(registry.object_states),all_received_neutral=bool(len(rows)==32 and all(r['neutral'] for r in rows)),
        all_rejections_full_state_unchanged=bool(len(rows)==32 and all(r['full_state_sha256_before']==r['full_state_sha256_after'] for r in rows)),
        transport_sequence_gaps=collector.sequence_gaps if collector else None,physics_steps=steps,
        elapsed_seconds=(time.monotonic_ns()-started)/1e9,drift=drift,
        explicit_recovery_reset_ok=bool(recovery and recovery['reset_ok']),fault=fault,
        actual_demo_execution=False,rate_qualification=False,
        max_deviation={key:max((r['max_deviation'][key] for r in rows),default=None) for key in ('joint_rad','position_m','orientation_rad')})
    report['passed']=bool(fault is None and report['all_received_neutral'] and report['all_rejections_full_state_unchanged'] and
        report['transport_sequence_gaps']==0 and drift and drift['hold_rejected'] and drift['publication_suppressed'] and
        drift['count_unchanged'] and drift['publisher_fault']=='NEUTRAL_DIVERGED' and
        drift['object_not_silently_restored'] and report['explicit_recovery_reset_ok'])
    report['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    durable(output/'summary.json',(json.dumps(report,indent=2,allow_nan=False)+'\n').encode())
    return report
