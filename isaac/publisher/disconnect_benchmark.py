"""Four-phase actual simulator client disconnect cost diagnostic.

Run after the separate one-hour rate measurement, never concurrently. The
receiver control thread connects/disconnects while the simulation thread keeps
stepping and publishing. No USD/PhysX access occurs on the receiver thread.
"""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import threading
import time

from isaac.reset.benchmark import durable
from isaac.reset.snapshot import load_snapshot
from .benchmark import LocalCollector, registry_from_snapshot
from .runtime import StatePublisher
from .transport import WebSocketTransport


def run_disconnect_check(adapter, layout, snapshot_path, output, *, expected_snapshot_sha256,
                         phase_seconds=30., station_id='station-01', socket_path='/tmp/av-disconnect.sock',
                         joint_csv=None, collector_mode='thread'):
    if collector_mode not in ('thread', 'process'):
        raise ValueError('Explicit thread or process diagnostic collector required')
    from .process_collector import ProcessCollector
    collector_factory=LocalCollector if collector_mode=='thread' else ProcessCollector
    if type(phase_seconds) not in (int, float) or not math.isfinite(phase_seconds) or phase_seconds < 10:
        raise ValueError('At least ten seconds per diagnostic phase')
    snapshot=load_snapshot(snapshot_path,expected_snapshot_sha256)
    if snapshot['scene_sha256']!=adapter.scene_sha256: raise ValueError('Scene identity changed')
    joint_csv=joint_csv or Path(__file__).resolve().parents[2]/'docs/spikes/isaac/joint_inventory.csv'
    registry=registry_from_snapshot(layout,snapshot,expected_snapshot_sha256,station_id,joint_csv)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    adapter.write_state(snapshot['state']);adapter.step_fixed(1)
    for _ in range(60):
        adapter.robot.write_data_to_sim();adapter.sim.step(render=False);adapter.robot.update(adapter.sim.get_physics_dt())
    transport=WebSocketTransport(socket_path=Path(socket_path))
    def sample():
        return adapter.robot.root_physx_view.get_dof_positions()[0].tolist(),adapter.accessors.read_public_state(),None
    publisher=StatePublisher(registry,sample,transport,output/'publish.csv',rate_hz=30)
    transport.health_provider=publisher.health
    stop=threading.Event();client_events=[];errors=[]
    names=['no_client','connected','disconnected','reconnected']
    start=time.monotonic_ns();publisher.epoch_ns=start
    def wait_to(seconds):
        return stop.wait(max(0.,seconds-(time.monotonic_ns()-start)/1e9))
    def receive_schedule():
        current=None
        try:
            for phase in (1,3):
                if wait_to(phase*phase_seconds):break
                current=collector_factory(Path(socket_path),registry)
                client_events.append(dict(phase=names[phase],event='connected',host_mono_ns=time.monotonic_ns()))
                wait_to((phase+1)*phase_seconds)
                current.close()
                client_events.append(dict(phase=names[phase],event='disconnected',host_mono_ns=time.monotonic_ns(),
                    received=current.count,sequence_gaps=current.sequence_gaps,error=str(current.error) if current.error else None))
                if current.error: errors.append(str(current.error))
                current=None
        except Exception as error:errors.append(type(error).__name__+': '+str(error))
        finally:
            if current:current.close()
    thread=threading.Thread(target=receive_schedule,name='disconnect-schedule',daemon=True);thread.start()
    rows=[];step=0;failure=None
    try:
        while time.monotonic_ns()-start < phase_seconds*4e9:
            began=time.monotonic_ns();phase=min(3,int((began-start)/(phase_seconds*1e9)))
            adapter.robot.write_data_to_sim();adapter.sim.step(render=False);adapter.robot.update(adapter.sim.get_physics_dt())
            physics_done=time.monotonic_ns();step+=1
            publisher.after_step(adapter.sim_time,step)
            done=time.monotonic_ns()
            rows.append(dict(phase=names[phase],step=step,host_mono_ns=done,connected_clients=transport.metrics()['connected_clients'],
                physics_ms=(physics_done-began)/1e6,publisher_call_ms=(done-physics_done)/1e6))
            if publisher.fault or errors:raise RuntimeError(publisher.fault or errors[0])
    except Exception as error:failure=type(error).__name__+': '+str(error)
    finally:
        end=time.monotonic_ns();stop.set();thread.join(10)
        if thread.is_alive():failure='Receiver control thread did not stop'
        publisher.close()
    with (output/'steps.csv').open('x',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['phase','step','host_mono_ns','connected_clients','physics_ms','publisher_call_ms']);writer.writeheader();writer.writerows(rows)
        handle.flush();os.fsync(handle.fileno())
    phases=[]
    for index,name in enumerate(names):
        selected=[row for row in rows if row['phase']==name]
        duration=max(0.,min(phase_seconds,(end-start)/1e9-index*phase_seconds))
        phases.append(dict(phase=name,steps=len(selected),elapsed_s=duration,physics_steps_per_host_second=len(selected)/duration if duration else 0.,
            physics_call_median_ms=statistics.median(row['physics_ms'] for row in selected) if selected else None,
            publisher_call_median_ms=statistics.median(row['publisher_call_ms'] for row in selected) if selected else None,
            active_processing_total_s=sum(row['physics_ms']+row['publisher_call_ms'] for row in selected)/1000))
    rates=[phase['physics_steps_per_host_second'] for phase in phases]
    comparisons=[]
    for left,right in ((0,1),(1,2),(2,3),(0,2),(0,3)):
        difference=abs(rates[right]-rates[left])/rates[left] if rates[left] else None
        comparisons.append(dict(reference=names[left],comparison=names[right],absolute_rate_change_fraction=difference,
            within_five_percent=difference is not None and difference<=.05))
    completed=failure is None and not errors and (end-start)/1e9>=phase_seconds*4
    actual_connections=all(any(event['phase']==name and event['event']=='disconnected' and event.get('received',0)>0
                               for event in client_events) for name in ('connected','reconnected'))
    summary=dict(source_kind='live',collector_mode=collector_mode,phase_seconds_requested=phase_seconds,elapsed_s=(end-start)/1e9,
        completed=completed,failure=failure,phases=phases,
        comparisons=comparisons,client_events=client_events,client_errors=errors,
        no_main_loop_wait_for_client=True,unpaced_simulation=True,public_publish_rate_hz=30,protected_mode=False,
        scene_sha256=adapter.scene_sha256,snapshot_sha256=expected_snapshot_sha256,
        five_percent_disconnect_screen=completed and actual_connections and all(item['within_five_percent'] for item in comparisons),
        limitations=['Separate unprotected engineering throughput diagnostic; not headset or network acceptance',
                     'Includes actual background workload variation; exact phase timings and active costs retained'],
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    durable(output/'summary.json',(json.dumps(summary,indent=2,allow_nan=False)+'\n').encode())
    return summary
