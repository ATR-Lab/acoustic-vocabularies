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


def export(robot, sim, path, dt):
    import torch
    result = {
        "source": "loaded_isaac_articulation",
        "position_units": "m", "joint_units": "rad",
        "coordinate_frame": "USD world, right-handed Z-up; root pose supplied for base-relative comparison",
        "quaternion_order": "wxyz",
        "joint_names": list(robot.joint_names), "body_names": list(robot.body_names), "poses": []}
    defaults = robot.data.default_joint_pos[0].tolist()
    limits = robot.data.joint_pos_limits[0].tolist()
    for name, vector in target_vectors(defaults, limits):
        target = torch.tensor([vector], device=robot.device, dtype=robot.data.joint_pos.dtype)
        robot.write_joint_state_to_sim(target, torch.zeros_like(target))
        robot.set_joint_position_target(target)
        for _ in range(3):
            robot.write_data_to_sim()
            sim.step(render=False)
            robot.update(dt)
        result["poses"].append({
            "id": name, "commanded_joint_positions": vector,
            "measured_joint_positions": robot.data.joint_pos[0].tolist(),
            "root_position": robot.data.root_pos_w[0].tolist(),
            "root_rotation_wxyz": robot.data.root_quat_w[0].tolist(),
            "body_positions": robot.data.body_pos_w[0].tolist(),
            "body_rotations_wxyz": robot.data.body_quat_w[0].tolist()})
    path.write_text(json.dumps(result, indent=2) + "\n")
    robot.write_joint_state_to_sim(robot.data.default_joint_pos, robot.data.default_joint_vel)
    robot.set_joint_position_target(robot.data.default_joint_pos)
    robot.write_data_to_sim()
    sim.step(render=False)
    robot.update(dt)
