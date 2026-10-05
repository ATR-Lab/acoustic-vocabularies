"""Robot-only hold after advancing physics, before public sample acquisition."""


def make_robot_hold(adapter):
    def hold(value):
        import torch
        robot = adapter.robot
        if value["joint_names"] != list(robot.joint_names):
            raise ValueError("hold canonical joint order mismatch")
        make = lambda values: torch.tensor([values], device=robot.device, dtype=robot.data.joint_pos.dtype)
        q = value["root_rotation_xyzw"]
        root = value["root_position_m"] + [q[3], *q[:3]] + [0.]*6
        robot.write_root_state_to_sim(make(root))
        positions = make(value["joint_positions_rad"])
        zero = torch.zeros_like(positions)
        robot.write_joint_state_to_sim(positions, zero)
        robot.set_joint_position_target(positions)
        robot.set_joint_velocity_target(zero)
        robot.set_joint_effort_target(zero)
        robot.write_data_to_sim()
        adapter.step_fixed(1)
    return hold
