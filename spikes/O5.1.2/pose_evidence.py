"""Export actual articulation transforms for independent Unity FK comparisons."""
import json
import math


def target_vectors(defaults, limits):
    yield "default", list(defaults)
    for joint, (lower, upper) in enumerate(limits):
        for fraction in (.25, .75):
            target = list(defaults)
            target[joint] = lower + fraction * (upper - lower)
            yield f"joint_{joint:02d}_{int(fraction * 100)}pct", target
    # Deterministic broad engineering coverage; not study stimuli or allocations.
    for pose in range(5):
        yield f"spread_{pose}", [
            lower + (.15 + .7 * (((joint + 1) * (pose + 1) * math.sqrt(2)) % 1)) * (upper - lower)
            for joint, (lower, upper) in enumerate(limits)]


def export(robot, sim, path, dt, capture_pose=None):
    import torch
    result = {
        "source": "loaded_isaac_articulation",
        "position_units": "m", "joint_units": "rad",
        "coordinate_frame": "usd_world_rh_z_up",
        "quaternion_order": "wxyz",
        "coverage": "Articulation rigid bodies; fixed or merged URDF links may be absent and require separate comparison reporting",
        "joint_names": list(robot.joint_names), "body_names": list(robot.body_names), "poses": []}
    defaults = robot.data.default_joint_pos[0].tolist()
    limits = robot.data.joint_pos_limits[0].tolist()
    capture_names = {"default", "joint_20_75pct", "spread_4"}
    for name, vector in target_vectors(defaults, limits):
        target = torch.tensor([vector], device=robot.device, dtype=robot.data.joint_pos.dtype)
        robot.write_joint_state_to_sim(target, torch.zeros_like(target))
        robot.set_joint_position_target(target)
        for _ in range(3):
            robot.write_data_to_sim()
            sim.step(render=False)
            robot.update(dt)
        result["poses"].append({
            "name": name, "commanded_joint_positions": vector,
            "joint_names": list(robot.joint_names),
            "joint_positions": robot.data.joint_pos[0].tolist(),
            "root_position": robot.data.root_pos_w[0].tolist(),
            "root_quaternion_wxyz": robot.data.root_quat_w[0].tolist(),
            "links": [{"name": body, "position": position, "quaternion_wxyz": rotation}
                      for body, position, rotation in zip(
                          robot.body_names, robot.data.body_pos_w[0].tolist(),
                          robot.data.body_quat_w[0].tolist())]})
        if capture_pose and name in capture_names:
            capture_pose(name)
    path.write_text(json.dumps(result, indent=2) + "\n")
    robot.write_joint_state_to_sim(robot.data.default_joint_pos, robot.data.default_joint_vel)
    robot.set_joint_position_target(robot.data.default_joint_pos)
    robot.write_data_to_sim()
    sim.step(render=False)
    robot.update(dt)
