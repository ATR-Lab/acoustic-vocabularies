"""Isaac Lab 2.3 articulation + #52 workcell adapter; simulator imports are lazy.

Readback comes from PhysX tensor views, not command/target buffers. Kinematic
forward frames update transforms without introducing gravity-driven settling.
"""
from copy import deepcopy

from .snapshot import validate_state


class IsaacResetAdapter:
    def __init__(self, robot, accessors, sim, scene_sha256):
        self.robot, self.accessors, self.sim = robot, accessors, sim
        self.scene_sha256 = scene_sha256
        if len(robot.joint_names) != 43:
            raise ValueError("reset requires the observed 43-joint G1 articulation")
        required_frames = {"head_link", "left_hand_palm_link", "right_hand_palm_link"}
        if not required_frames.issubset(robot.body_names):
            raise ValueError("head and both hand frames must be independently observable")
        self._object_neutral = deepcopy(accessors.read_state())

    @property
    def sim_time(self):
        return float(self.sim.current_time)

    def read_state(self):
        view = self.robot.root_physx_view
        root = view.get_root_transforms()[0].tolist()  # PhysX is xyzw.
        velocity = view.get_root_velocities()[0].tolist()
        links = view.get_link_transforms()[0].tolist()
        return {"robot": {
            "joint_names": list(self.robot.joint_names),
            "joint_positions_rad": view.get_dof_positions()[0].tolist(),
            "joint_velocities_rad_s": view.get_dof_velocities()[0].tolist(),
            "root_position_m": root[:3], "root_rotation_xyzw": root[3:7],
            "root_linear_velocity_m_s": velocity[:3], "root_angular_velocity_rad_s": velocity[3:6]},
            "objects": self.accessors.read_state(), "environment": self.accessors.read_environment(),
            "frames": {name: {"position_m": row[:3], "rotation_xyzw": row[3:7]}
                       for name, row in zip(self.robot.body_names, links)}}

    def write_state(self, state):
        import torch
        validate_state(state)
        value = state["robot"]
        if value["joint_names"] != list(self.robot.joint_names):
            raise ValueError("canonical joint order mismatch")
        # Validate immutable scene appearance before any articulation writes.
        # #52 may either restore authored appearance or reject a mismatch.
        self.accessors.apply_environment(state["environment"])
        self.accessors.apply_state(state["objects"])
        make = lambda values: torch.tensor([values], device=self.robot.device, dtype=self.robot.data.joint_pos.dtype)
        xyzw = value["root_rotation_xyzw"]
        root = value["root_position_m"] + [xyzw[3], *xyzw[:3]] + value["root_linear_velocity_m_s"] + value["root_angular_velocity_rad_s"]
        self.robot.write_root_state_to_sim(make(root))  # Isaac Lab 2.3 is wxyz.
        positions = make(value["joint_positions_rad"])
        velocities = make(value["joint_velocities_rad_s"])
        self.robot.write_joint_state_to_sim(positions, velocities)
        self.robot.set_joint_position_target(positions)
        self.robot.set_joint_velocity_target(velocities)
        self.robot.set_joint_effort_target(torch.zeros_like(positions))
        self.robot.write_data_to_sim()

    def step_fixed(self, count):
        for _ in range(count):
            self.sim.forward()
            # Invalidates Isaac Lab data caches; does not advance physics time.
            self.robot.update(self.sim.get_physics_dt())

    def prepare_neutral(self):
        """Explicit initial neutral preparation before capture, never during load."""
        import torch
        self.accessors.apply_state(deepcopy(self._object_neutral))
        self.robot.write_root_state_to_sim(self.robot.data.default_root_state)
        self.robot.write_joint_state_to_sim(self.robot.data.default_joint_pos, torch.zeros_like(self.robot.data.default_joint_vel))
        self.robot.set_joint_position_target(self.robot.data.default_joint_pos)
        self.robot.set_joint_velocity_target(torch.zeros_like(self.robot.data.default_joint_vel))
        self.robot.set_joint_effort_target(torch.zeros_like(self.robot.data.default_joint_vel))
        self.robot.write_data_to_sim()
        self.step_fixed(1)
