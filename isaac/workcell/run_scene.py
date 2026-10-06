"""Generate and measure the provisional workcell in an isolated Isaac process."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'spikes/O5.1.2'))
from evidence import verify_loopback_only, require_revision
from isaac.workcell.layout import canonical_bytes, digest, preconditions
from isaac.view_capture.options import add_arguments as add_view_arguments, profile_options as view_options


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--layout',type=Path,default=ROOT/'apparatus/workcell_layout.json')
    parser.add_argument('--reset-check',action='store_true')
    parser.add_argument('--reset-cycles',type=int,default=1000)
    parser.add_argument('--skip-reach',action='store_true')
    parser.add_argument('--capture',action='store_true')
    parser.add_argument('--integration-overlay',type=Path)
    parser.add_argument('--publisher-seconds',type=float,default=0.)
    parser.add_argument('--command-check',action='store_true')
    parser.add_argument('--published-command-check',action='store_true')
    parser.add_argument('--disconnect-check',action='store_true')
    parser.add_argument('--demo-check',action='store_true')
    parser.add_argument('--demo-preflight',action='store_true')
    parser.add_argument('--grip-check',action='store_true')
    parser.add_argument('--protected-stream-seconds',type=float,default=0.)
    parser.add_argument('--protected-socket',type=Path)
    parser.add_argument('--protected-station-id',default='simulator-01')
    parser.add_argument('--e2e-seconds',type=float,default=0.)
    parser.add_argument('--e2e-station-id')
    parser.add_argument('--e2e-host-uid',type=int)
    parser.add_argument('--e2e-control-session-id')
    parser.add_argument('--e2e-public-socket',type=Path)
    parser.add_argument('--e2e-private-socket',type=Path)
    parser.add_argument('--e2e-private-timing-seconds',type=float,default=0.)
    parser.add_argument('--same-iteration-check',action='store_true')
    add_view_arguments(parser)
    early,_=parser.parse_known_args()
    observation_options=view_options(early)
    verify_loopback_only()
    pins=json.loads((ROOT/'spikes/O5.1.2/pins.json').read_text())
    require_revision(Path('/lab'),pins['isaac_lab_commit'])
    require_revision(Path('/unitree'),pins['unitree_commit'])
    from isaaclab.app import AppLauncher
    AppLauncher.add_app_launcher_args(parser)
    args=parser.parse_args()
    if args.integration_overlay:
        # Separate ignored integration tree; never changes issue52 source scope.
        import isaac
        isaac.__path__.append(str(args.integration_overlay/'isaac'))
    args.output.mkdir(parents=True,exist_ok=False)
    if args.capture: args.enable_cameras=True
    app=AppLauncher(args).app
    try:
        import torch
        import omni.usd
        import isaaclab.sim as sim_utils
        from isaaclab.assets import Articulation
        from pxr import Usd
        from isaac.workcell.build_usd import build_workcell
        from isaac.workcell.state import StateAccessors
        layout=json.loads(args.layout.read_text())
        if canonical_bytes(layout)!=args.layout.read_bytes(): raise ValueError('Canonical layout bytes required')
        sim=sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1/60,device=args.device))
        stage=omni.usd.get_context().get_stage()
        accessors=build_workcell(stage,layout)
        os.environ['PROJECT_ROOT']='/unitree'
        spec=importlib.util.spec_from_file_location('workcell_unitree_cfg','/unitree/robots/unitree.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        cfg=module.G129_CFG_WITH_DEX3_BASE_FIX.copy()
        cfg.prim_path=layout['robot']['prim_path']
        cfg.spawn.usd_path=str(Path('/assets')/pins['asset_relative_path'])
        cfg.init_state.pos=tuple(layout['robot']['position_m'])
        import csv
        with (ROOT/'docs/spikes/isaac/joint_inventory.csv').open() as handle:
            names=[row['name'] for row in csv.DictReader(handle)]
        cfg.init_state.joint_pos={name:layout['robot']['neutral_joint_overrides_rad'].get(name,0.) for name in names}
        robot=Articulation(cfg)
        observer=layout['observer']; camera=None
        if args.capture:
            from isaaclab.sensors import Camera,CameraCfg
            camera=Camera(CameraCfg(prim_path='/World/ObserverReference',update_period=0.,
                width=observer['width'],height=observer['height'],data_types=['rgb'],
                spawn=sim_utils.PinholeCameraCfg(focal_length=observer['focal_length_mm'],
                    horizontal_aperture=observer['horizontal_aperture_mm'],clipping_range=(.01,100.))))
        # Export before runtime targets/measurements; no wall-clock data enters USD.
        scene_path=args.output/'workcell.usda'
        stage.GetRootLayer().Export(str(scene_path))
        scene_hash=hashlib.sha256(scene_path.read_bytes()).hexdigest()
        reopened=Usd.Stage.Open(str(scene_path))
        reopened.GetRootLayer().Export(str(args.output/'workcell-reloaded.usda'))
        original_state=accessors.read_state()
        reload_state=StateAccessors(reopened,layout).read_state()
        reloaded_hash=hashlib.sha256((args.output/'workcell-reloaded.usda').read_bytes()).hexdigest()
        if original_state!=reload_state or scene_hash!=reloaded_hash:
            raise RuntimeError('USD round-trip changed state or scene bytes')
        sim.reset(); robot.update(1/60)
        robot.write_root_state_to_sim(robot.data.default_root_state)
        robot.write_joint_state_to_sim(robot.data.default_joint_pos,torch.zeros_like(robot.data.default_joint_vel))
        sim.forward(); robot.update(1/60)
        if not robot.is_fixed_base or len(robot.joint_names)!=43:
            raise RuntimeError('Fixed-base29+7+7 articulation required')
        def capture(path,eye=None,look=None):
            from PIL import Image
            camera.set_world_poses_from_view(torch.tensor([eye or observer['position_m']],device=args.device),
                                            torch.tensor([look or observer['look_at_m']],device=args.device))
            for _ in range(12):
                sim.render(); camera.update(1/60,force_recompute=True)
            Image.fromarray(camera.data.output['rgb'][0,:,:,:3].cpu().numpy()).save(path)
        reach=None
        if not args.skip_reach:
            from isaac.workcell.reach import check_reach
            reach=check_reach(robot,sim,layout)
            (args.output/'reach.json').write_bytes(canonical_bytes(reach))
        if args.capture:
            capture(args.output/'observer.png')
            capture(args.output/'trays-detail.png',[.80,-.45,1.35],[.23,-.23,.86])
            capture(args.output/'containers-detail.png',[.80,.45,1.35],[.23,.23,.87])
        reset=None
        if args.reset_check:
            if args.integration_overlay:
                sys.path.insert(0,str(args.integration_overlay))
                import isaac
                isaac.__path__.append(str(args.integration_overlay/'isaac'))
            from isaac.reset.isaac_adapter import IsaacResetAdapter
            from isaac.reset.benchmark import run_reset_check
            adapter=IsaacResetAdapter(robot,accessors,sim,scene_hash)
            reset=run_reset_check(adapter,args.output/'reset-check',cycles=args.reset_cycles,
                                  capture_image=capture if args.capture else None)
        commands=None;published_commands=None
        if args.command_check or args.published_command_check:
            if reset is None: raise ValueError('Command diagnostic requires actual reset snapshot')
            import uuid
            from isaac.commands.benchmark import run_command_check
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'command-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='workcell-development-v1',protocol_version='unresolved-methodology')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                if args.command_check:
                    commands=run_command_check(manager,args.output/'command-check',physics_steps=240)
                if args.published_command_check:
                    from isaac.commands.published_benchmark import run_published_command_check
                    published_commands=run_published_command_check(manager,layout,args.output/'published-command-check',
                        socket_path='/tmp/av-published-command-check.sock')
            finally: event_log.close()
        publisher=None
        if args.publisher_seconds:
            if reset is None: raise ValueError('Publisher diagnostic requires actual reset snapshot')
            from isaac.publisher.benchmark import run_publisher_check
            publisher=run_publisher_check(adapter,layout,args.output/'reset-check/neutral_v1.json',
                args.output/'publisher-check',expected_snapshot_sha256=reset['reset_snapshot_sha256'],
                seconds=args.publisher_seconds,rate_hz=30,socket_path='/tmp/av-publisher52.sock')
        disconnect=None
        if args.disconnect_check:
            if reset is None: raise ValueError('Disconnect diagnostic requires actual reset snapshot')
            from isaac.publisher.disconnect_benchmark import run_disconnect_check
            disconnect=run_disconnect_check(adapter,layout,args.output/'reset-check/neutral_v1.json',
                args.output/'disconnect-check',expected_snapshot_sha256=reset['reset_snapshot_sha256'],phase_seconds=30.)
        demos=None
        if args.demo_check or args.demo_preflight:
            if reset is None: raise ValueError('Demo diagnostic requires actual reset snapshot')
            import uuid
            from isaac.demos.benchmark import run_demo_check
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'demo-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='workcell-development-v1',protocol_version='unresolved-methodology')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                demos=run_demo_check(manager,layout,args.output/'demo-check',
                    preflight_only=args.demo_preflight,capture_image=capture if args.capture else None)
            finally: event_log.close()
        grip=None
        if args.grip_check:
            if reset is None: raise ValueError('Grip diagnostic requires actual reset snapshot')
            import uuid
            from isaac.demos.grip_probe import run_grip_check
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'grip-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='workcell-development-v1',protocol_version='unresolved-methodology')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                grip=run_grip_check(manager,layout,args.output/'grip-check',
                    capture_image=capture if args.capture else None)
            finally: event_log.close()
        protected=None
        if args.protected_stream_seconds:
            if reset is None or args.protected_socket is None:
                raise ValueError('Protected stream requires actual reset snapshot and explicit private Unix socket')
            import uuid
            from isaac.commands.hold import make_robot_hold
            from isaac.publisher.protected_stream import run_protected_stream
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'protected-stream-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='workcell-development-v1',protocol_version='unresolved-methodology')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                protected=run_protected_stream(manager,layout,args.output/'protected-stream',
                    hold_robot=make_robot_hold(adapter),socket_path=args.protected_socket,
                    station_id=args.protected_station_id,seconds=args.protected_stream_seconds,
                    joint_csv=ROOT/'docs/spikes/isaac/joint_inventory.csv')
            finally: event_log.close()
        e2e=None
        if args.e2e_seconds:
            if reset is None or not args.e2e_station_id or args.e2e_host_uid is None or not args.e2e_control_session_id or args.e2e_public_socket is None or args.e2e_private_socket is None:
                raise ValueError('E2E requires reset snapshot and explicit station, relay UID and separate Unix endpoints')
            import uuid
            from isaac.e2e.service import run_joined_service
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'e2e-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='joined-e2e-development',protocol_version='SIMULATION_TEST')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                e2e=run_joined_service(manager,layout,args.output/'joined-e2e',seconds=args.e2e_seconds,
                    station_id=args.e2e_station_id,host_uid=args.e2e_host_uid,
                    control_session_id=args.e2e_control_session_id,
                    public_socket=args.e2e_public_socket,private_socket=args.e2e_private_socket,
                    private_timing_seconds=args.e2e_private_timing_seconds,
                    view_observation=observation_options,
                    joint_csv=ROOT/'docs/spikes/isaac/joint_inventory.csv')
                if not e2e['service_completed']: raise RuntimeError('JOINED_E2E_SERVICE_FAILED')
            finally: event_log.close()
        same_iteration=None
        if args.same_iteration_check:
            if reset is None: raise ValueError('Same-iteration diagnostic requires actual reset snapshot')
            import uuid
            from isaac.e2e.diagnostic import run_same_iteration_check
            from isaac.reset.snapshot import load_snapshot
            from isaac.reset.manager import ResetManager
            from isaac.reset.event_log import DurableResetLog
            snapshot=load_snapshot(args.output/'reset-check/neutral_v1.json',reset['reset_snapshot_sha256'])
            event_log=DurableResetLog(args.output/'same-iteration-reset-events.jsonl',session_id=uuid.uuid4().hex,
                apparatus_version='same-iteration-diagnostic',protocol_version='SIMULATION_TEST')
            try:
                manager=ResetManager(adapter,snapshot,reset['reset_snapshot_sha256'],event_log)
                same_iteration=run_same_iteration_check(manager,layout,args.output/'same-iteration-check')
                if not same_iteration['completed']: raise RuntimeError('SAME_ITERATION_DIAGNOSTIC_INCOMPLETE')
            finally: event_log.close()
        actual=accessors.read_state()
        conditions=preconditions(layout,actual)
        if conditions['possible_count']!=32: raise RuntimeError('Neutral preconditions incomplete')
        (args.output/'state.json').write_bytes(canonical_bytes(actual))
        (args.output/'environment.json').write_bytes(canonical_bytes(accessors.read_environment()))
        (args.output/'preconditions.json').write_bytes(canonical_bytes(conditions))
        summary=dict(status='completed',layout_sha256=digest(layout),scene_sha256=scene_hash,
            scene_reload_sha256=reloaded_hash,reload_state_identical=original_state==reload_state,
            semantic_objects=len(actual),joint_names=list(robot.joint_names),joint_count=len(robot.joint_names),
            fixed_base=robot.is_fixed_base,preconditions_possible=conditions['possible_count'],
            simulation_time=float(sim.current_time),
            physics_integrated=bool(publisher or commands or published_commands or disconnect or protected or e2e or same_iteration or (demos and demos.get('rows'))),
            unitree_dds_started=False,network_interfaces=['lo'],methodology_review_complete=False,
            reach_summary={k:v for k,v in reach.items() if k!='results'} if reach else None,
            reset_summary=reset,publisher_summary=publisher,command_summary=commands,published_command_summary=published_commands,disconnect_summary=disconnect,
            demo_summary=demos,grip_summary=grip,protected_stream_summary=protected,joined_e2e_summary=e2e,
            same_iteration_summary=same_iteration,
            pins=pins,isaac_build=Path('/isaac-sim/VERSION').read_text().strip(),
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir()) if p.is_file()})
        (args.output/'summary.json').write_bytes(canonical_bytes(summary))
        print(json.dumps(summary),flush=True)
    except Exception:
        import traceback
        error=traceback.format_exc()
        (args.output/'error.txt').write_text(error)
        print(error,flush=True)
        raise
    finally:
        app.close(wait_for_replicator=False,skip_cleanup=True)


if __name__=='__main__': main()
