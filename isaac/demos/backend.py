"""Actual PhysX readback and bounded arm IK; no simulator import at module load."""
import math
from copy import deepcopy

from .geometry import mul, inverse, norm, angle


class IsaacMotionBackend:
    def __init__(self, adapter, neutral_robot):
        self.adapter, self.robot, self.sim = adapter, adapter.robot, adapter.sim
        self.neutral = deepcopy(neutral_robot)
        self.names = list(self.robot.joint_names)
        self.arms, self.hands = {}, {}
        for side in ('left', 'right'):
            self.arms[side] = [self.names.index(side+'_'+suffix+'_joint') for suffix in
                ('shoulder_pitch', 'shoulder_roll', 'shoulder_yaw', 'elbow', 'wrist_roll', 'wrist_pitch', 'wrist_yaw')]
            self.hands[side] = [i for i, name in enumerate(self.names) if name.startswith(side+'_hand_')]
            if len(self.hands[side]) != 7:
                raise ValueError('Both observed seven-joint Dex3 hands are required')
        self.low = self.robot.data.joint_pos_limits[0, :, 0].tolist()
        self.high = self.robot.data.joint_pos_limits[0, :, 1].tolist()
        self.ik_results = []

    def write(self, positions):
        from isaac.commands.hold import make_robot_hold
        if len(positions) != 43 or any(not math.isfinite(v) or v < a-1e-6 or v > b+1e-6
                                       for v, a, b in zip(positions, self.low, self.high)):
            raise ValueError('Motion outside finite canonical joint limits')
        state = {**self.neutral, 'joint_positions_rad': list(positions)}
        make_robot_hold(self.adapter)(state)

    def positions(self):
        return self.robot.root_physx_view.get_dof_positions()[0].tolist()

    def palm(self, side):
        body = self.robot.body_names.index(side+'_hand_palm_link')
        value = self.robot.root_physx_view.get_link_transforms()[0, body].tolist()
        return value[:3], value[3:7]

    def fingers(self, positions, side, closure):
        result = list(positions)
        for index in self.hands[side]:
            start = self.neutral['joint_positions_rad'][index]
            a, b = self.low[index], self.high[index]
            # An explicitly provisional flexion display, not a contact solution.
            end = start + .30*((b-start) if abs(b-start) >= abs(a-start) else (a-start))
            result[index] = start + closure*(end-start)
        return result

    def solve(self, seed, side, target, quaternion=None, *, label=''):
        import torch
        import random
        indices = self.arms[side]
        body = self.robot.body_names.index(side+'_hand_palm_link')
        lo = torch.tensor(self.low, device=self.robot.device)
        hi = torch.tensor(self.high, device=self.robot.device)
        seeds = [('previous_keyframe', list(seed))]
        neutral_seed = list(seed)
        for i in indices:
            neutral_seed[i] = self.neutral['joint_positions_rad'][i]
        seeds.append(('neutral_arm', neutral_seed))
        # Public engineering search seeds, independent of any study randomization.
        rng = random.Random('issue56-IK-'+label)
        for number in range(2):
            retry = list(seed)
            for i in indices:
                retry[i] = rng.uniform(self.low[i], self.high[i])
            seeds.append(('engineering_restart_'+str(number), retry))
        best, attempts = None, []
        for seed_kind, attempt_seed in seeds:
            q = torch.tensor([attempt_seed], device=self.robot.device, dtype=self.robot.data.joint_pos.dtype)
            local = None
            for iteration in range(200):
                self.write(q[0].tolist())
                actual = self.palm(side)
                p_error = [a-b for a, b in zip(target, actual[0])]
                r_error = [0., 0., 0.]
                if quaternion is not None:
                    delta = mul(quaternion, inverse(actual[1]))
                    if delta[3] < 0:
                        delta = [-x for x in delta]
                    magnitude = norm(delta[:3])
                    if magnitude > 1e-10:
                        scale = 2*math.atan2(magnitude, delta[3])/magnitude
                        r_error = [x*scale for x in delta[:3]]
                position_error, rotation_error = norm(p_error), norm(r_error)
                quality = position_error + .15*rotation_error
                if local is None or quality < local[0]:
                    local = (quality, q[0].tolist(), actual, position_error, rotation_error)
                if position_error < .0005 and rotation_error < .008:
                    break
                jac = self.robot.root_physx_view.get_jacobians()[0, body-1, :, indices]
                if jac.shape != (6, len(indices)):
                    jac = jac.T
                if quaternion is None:
                    jac = jac[:3]
                    error = torch.tensor(p_error, device=self.robot.device)
                else:
                    jac = jac.clone()
                    jac[3:] *= .15
                    error = torch.tensor(p_error+[v*.15 for v in r_error], device=self.robot.device)
                damped = jac@jac.T + .002**2*torch.eye(jac.shape[0], device=self.robot.device)
                step = jac.T@torch.linalg.solve(damped, error)
                q[0, indices] = torch.clamp(q[0, indices]+step.clamp(-.08, .08), lo[indices], hi[indices])
            attempts.append(dict(seed_kind=seed_kind, seed_joint_positions_rad=attempt_seed,
                iterations=iteration+1, best_joint_positions_rad=local[1], measured_palm_pose=local[2],
                position_error_m=local[3], orientation_error_rad=local[4]))
            if best is None or local[0] < best[0]:
                best = local
            if local[3] < .0005 and local[4] < .008:
                best = local
                break
        self.write(best[1])
        self.ik_results.append(dict(label=label, side=side, target_position_m=list(target),
            target_orientation_xyzw=quaternion, joint_names=self.names, attempts=attempts,
            best_joint_positions_rad=best[1], measured_palm_pose=best[2],
            position_error_m=best[3], orientation_error_rad=best[4],
            max_joint_change_from_previous_rad=max(abs(a-b) for a, b in zip(seed, best[1])),
            orientation_constrained=quaternion is not None))
        if best[3] >= .0005 or best[4] >= .008:
            raise ValueError(f'Bounded IK search failed: {label}, position={best[3]:.6g}, orientation={best[4]:.6g}')
        return best[1], best[2]
