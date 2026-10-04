"""Isolated fixed-base articulation spike; requires the approved pinned installation.

Run with Isaac Lab's Python launcher, inside a network-none container. This does
not import Unitree's sim_main or start DDS. Outputs are real measurements only.
"""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import traceback

from evidence import hash_files, require_revision, summarize, verify_loopback_only, write_csv


def sample_resources(output, stop):
    previous_wall, previous_cpu = time.monotonic(), time.process_time()
    with output.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["host_monotonic_ns", "gpu_total_used_mib", "process_rss_mib",
                         "cpu_one_core_percent", "gpu_sample_status"])
        while not stop.is_set():
            gpu, status = "", "unavailable"
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "--id=0"],
                    capture_output=True, text=True, timeout=3, check=True)
                gpu, status = float(result.stdout.strip()), "ok"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            rss = next((int(line.split()[1]) / 1024 for line in
                        Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:")), "")
            now, cpu = time.monotonic(), time.process_time()
            writer.writerow([time.monotonic_ns(), gpu, rss,
                             100 * (cpu - previous_cpu) / (now - previous_wall), status])
            stream.flush()
            previous_wall, previous_cpu = now, cpu
            stop.wait(1)


def dump_inventory(robot, stage, output):
    from pxr import Usd, UsdPhysics
    prims = {}
    for prim in Usd.PrimRange(stage.GetPrimAtPath("/World/Robot_0")):
        if prim.IsA(UsdPhysics.Joint):
            prims.setdefault(prim.GetName(), []).append(prim)
    rows = []
    for index, name in enumerate(robot.joint_names):
        matches = prims.get(name, [])
        if len(matches) != 1:
            raise RuntimeError("Cannot map articulation joint uniquely to its USD prim")
        prim = matches[0]
        axis = prim.GetAttribute("physics:axis").Get()
        lower, upper = robot.data.joint_pos_limits[0, index].tolist()
        group = "left_hand" if name.startswith("left_hand_") else (
            "right_hand" if name.startswith("right_hand_") else "body")
        rows.append({"index": index, "name": name, "group": group,
                     "usd_type": prim.GetTypeName(), "axis": axis,
                     "lower_rad_or_m": lower, "upper_rad_or_m": upper,
                     "default_rad_or_m": robot.data.default_joint_pos[0, index].item(),
                     "prim_path": str(prim.GetPath())})
    write_csv(output, list(rows[0]), rows)
    counts = {group: sum(r["group"] == group for r in rows)
              for group in ("body", "left_hand", "right_hand")}
    if counts != {"body": 29, "left_hand": 7, "right_hand": 7}:
        raise RuntimeError("Measured joint counts differ from 29+7+7; inspect inventory")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unitree-root", type=Path, required=True)
    parser.add_argument("--isaac-lab-root", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True,
                        help="Directory containing assets/robots")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--num-envs", type=int, choices=(1, 2), default=1)
    parser.add_argument("--physics-dt", type=float, default=1 / 60)
    parser.add_argument("--capture", action="store_true",
                        help="Enable an offscreen RGB camera; distinct from a visible viewport")
    parser.add_argument("--preflight-only", action="store_true")
    # No middleware or simulator import until namespace and revisions pass.
    early, _ = parser.parse_known_args()
    verify_loopback_only()
    pins_path = Path(__file__).with_name("pins.json")
    pins = json.loads(pins_path.read_text())
    require_revision(early.unitree_root, pins["unitree_commit"])
    require_revision(early.isaac_lab_root, pins["isaac_lab_commit"])
    asset = early.asset_root / pins["asset_relative_path"]
    if not asset.is_file():
        raise SystemExit("Pinned fixed-base asset missing")
    if early.preflight_only:
        print("PASS: network namespace, source pins, and fixed-base asset path")
        return
    from isaaclab.app import AppLauncher
    AppLauncher.add_app_launcher_args(parser)
    args = parser.parse_args()
    if args.seconds <= 0 or args.physics_dt <= 0:
        parser.error("Duration and physics dt must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    if args.capture:
        args.enable_cameras = True
    rendered = args.capture or not args.headless
    manifest = {"status": "starting", "pins": pins, "os": platform.freedesktop_os_release()["PRETTY_NAME"],
                "network_interfaces": ["lo"], "dds_started": False,
                "duration_requested_seconds": args.seconds, "num_envs": args.num_envs,
                "headless": args.headless, "offscreen_camera": args.capture,
                "rendered": rendered, "physics_dt": args.physics_dt,
                "acceptance_duration": args.seconds >= 600}
    manifest_path = args.output / "run.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    app = AppLauncher(args).app
    stop = threading.Event()
    sampler = None
    try:
        import isaaclab.sim as sim_utils
        from isaaclab.assets import Articulation
        from isaaclab.utils.version import get_isaac_sim_version
        from pxr import UsdUtils
        import omni.usd

        version = str(get_isaac_sim_version())
        manifest["isaac_sim_version"] = version
        # API may return a semantic tuple or string; accept only 5.1.
        if not (version.startswith("5.1.") or version.startswith("(5, 1,")):
            raise RuntimeError("Isaac Sim 5.1 required")
        os.environ["PROJECT_ROOT"] = str(args.unitree_root.resolve())
        source = args.unitree_root / "robots" / "unitree.py"
        spec = importlib.util.spec_from_file_location("spike_unitree_cfg", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=args.physics_dt, device=args.device))
        sim.set_camera_view([2.5, -2.5, 2], [0, 0, .9])
        # Procedural placeholders avoid remote asset requests from the isolated run.
        ground = sim_utils.CuboidCfg(size=(5, 5, .1), collision_props=sim_utils.CollisionPropertiesCfg())
        ground.func("/World/Ground", ground, translation=(0, 0, -.05))
        light = sim_utils.DomeLightCfg(intensity=2000)
        light.func("/World/Light", light)
        for index in range(4):
            cube = sim_utils.CuboidCfg(size=(.12, .12, .12), collision_props=sim_utils.CollisionPropertiesCfg())
            cube.func(f"/World/Placeholder_{index}", cube, translation=(.5, .2 * index - .3, .7))
        robots = []
        for index in range(args.num_envs):
            cfg = module.G129_CFG_WITH_DEX3_BASE_FIX.copy()
            cfg.prim_path = f"/World/Robot_{index}"
            cfg.spawn.usd_path = str(asset.resolve())
            cfg.init_state.pos = (index * 2., 0., .75)
            robots.append(Articulation(cfg))
        camera = None
        if args.capture:
            from isaaclab.sensors import Camera, CameraCfg
            camera = Camera(CameraCfg(
                prim_path="/World/EvidenceCamera", update_period=0., height=720, width=1280,
                data_types=["rgb"], spawn=sim_utils.PinholeCameraCfg(
                    focal_length=24., horizontal_aperture=36., clipping_range=(.1, 100.))))
        sim.reset()
        if camera:
            import torch
            camera.set_world_poses_from_view(
                torch.tensor([[2.5, -2.5, 2.]], device=args.device),
                torch.tensor([[0., 0., .9]], device=args.device))
        for robot in robots:
            robot.update(args.physics_dt)
            if not robot.is_fixed_base:
                raise RuntimeError("Loaded articulation is not fixed base")
            robot.write_joint_state_to_sim(robot.data.default_joint_pos, robot.data.default_joint_vel)
            robot.reset()
        stage = omni.usd.get_context().get_stage()
        manifest["joint_counts"] = dump_inventory(robots[0], stage, args.output / "joint_inventory.csv")
        manifest["fixed_base"] = all(robot.is_fixed_base for robot in robots)
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(asset.resolve()))
        # USD's resolver does not resolve bare MDL module names. Kit's standard
        # MDL search root does; include that whole source tree to cover imports.
        mdl_root = Path("/isaac-sim/kit/mdl")
        builtin_mdl = {}
        for identifier in unresolved:
            if Path(identifier).name == identifier and identifier.endswith(".mdl"):
                candidates = list(mdl_root.rglob(identifier))
                if len(candidates) == 1:
                    builtin_mdl[identifier] = candidates[0]
        unresolved = [item for item in unresolved if item not in builtin_mdl]
        if unresolved:
            raise RuntimeError(f"Asset dependency closure contains unresolved references: {unresolved}")
        files = {Path(layer.realPath) for layer in layers if layer.realPath}
        files.update(Path(layer.realPath) for layer in stage.GetUsedLayers() if layer.realPath)
        files.update(builtin_mdl.get(path, Path(path)) for path in assets)
        if builtin_mdl:
            files.update(mdl_root.rglob("*.mdl"))
        manifest["builtin_mdl_dependencies"] = sorted(builtin_mdl)
        files.update((source, pins_path, Path(__file__), Path(__file__).with_name("evidence.py"), manifest_path))
        for imported in tuple(sys.modules.values()):
            filename = getattr(imported, "__file__", None)
            if filename and Path(filename).is_file() and any(
                Path(filename).resolve().is_relative_to(root.resolve())
                for root in (args.isaac_lab_root, args.unitree_root)):
                files.add(Path(filename))
        roots = {"assets": args.asset_root, "unitree": args.unitree_root,
                 "isaaclab": args.isaac_lab_root, "spike": Path(__file__).parent,
                 "run": args.output, "isaacsim": Path("/isaac-sim")}
        # Write final configuration before hashing it; completion metadata has its own file.
        manifest["status"] = "initialized"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        hash_files(files, args.output / "asset_hashes.csv", roots)
        sampler = threading.Thread(target=sample_resources, args=(args.output / "resources.csv", stop), daemon=True)
        sampler.start()
        started = time.monotonic()
        last_render, step_index = None, 0
        with (args.output / "steps.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["step", "host_monotonic_ns", "sim_time", "step_wall_ms", "render_interval_ms"])
            while app.is_running() and time.monotonic() - started < args.seconds:
                for robot in robots:
                    robot.set_joint_position_target(robot.data.default_joint_pos)
                    robot.write_data_to_sim()
                before = time.monotonic_ns()
                sim.step(render=rendered)
                after = time.monotonic_ns()
                for robot in robots:
                    robot.update(args.physics_dt)
                if camera:
                    camera.update(args.physics_dt)
                render_interval = "" if not rendered or last_render is None else (after - last_render) / 1e6
                if rendered:
                    last_render = after
                step_index += 1
                writer.writerow([step_index, after, step_index * args.physics_dt, (after - before) / 1e6, render_interval])
        elapsed = time.monotonic() - started
        completed = elapsed >= args.seconds
        if camera:
            from PIL import Image
            Image.fromarray(camera.data.output["rgb"][0, :, :, :3].cpu().numpy()).save(args.output / "robot.png")
        elif not args.headless:
            from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
            capture = capture_viewport_to_file(get_active_viewport(), str(args.output / "robot.png"))
            for _ in range(30):
                app.update()
        (args.output / "completion.json").write_text(json.dumps(
            {"completed": completed, "elapsed_seconds": elapsed, "steps": step_index,
             "screenshot_requires_visual_review": rendered}, indent=2) + "\n")
        if not completed:
            raise RuntimeError("Simulation ended before requested duration")
    except Exception as exc:
        traceback.print_exc()
        (args.output / "failure.json").write_text(json.dumps({"error_type": type(exc).__name__,
                                                             "completed": False}) + "\n")
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(timeout=5)
        app.close(wait_for_replicator=False)
    summarize(args.output)


if __name__ == "__main__":
    main()
