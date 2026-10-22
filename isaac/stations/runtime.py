"""Isaac/publisher/command/gateway components for one supervised station.

This mirrors the validated bounded paths (``isaac.workcell.run_scene`` scene
construction and ``isaac.e2e.service.run_joined_service`` service wiring) but
runs until stopped. It has NOT been executed in Isaac by this change; the
remote validation procedure in docs/isaac/stations.md is still required.
All Isaac imports are deferred so the module imports on any platform.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

from isaac.reset.benchmark import durable
from .supervisor import Component

ROOT = Path(__file__).resolve().parents[2]
PHYSICS_HZ = 60


def gpu_inventory(run=subprocess.run):
    """Read-only GPU/driver query inside the container (its GPU appears as index 0)."""
    try:
        result = run(['nvidia-smi', '--query-gpu=index,name,uuid,driver_version,memory.total',
                      '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10, check=True)
        rows = [dict(zip(('index', 'name', 'uuid', 'driver_version', 'memory_total_mib'),
                         (field.strip() for field in row))) for row in csv.reader(result.stdout.strip().splitlines())]
        return dict(gpus=rows, error=None)
    except (OSError, subprocess.SubprocessError) as error:
        return dict(gpus=[], error=type(error).__name__+': '+str(error))


def build_station(pins, *, journal, runtime, output, isaac_args, observer_camera):
    """Return (ordered components, step callable, identity) for exactly one station."""
    value, layout, snapshot = pins['config'], pins['layout'], pins['snapshot']
    station_id, uid = value['station_id'], value['allowed_uid']
    run = Path(output)/'runs'/journal.run_id
    run.mkdir(parents=True, exist_ok=False)
    control_session_id = uuid.uuid4().hex
    s = SimpleNamespace(app=None, sim=None, adapter=None, manager=None, reset_log=None, trace=None,
                        publisher=None, registry=None, command_log=None, dispatcher=None, handoff=None,
                        private=None, gateway=None, steps=0, started_ns=None, ready=False)

    def save(name, data):
        durable(run/name, (json.dumps(data, indent=2, allow_nan=False)+'\n').encode())

    def start_app():
        sys.path.insert(0, str(ROOT/'spikes/O5.1.2'))
        from evidence import require_revision, verify_loopback_only
        verify_loopback_only()
        s.pins = json.loads((ROOT/'spikes/O5.1.2/pins.json').read_text())
        require_revision(Path('/lab'), s.pins['isaac_lab_commit'])
        require_revision(Path('/unitree'), s.pins['unitree_commit'])
        from isaaclab.app import AppLauncher
        parser = argparse.ArgumentParser()
        AppLauncher.add_app_launcher_args(parser)
        s.app_args = parser.parse_args(list(isaac_args))
        if observer_camera:
            s.app_args.enable_cameras = True
        s.app = AppLauncher(s.app_args).app
        journal.record('inventory', container_pid=os.getpid(), gpu_index_host=value['gpu_index'],
                       isaac_build=Path('/isaac-sim/VERSION').read_text().strip(), **gpu_inventory())

    def close_app():
        if s.app is not None:
            s.app.close(wait_for_replicator=False, skip_cleanup=True)

    def start_scene():
        import torch
        import omni.usd
        import isaaclab.sim as sim_utils
        from isaaclab.assets import Articulation
        from isaac.workcell.build_usd import build_workcell
        from isaac.reset.isaac_adapter import IsaacResetAdapter
        from isaac.reset.manager import ResetManager
        from isaac.reset.event_log import DurableResetLog
        from isaac.soak.host import StepTrace
        s.sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1/PHYSICS_HZ, device=s.app_args.device))
        stage = omni.usd.get_context().get_stage()
        accessors = build_workcell(stage, layout)
        os.environ['PROJECT_ROOT'] = '/unitree'
        spec = importlib.util.spec_from_file_location('workcell_unitree_cfg', '/unitree/robots/unitree.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = module.G129_CFG_WITH_DEX3_BASE_FIX.copy()
        cfg.prim_path = layout['robot']['prim_path']
        cfg.spawn.usd_path = str(Path('/assets')/s.pins['asset_relative_path'])
        cfg.init_state.pos = tuple(layout['robot']['position_m'])
        with (ROOT/'docs/spikes/isaac/joint_inventory.csv').open() as handle:
            names = [row['name'] for row in csv.DictReader(handle)]
        cfg.init_state.joint_pos = {name: layout['robot']['neutral_joint_overrides_rad'].get(name, 0.) for name in names}
        robot = Articulation(cfg)
        if observer_camera:
            from isaaclab.sensors import Camera, CameraCfg
            observer = layout['observer']
            s.camera = Camera(CameraCfg(prim_path='/World/ObserverReference', update_period=0.,
                width=observer['width'], height=observer['height'], data_types=['rgb'],
                spawn=sim_utils.PinholeCameraCfg(focal_length=observer['focal_length_mm'],
                    horizontal_aperture=observer['horizontal_aperture_mm'], clipping_range=(.01, 100.))))
        scene_path = run/'workcell.usda'
        stage.GetRootLayer().Export(str(scene_path))
        scene_hash = hashlib.sha256(scene_path.read_bytes()).hexdigest()
        if scene_hash != value['scene_sha256']:
            raise RuntimeError('SCENE_HASH_MISMATCH')
        s.sim.reset(); robot.update(1/PHYSICS_HZ)
        robot.write_root_state_to_sim(robot.data.default_root_state)
        robot.write_joint_state_to_sim(robot.data.default_joint_pos, torch.zeros_like(robot.data.default_joint_vel))
        s.sim.forward(); robot.update(1/PHYSICS_HZ)
        if not robot.is_fixed_base or len(robot.joint_names) != 43:
            raise RuntimeError('Fixed-base 29+7+7 articulation required')
        s.adapter = IsaacResetAdapter(robot, accessors, s.sim, scene_hash)
        s.reset_log = DurableResetLog(run/'reset-events.jsonl', session_id=uuid.uuid4().hex,
            apparatus_version='station-service-development', protocol_version='SIMULATION_TEST')
        s.manager = ResetManager(s.adapter, snapshot, value['reset_snapshot_sha256'], s.reset_log)
        result = s.manager.reset()
        save('initial-reset.json', result)
        if result.get('reset_ok') is not True:
            raise RuntimeError('INITIAL_RESET_FAILED')
        s.trace = StepTrace(run/'physics-steps.jsonl')

    def close_scene():
        errors = []
        for resource in (s.trace, s.reset_log):
            if resource is not None:
                try: resource.close()
                except Exception as error: errors.append(error)
        if errors:
            raise errors[0]

    def start_publisher():
        from isaac.publisher.benchmark import registry_from_snapshot
        from isaac.publisher.runtime import StatePublisher
        from isaac.publisher.transport import WebSocketTransport
        s.registry = registry_from_snapshot(layout, {'state': s.manager.neutral_state,
            'scene_sha256': s.adapter.scene_sha256}, value['reset_snapshot_sha256'], station_id,
            ROOT/'docs/spikes/isaac/joint_inventory.csv')
        save('registry.json', dict(station_id=s.registry.station_id, scene_sha256=s.registry.scene_sha256,
            reset_snapshot_sha256=s.registry.reset_snapshot_sha256, joint_names=list(s.registry.joint_names),
            object_states=[[key, list(fields)] for key, fields in s.registry.object_states],
            anchor_ids=list(s.registry.anchor_ids)))
        path = runtime['internal_sockets']['state']
        public = WebSocketTransport(socket_path=path)
        os.chmod(path, 0o600)
        def sample():
            state = s.adapter.read_state()
            if tuple(state['robot']['joint_names']) != s.registry.joint_names:
                raise RuntimeError('CANONICAL_ORDER_CHANGED')
            return state['robot']['joint_positions_rad'], state['objects'], state
        try:
            s.publisher = StatePublisher(s.registry, sample, public, run/'publish.csv', rate_hz=value['publisher_hz'],
                                         neutral_check=s.manager.verify_state)
        except Exception:
            public.close()
            raise
        public.health_provider = s.publisher.health

    def close_publisher():
        if s.publisher is not None:
            s.publisher.close()

    def start_commands():
        from isaac.commands.dispatcher import CommandDispatcher
        from isaac.commands.event_log import DurableCommandLog
        from isaac.commands.hold import make_robot_hold
        from isaac.commands.queue import CommandQueue
        from isaac.commands.transport import PrivateCommandTransport
        s.command_log = DurableCommandLog(run/'commands.jsonl', session_id=uuid.uuid4().hex,
            apparatus_version='station-service-development', protocol_version='SIMULATION_TEST')
        try:
            s.dispatcher = CommandDispatcher(s.manager, s.command_log, station_id=station_id,
                allowed_client=f'uid:{uid}', hold_robot=make_robot_hold(s.adapter), publisher=s.publisher)
            s.dispatcher.control_session_id = control_session_id
            s.handoff = CommandQueue(s.dispatcher)
            s.private = PrivateCommandTransport(s.handoff, socket_path=runtime['internal_sockets']['commands'],
                                                allowed_uid=uid)
        except Exception:
            s.command_log.close()
            raise

    def close_commands():
        try:
            if s.private is not None:
                s.private.close()  # Rejects and logs pending commands first.
        finally:
            s.command_log.close()

    def start_gateway():
        from .gateway import StationGateway
        s.gateway = StationGateway(runtime, uid, journal.record)

    def close_gateway():
        return dict(gateway_counts=s.gateway.close()) if s.gateway is not None else None

    def step():
        from isaac.e2e.service import advance_once
        if s.started_ns is None:
            s.started_ns = time.monotonic_ns()
            s.publisher.epoch_ns = s.started_ns
        s.steps, frame, stopped = advance_once(s.adapter, s.dispatcher, s.handoff, s.publisher, s.steps, s.trace)
        if stopped:
            return False
        if frame is not None and not s.ready:
            health = s.handoff.health()
            if health['exposure_ready'] is not True:
                raise RuntimeError('INITIAL_NEUTRAL_NOT_READY')
            journal.record('ready', control_session_id=control_session_id,
                           public_session_id=s.publisher.encoder.session_id,
                           registry_sha256=hashlib.sha256((run/'registry.json').read_bytes()).hexdigest(),
                           external_sockets={k: str(v) for k, v in runtime['external'].items()},
                           run_directory=str(run), physics_steps=s.steps)
            s.ready = True
        remaining = (s.started_ns+s.steps*1_000_000_000//PHYSICS_HZ-time.monotonic_ns())/1e9
        if remaining > 0:
            time.sleep(remaining)
        return True

    # Start order; the supervisor closes in exact reverse: gateway, commands,
    # publisher, scene evidence, Isaac app.
    components = [Component('isaac_app', start_app, close_app), Component('scene', start_scene, close_scene),
                  Component('publisher', start_publisher, close_publisher),
                  Component('commands', start_commands, close_commands),
                  Component('gateway', start_gateway, close_gateway)]
    identity = dict(station_id=station_id, logical_host=value['logical_host'], gpu_index=value['gpu_index'],
                    image_digest=value['image_digest'], source_revision=value['source_revision'],
                    scene_sha256=value['scene_sha256'], reset_snapshot_sha256=value['reset_snapshot_sha256'],
                    layout_sha256=value['layout_sha256'], publisher_hz=value['publisher_hz'],
                    dds_domain_id=value['dds_domain_id'], ros_domain_id=value['ros_domain_id'],
                    run_directory=str(run), uid=uid)
    return components, step, identity
