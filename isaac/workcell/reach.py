"""Position-only engineering reach diagnostic using the loaded articulation.

This tests palm approach positions, not collision-free paths, grasps, force
control, finger clearance, or protocol ergonomics. No dynamics are integrated.
"""
import math


def check_reach(robot, sim, layout):
    import torch
    nominal=robot.data.default_joint_pos.clone()
    lo,hi=robot.data.joint_pos_limits[0,:,0],robot.data.joint_pos_limits[0,:,1]
    targets=[]
    for identifier,anchor in sorted(layout['anchors'].items()):
        if identifier.endswith('/home') or identifier.startswith(('tray_','quarantine_')) or identifier in ('supply_cup','return_cup') or identifier.endswith('/tag_free'):
            p=list(anchor['position_m']); p[2]+=.06
            targets.append((identifier,p))
    rows=[]
    for identifier,target in targets:
        side='left' if target[1]>=0 else 'right'
        names=[side+'_'+suffix+'_joint' for suffix in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
        indices=[robot.joint_names.index(name) for name in names]
        body=robot.body_names.index(side+'_hand_palm_link')
        target_tensor=torch.tensor(target,device=robot.device)
        best=None
        for restart in range(3):
            q=nominal.clone()
            if restart:
                generator=torch.Generator(device=robot.device).manual_seed(5200+restart)
                q[0,indices]=lo[indices]+torch.rand(len(indices),generator=generator,device=robot.device)*(hi[indices]-lo[indices])
            for iteration in range(140):
                robot.write_joint_state_to_sim(q,torch.zeros_like(q)); sim.forward()
                current=robot.root_physx_view.get_link_transforms()[0,body,:3]
                error=target_tensor-current; norm=float(torch.linalg.vector_norm(error))
                if best is None or norm<best[0]: best=(norm,q[0,indices].tolist(),current.tolist())
                if norm<.003: break
                jac=robot.root_physx_view.get_jacobians()[0,body-1,:3,indices]
                # Advanced indexing can transpose across tensor implementations.
                if jac.shape!=(3,len(indices)): jac=jac.T
                delta=jac.T@torch.linalg.solve(jac@jac.T+.004**2*torch.eye(3,device=robot.device),error)
                q[0,indices]=torch.clamp(q[0,indices]+delta.clamp(-.15,.15),lo[indices],hi[indices])
            if best[0]<.003: break
        rows.append(dict(anchor_id=identifier,hand=side,target_palm_position_m=target,
                         measured_palm_position_m=best[2],position_error_m=best[0],
                         within_3mm=best[0]<.003,joint_names=names,joint_positions_rad=best[1]))
    robot.write_joint_state_to_sim(nominal,torch.zeros_like(nominal)); sim.forward(); robot.update(sim.get_physics_dt())
    return dict(kind='loaded_43_joint_articulation_position_only_IK',approach_offset_m=[0.,0.,.06],
                torso_and_legs_at_default=True,physics_integrated=False,collision_checked=False,
                orientation_constrained=False,grasp_validated=False,threshold_m=.003,
                targets=len(rows),targets_within_threshold=sum(row['within_3mm'] for row in rows),results=rows)
