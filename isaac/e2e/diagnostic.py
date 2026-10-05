"""Opt-in actual ABBA profile; no qualification, native visit or deployment claim."""
from dataclasses import asdict
import csv
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def close_phase_resources(process, stop_path, write_stop, resources, errors):
    """Attempt every independent cleanup, retaining failures for the summary."""
    def record(name, error):
        errors.append(name+': '+type(error).__name__+': '+str(error))
    if process is not None:
        try: write_stop(stop_path,b'stop')
        except Exception as error: record('client stop marker',error)
        try: process.wait(timeout=5)
        except Exception as error:
            record('client graceful wait',error)
            try: process.terminate()
            except Exception as error: record('client terminate',error)
            try: process.wait(timeout=5)
            except Exception as error: record('client final wait',error)
    for name,resource in resources:
        if resource is not None:
            try: resource.close()
            except Exception as error: record(name,error)


def run_same_iteration_check(manager, layout, output, *, phase_seconds=30.):
    from isaac.commands.dispatcher import CommandDispatcher
    from isaac.commands.event_log import DurableCommandLog
    from isaac.commands.hold import make_robot_hold
    from isaac.commands.queue import CommandQueue
    from isaac.commands.transport import PrivateCommandTransport
    from isaac.e2e.same_iteration import SameIterationCapture, StageMutationWatch, VerifiedTransport, advance_once_verified
    from isaac.e2e.service import advance_once
    from isaac.publisher.benchmark import registry_from_snapshot
    from isaac.publisher.runtime import StatePublisher
    from isaac.publisher.transport import WebSocketTransport
    from isaac.reset.benchmark import durable
    from isaac.soak.host import StepTrace
    if type(phase_seconds) not in (int,float) or not math.isfinite(phase_seconds) or not 12 <= phase_seconds <= 30:
        raise ValueError('Bounded12..30 second phase required')
    adapter=manager.adapter
    if not math.isclose(adapter.sim.get_physics_dt(),1/60,rel_tol=0,abs_tol=1e-8):
        raise ValueError('Actual60Hz physics configuration required')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    registry=registry_from_snapshot(layout,{'state':manager.neutral_state,'scene_sha256':adapter.scene_sha256},
        manager.reset_snapshot_sha256,'diagnostic-01',Path(__file__).resolve().parents[2]/'docs/spikes/isaac/joint_inventory.csv')
    def save(path,value): durable(path,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())
    report=dict(scope='actual_isaac_bounded_abba',participant=False,qualification=False,
        scene_sha256=registry.scene_sha256,reset_snapshot_sha256=registry.reset_snapshot_sha256,
        phase_seconds=phase_seconds,phases=[],fault=None,gc_enabled=gc.isenabled(),gc_thresholds=list(gc.get_threshold()))
    for number,candidate in enumerate((False,True,True,False)):
        directory=output/(str(number+1)+('-candidate' if candidate else '-baseline'));directory.mkdir()
        public=publisher=journal=handoff=private=trace=process=watch=proof=stderr=None
        measurements={};gcs=[];gc_start={};cleanups=[];rows=[];failure=None;steps=0;started=ended=time.monotonic_ns()
        original_read,original_verify=adapter.read_state,manager.verify_state
        def measured(name,fn):
            def call(*args,**kwargs):
                before=time.monotonic_ns()
                try:return fn(*args,**kwargs)
                finally: measurements.setdefault(name,[]).append(time.monotonic_ns()-before)
            return call
        def gc_note(phase,info):
            generation=info['generation']
            if phase=='start':gc_start[generation]=time.monotonic_ns()
            elif generation in gc_start:
                gcs.append(dict(generation=generation,elapsed_ms=(time.monotonic_ns()-gc_start.pop(generation))/1e6,
                    collected=info['collected'],uncollectable=info['uncollectable']))
        with tempfile.TemporaryDirectory(prefix='av-abba-') as sockets:
            try:
                reset=manager.reset();save(directory/'initial-reset.json',reset)
                if reset['reset_ok'] is not True:raise RuntimeError('Initial reset refused')
                public_path=Path(sockets)/'state.sock';private_path=Path(sockets)/'command.sock'
                public=WebSocketTransport(socket_path=public_path)
                if candidate:
                    proof=SameIterationCapture(manager,lambda:watch.identity())
                    watch=StageMutationWatch(adapter,proof.notify_mutation)
                def sample():
                    state=adapter.read_state()
                    return state['robot']['joint_positions_rad'],state['objects'],state
                publisher=StatePublisher(registry,proof.sample if candidate else sample,
                    VerifiedTransport(public,proof) if candidate else public,directory/'publish.csv',rate_hz=30,
                    neutral_check=proof.neutral_check if candidate else manager.verify_state)
                public.health_provider=publisher.health
                journal=DurableCommandLog(directory/'commands.jsonl',session_id=uuid.uuid4().hex,
                    apparatus_version='same-iteration-diagnostic',protocol_version='SIMULATION_TEST')
                dispatcher=CommandDispatcher(manager,journal,station_id='diagnostic-01',allowed_client=f'uid:{os.getuid()}',
                    hold_robot=measured('hold',make_robot_hold(adapter)),publisher=publisher)
                handoff=CommandQueue(dispatcher)
                private=PrivateCommandTransport(handoff,socket_path=private_path,allowed_uid=os.getuid())
                trace=StepTrace(directory/'physics-steps.jsonl')
                save(directory/'client-config.json',dict(registry=asdict(registry),public_socket=str(public_path),
                    private_socket=str(private_path),control_session_id=dispatcher.control_session_id))
                env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join(str(x) for x in sys.path if x)
                stderr=(directory/'client-stderr.txt').open('x')
                process=subprocess.Popen([sys.executable,'-m','isaac.e2e.diagnostic_client',str(directory)],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,env=env)
                deadline=time.monotonic()+10
                while not (directory/'client-ready.json').exists():
                    if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Actual client readiness failed')
                    time.sleep(.01)
                adapter.read_state=measured('full_read',original_read)
                manager.verify_state=measured('full_verify',original_verify)
                # Bind the baseline callback to the same instrumented full comparator.
                if not candidate:publisher.neutral_check=manager.verify_state
                gc.callbacks.append(gc_note)
                started=time.monotonic_ns();publisher.epoch_ns=started
                durable(directory/'go',str(started).encode())
                while time.monotonic_ns()-started < phase_seconds*1e9:
                    if process.poll() is not None:raise RuntimeError('Diagnostic client exited early')
                    before=time.monotonic_ns()
                    if candidate:steps,frame,stopped=advance_once_verified(adapter,dispatcher,handoff,publisher,proof,steps,trace)
                    else:steps,frame,stopped=advance_once(adapter,dispatcher,handoff,publisher,steps,trace)
                    rows.append((steps,before,time.monotonic_ns(),frame is not None))
                    if stopped:raise RuntimeError('Unexpected stopped dispatcher')
                    remaining=(started+steps*1_000_000_000//60-time.monotonic_ns())/1e9
                    if remaining>0:time.sleep(remaining)
                ended=time.monotonic_ns()
            except Exception as error:
                ended=time.monotonic_ns();failure=type(error).__name__+': '+str(error)
            finally:
                if gc_note in gc.callbacks:gc.callbacks.remove(gc_note)
                adapter.read_state,manager.verify_state=original_read,original_verify
                close_phase_resources(process,directory/'stop',durable,
                    [('private',private),('publisher',publisher),('public',public if publisher is None else None),
                     ('trace',trace),('journal',journal),('watch',watch),('proof',proof),('stderr',stderr)],cleanups)
        with (directory/'iterations.csv').open('x',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['step','start_host_ns','end_host_ns','published']);writer.writerows(rows)
            stream.flush();os.fsync(stream.fileno())
        save(directory/'scope-durations-ns.json',measurements);save(directory/'gc.json',gcs)
        client=json.loads((directory/'client-result.json').read_text()) if (directory/'client-result.json').exists() else None
        result=dict(candidate=candidate,elapsed_seconds=(ended-started)/1e9,physics_steps=steps,
            frames=publisher.published if publisher else 0,missed_deadlines=publisher.missed if publisher else 0,
            fault=failure,cleanup_errors=cleanups,client_exit=process.returncode if process else None,
            client_count=client['count'] if client else 0,client_error=client['error'] if client else 'missing',
            client_sequence_gaps=client['sequence_gaps'] if client else None,
            command_count=len(client['commands']) if client else 0,
            measured_call_counts={key:len(value) for key,value in measurements.items()})
        result['completed']=failure is None and not cleanups and result['client_exit']==0 and result['client_count']>0 and result['command_count']==6
        result['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}
        save(directory/'summary.json',result);report['phases'].append(result)
        if not result['completed']:
            report['fault']='Phase incomplete; candidate testing stopped';break
    try:report['final_reset']=manager.reset()
    except Exception as error:report['fault']='Final reset failed: '+type(error).__name__
    report['completed']=len(report['phases'])==4 and report['fault'] is None and report['final_reset']['reset_ok'] is True
    save(output/'summary.json',report)
    return report
