"""Bounded hypothetical orientation rejection screen; never a motion plan.

Uses NumPy already supplied by the approved Isaac environment. A surviving
candidate would still require triangle, table/other-link, IK and actual-motion
checks. Positive cup intersections suffice to reject the candidates here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .geometry import compose, pose
from .grip_geometry import read_vertices, HAND_MESHES
from .recording import write_json


def search(summary, layout, mesh_directory, *, tilted):
    import numpy as np
    cup = next(row for row in layout['objects'] if row['id'] == 'supply_cup')
    if cup['rotation_xyzw'] != [0., 0., 0., 1.] or cup['kind'] != 'cup':
        raise ValueError('This diagnostic requires the frozen axis-aligned cup')
    x, y, z = cup['dimensions_m']
    w = .004  # frozen build_usd.shell geometry
    boxes = [('Bottom', (0, 0, -z/2+w/2), (x, y, w)),
             ('Back', (-x/2+w/2, 0, 0), (w, y, z)),
             ('Front', (x/2-w/2, 0, 0), (w, y, z)),
             ('Left', (0, -y/2+w/2, 0), (x, w, z)),
             ('Right', (0, y/2-w/2, 0), (x, w, z))]
    meshes = {name[:-4]: read_vertices(Path(mesh_directory)/name, digest)
              for name, digest in HAND_MESHES.items()}
    selected = {row['sample']: row for row in summary['rows'] if row['sample'] in (60, 72, 191, 209)}
    if set(selected) != {60, 72, 191, 209}:
        raise ValueError('All four captured cup-level hand poses are required')
    samples = []
    for sample, row in sorted(selected.items()):
        center = np.array(row['washer']['position_m'])
        washer_rotation = row['washer']['rotation_xyzw']
        if len(washer_rotation) != 4 or any(not math.isfinite(v) for v in washer_rotation) or sum(v*v for v in washer_rotation[:3]) > 1e-10 or abs(abs(washer_rotation[3])-1.) > 1e-5:
            raise ValueError('This diagnostic requires the frozen horizontal washer')
        points = []
        for link, vertices in meshes.items():
            frame = pose(row['frames'][link])
            if abs(sum(v*v for v in frame[1])-1) > 1e-5:
                raise ValueError('Captured link rotation must be a unit quaternion')
            world = np.array([compose(frame, (v, [0., 0., 0., 1.]))[0] for v in vertices])-center
            if not np.isfinite(world).all():
                raise ValueError('Nonfinite captured geometry')
            points.append((link, world))
        samples.append((sample, center, points))
    closed = selected[72]
    contact = np.array([row['world_position_m'] for row in closed['contacts']])-np.array(closed['washer']['position_m'])
    if contact.shape != (2, 3) or not np.isfinite(contact).all():
        raise ValueError('Two finite measured contact vertices are required')

    def rotation(axis, angle):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]]) if axis == 0 else np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]) if axis == 1 else np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    rows = []
    for pitch in (-30, -15, 0, 15, 30) if tilted else (0,):
        for roll in (-30, -15, 0, 15, 30) if tilted else (0,):
            for yaw in range(-180, 180, 15 if tilted else 2):
                r = rotation(2, yaw)@rotation(1, pitch)@rotation(0, roll)
                cp = contact@r.T
                radial, axial = np.linalg.norm(cp[:, :2], axis=1)-.0125, np.abs(cp[:, 2])-.003
                distance = np.minimum(np.maximum(radial, axial), 0)+np.linalg.norm(np.maximum(np.c_[radial, axial], 0), axis=1)
                record = dict(yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll, selected_contact_distance_m=distance.tolist())
                if tilted and (np.any(distance < -.0001) or np.any(distance > .00075)):
                    rows.append(dict(record, rejection='selected_contact_distance'))
                    continue
                failure = None
                for sample, center, points in samples:
                    for link, vertices in points:
                        p = vertices@r.T+center-np.array(cup['position_m'])
                        for wall, wall_center, dimensions in boxes:
                            depths = np.min(np.array(dimensions)/2-np.abs(p-np.array(wall_center)), axis=1)
                            if float(depths.max()) > .0001:
                                failure = dict(sample=sample, link=link, wall=wall, max_vertex_depth_m=float(depths.max()))
                                break
                        if failure:
                            break
                    if failure:
                        break
                rows.append(dict(record, rejection='cup_vertex' if failure else None, first_intersection=failure))
    return dict(kind='hypothetical_rigid_orientation_of_captured_geometry_not_IK_or_trajectory',
        mesh_sha256=HAND_MESHES, candidate_count=len(rows),
        contact_rejected=sum(row['rejection'] == 'selected_contact_distance' for row in rows),
        cup_rejected=sum(row['rejection'] == 'cup_vertex' for row in rows),
        survivors=[row for row in rows if row['rejection'] is None], rows=rows,
        collision_clearance_proven=False, triangle_table_other_link_checks_complete=False,
        contact_distance_range_m=[-.0001, .00075], cup_vertex_threshold_m=.0001)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('summary', 'layout', 'mesh-directory', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--summary-sha256', required=True)
    parser.add_argument('--tilted', action='store_true')
    args = parser.parse_args()
    raw, layout = args.summary.read_bytes(), args.layout.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.summary_sha256:
        raise ValueError('Captured evidence hash mismatch')
    result = search(json.loads(raw), json.loads(layout), args.mesh_directory, tilted=args.tilted)
    result.update(summary_sha256=args.summary_sha256, layout_sha256=hashlib.sha256(layout).hexdigest())
    write_json(args.out, result)
    print(json.dumps({key: result[key] for key in ('candidate_count', 'contact_rejected', 'cup_rejected', 'survivors')}))


if __name__ == '__main__':
    main()
