"""Standard-library cup-shell geometry for the supply-cup grasp plan and screens.

The cup is the frozen #52 ``build_usd.shell``: five axis-aligned 4 mm boxes
(Bottom/Back/Front/Left/Right). Two uses share that model:

* The planned ADD_ONE pickup (``planner``) enters the supply cup with one
  straight middle finger pointing down, while every other hand link stays above
  the rim. A declared envelope (a capsule around the distal finger column, a
  half-space bounding the rest of the hand, and a reach sphere outside the
  corridor) must keep at least ``CLEARANCE_MARGIN_M`` from every wall box.
  The envelope is an engineering declaration from the pinned URDF chain; it is
  not the STL mesh and does not prove whole-robot collision freedom.
* The read-only CLI screens actual PhysX link frames with pinned STL vertices.
  Positive intersections prove a geometric failure. Vertex clearance above the
  margin does not prove triangle or whole-robot collision freedom.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from .geometry import add, axis_angle, compose, mul, pose, relative, sub
from .grip_probe import CONTACT_POINTS, MESH_SHA256
from .recording import write_json


SHELL_WALL_M = .004  # Exact shell wall thickness in the frozen #52 build_usd.shell.
HAND_MESHES = {
    'right_hand_index_0_link.STL': '12ab4300c95e437e834f9aef772b3b431c671bf34338930b52ad11aef73bbd0d',
    'right_hand_index_1_link.STL': 'c9c34efce4563cacdcfd29fc838a982976d40f5442c71219811dcbbf3923a33d',
    'right_hand_middle_0_link.STL': '12ab4300c95e437e834f9aef772b3b431c671bf34338930b52ad11aef73bbd0d',
    'right_hand_middle_1_link.STL': 'c9c34efce4563cacdcfd29fc838a982976d40f5442c71219811dcbbf3923a33d',
    'right_hand_palm_link.STL': '86c0b231cc44477d64a6493e5a427ba16617a00738112dd187c652675b086fb9',
    'right_hand_thumb_0_link.STL': '544298d0ea1088f5b276a10cc6a6a9e533efdd91594955fdc956c46211d07f83',
    'right_hand_thumb_1_link.STL': '0a9a820da8dd10f298778b714f1364216e8a5976f4fd3a05689ea26327d44bf6',
    'right_hand_thumb_2_link.STL': '3f1bfb37668e8f61801c8d25f171fa1949e08666be86c67acad7e0079937cc45',
}

# Declared supply-cup grasp envelope (engineering proposal, native mesh check pending).
CLEARANCE_MARGIN_M = .005          # Required hand clearance from every cup wall box.
CARRIED_MIN_CLEARANCE_M = .001     # Lifted washer; the frozen layout leaves 2.5 mm.
FINGER_RADIUS_M = .012             # Capsule radius around the straight middle finger.
BODY_LIMIT_PALM_X_M = .095         # All other hand geometry has palm-x <= this value.
HAND_REACH_M = .19                 # Every hand point lies within this of the palm origin
                                   # (straight middle tip 0.173 m + 12 mm capsule, rounded up).
APPROACH_ABOVE_RIM_M = .12         # Fingertip height above the rim at corridor entry/exit.
DESCENT_STEP_M = .01               # Maximum vertical spacing of corridor keyframes.
# Pinned g1_29dof_with_hand_rev_1_0.urdf: middle_0 origin (0.0777,-0.0016,-0.0285),
# middle_1 origin +0.0458 along x; distal extent from the pinned middle_1 pad
# vertex. With both middle joints at 0 the finger axis is palm +x.
FINGER_AXIS_YZ = (-.0016, -.0285)
TIP_LOCAL = (.0777+.0458+CONTACT_POINTS['right_hand_middle_1_link'][0], *FINGER_AXIS_YZ)
# Straight middle finger, curled index, thumb along palm +y (all within URDF limits).
PAD_POSTURE = {'thumb_0': 0., 'thumb_1': 0., 'thumb_2': 0., 'middle_0': 0., 'middle_1': 0.,
               'index_0': 1.55, 'index_1': 1.50}


def box_vertex_depth(point, center, dimensions):
    if any(not math.isfinite(x) for x in (*point, *center, *dimensions)) or any(x <= 0 for x in dimensions):
        raise ValueError('Finite positive box geometry required')
    return max(0., min(size/2-abs(x-c) for x, c, size in zip(point, center, dimensions)))


def point_box_signed_distance(point, center, dimensions):
    """Exact signed distance to an axis-aligned box; negative inside."""
    if any(not math.isfinite(x) for x in (*point, *center, *dimensions)) or any(x <= 0 for x in dimensions):
        raise ValueError('Finite positive box geometry required')
    d = [abs(x-c)-size/2 for x, c, size in zip(point, center, dimensions)]
    return math.sqrt(sum(max(v, 0.)**2 for v in d))+min(max(d), 0.)


def segment_box_distance(a, b, center, dimensions):
    """Minimum signed distance from segment ab to a box.

    The signed distance of a convex set is convex, so a golden-section search
    along the segment converges to the exact minimum (to ~1e-12 m).
    """
    f = lambda t: point_box_signed_distance([x+(y-x)*t for x, y in zip(a, b)], center, dimensions)
    lo, hi = 0., 1.
    ratio = (math.sqrt(5)-1)/2
    c, d = hi-ratio*(hi-lo), lo+ratio*(hi-lo)
    fc, fd = f(c), f(d)
    for _ in range(80):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi-ratio*(hi-lo)
            fc = f(c)
        else:
            lo, c, fc = c, d, fd
            d = lo+ratio*(hi-lo)
            fd = f(d)
    return min(f(0.), f(1.), fc, fd)


def cup_wall_boxes(layout, cup_id='supply_cup'):
    """World-frame (name, center, dimensions) of the frozen axis-aligned shell."""
    cup = next(item for item in layout['objects'] if item['id'] == cup_id)
    x, y, z = cup['dimensions_m']
    w = SHELL_WALL_M
    if cup['kind'] != 'cup' or min(x, y, z) <= w*2:
        raise ValueError('Unsupported cup geometry')
    if cup['rotation_xyzw'] != [0., 0., 0., 1.]:
        raise ValueError('This model requires the frozen axis-aligned cup')
    local = [('Bottom', (0, 0, -z/2+w/2), (x, y, w)),
             ('Back', (-x/2+w/2, 0, 0), (w, y, z)),
             ('Front', (x/2-w/2, 0, 0), (w, y, z)),
             ('Left', (0, -y/2+w/2, 0), (x, w, z)),
             ('Right', (0, y/2-w/2, 0), (x, w, z))]
    return [(name, add(cup['position_m'], center), list(size)) for name, center, size in local]


def fingers_down_orientation(palm_yaw):
    """Palm +x (the straight finger) points to world -Z; yaw is about world Z."""
    return mul(axis_angle([0, 0, 1], palm_yaw), axis_angle([0, 1, 0], math.pi/2))


def pad_posture(names, positions, side):
    if side != 'right':
        raise ValueError('The supply-cup pad posture is defined for the right Dex3 hand only')
    result = list(positions)
    for suffix, value in PAD_POSTURE.items():
        result[names.index(side+'_hand_'+suffix+'_joint')] = value
    return result


def washer_rim_vertices(definition):
    """Outer-ring vertices of the 32-segment build_usd washer, in its own frame."""
    r, h = definition['dimensions_m'][0]/2, definition['dimensions_m'][2]/2
    return [(r*math.cos(i*2*math.pi/32), r*math.sin(i*2*math.pi/32), z) for z in (-h, h) for i in range(32)]


def supply_cup_corridor(layout, neutral, washer_id, palm_yaw, *, cup_id='supply_cup',
                        margin=CLEARANCE_MARGIN_M):
    """Vertical fingertip corridor to the top face of ``washer_id``.

    The contact point lies on the washer ring at its mid radius, displaced
    toward the cup axis so the finger capsule is as far from the walls as the
    ring allows. Returns world tip heights (entry first) and the declared model.
    """
    definitions = {item['id']: item for item in layout['objects']}
    washer, cup = definitions[washer_id], definitions[cup_id]
    if washer['kind'] != 'washer' or neutral[washer_id]['state'].get('location') != cup_id:
        raise ValueError('Supply-cup corridor requires a washer inside the cup')
    if washer['dimensions_m'] != [.025, .025, .006]:
        raise ValueError('Supply-cup corridor is defined for the 25x25x6mm washer only')
    center = neutral[washer_id]['position_m']
    direction = sub(cup['position_m'][:2], center[:2])
    size = math.hypot(*direction)
    direction = [-1., 0.] if size < 1e-9 else [v/size for v in direction]
    ring = .75*washer['dimensions_m'][0]/2  # Mid radius between inner r/2 and outer r.
    top = center[2]+washer['dimensions_m'][2]/2
    pick = [center[0]+direction[0]*ring, center[1]+direction[1]*ring, top]
    rim = cup['position_m'][2]+cup['dimensions_m'][2]/2
    entry = rim+APPROACH_ABOVE_RIM_M
    count = max(1, math.ceil((entry-top)/DESCENT_STEP_M-1e-9))
    heights = [entry-(entry-top)*k/count for k in range(count+1)]
    # Other washers in the cup are static obstacles (bounding boxes of the
    # level discs); the finger may touch only the selected washer's top face.
    neighbors = [(key, list(value['position_m']), list(definitions[key]['dimensions_m']))
                 for key, value in sorted(neutral.items()) if key != washer_id
                 and definitions[key]['kind'] == 'washer' and value['state'].get('location') == cup_id]
    if any(abs(sum(v*v for v in neutral[key]['rotation_xyzw'][:2])) > 1e-12 for key, _, _ in neighbors):
        raise ValueError('Neighbor washer bounding boxes require level washers')
    return dict(cup=cup_id, walls=cup_wall_boxes(layout, cup_id), neighbors=neighbors,
                rim_top_m=rim, pick_m=pick,
                tip_heights_m=heights, palm_yaw_rad=palm_yaw,
                orientation_xyzw=fingers_down_orientation(palm_yaw), tip_local_m=list(TIP_LOCAL),
                finger_radius_m=FINGER_RADIUS_M, body_limit_palm_x_m=BODY_LIMIT_PALM_X_M,
                hand_reach_m=HAND_REACH_M, margin_m=margin, carried_min_clearance_m=CARRIED_MIN_CLEARANCE_M,
                washer_vertices_local_m=washer_rim_vertices(washer),
                model='declared_capsule_halfspace_reach_envelope_not_mesh')


def envelope_clearance(corridor, palm_pose, carried_pose=None):
    """Declared-envelope clearances (m) from every cup wall for one palm pose."""
    r = corridor['finger_radius_m']
    tip = corridor['tip_local_m']
    a = compose(palm_pose, ([tip[0]-r, tip[1], tip[2]], [0., 0., 0., 1.]))[0]
    b = compose(palm_pose, ([corridor['body_limit_palm_x_m'], tip[1], tip[2]], [0., 0., 0., 1.]))[0]
    finger = body = carried = math.inf
    limiting = None
    for name, center, size in corridor['walls']:
        value = segment_box_distance(a, b, center, size)-r
        if value < finger:
            finger, limiting = value, name
        # Separation of the hand half-space {palm x <= limit} from the wall box:
        # the smallest palm-frame x of any box corner, minus the limit.
        corners = [[c+s/2*sign for c, s, sign in zip(center, size, signs)]
                   for signs in [(sx, sy, sz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]]
        local_x = [relative(palm_pose, (corner, [0., 0., 0., 1.]))[0][0] for corner in corners]
        body = min(body, min(local_x)-corridor['body_limit_palm_x_m'])
        if carried_pose is not None:
            for vertex in corridor['washer_vertices_local_m']:
                point = compose(carried_pose, (vertex, [0., 0., 0., 1.]))[0]
                carried = min(carried, point_box_signed_distance(point, center, size))
    neighbors = min((segment_box_distance(a, b, center, size)-r for _, center, size in corridor['neighbors']),
                    default=math.inf)
    return dict(finger_m=finger, body_m=body, carried_m=None if carried_pose is None else carried,
                neighbors_m=neighbors, limiting_wall=limiting)


def reach_clearance(corridor, palm_pose):
    """Clearance of the whole-hand reach sphere around the palm origin."""
    return min(point_box_signed_distance(palm_pose[0], center, size)
               for _, center, size in corridor['walls'])-corridor['hand_reach_m']


def path_clearance(corridor, samples, *, strict_reach=True, stop_on_violation=False):
    """Check every sample: declared envelope while the pad posture is held
    (corridor entry through release), reach sphere otherwise.

    ``samples`` rows hold ``u``, ``palm`` (position, xyzw) and optional
    ``carried`` washer pose. Returns minima and every violation; never raises.
    With ``strict_reach=False`` an outside-corridor sample whose reach sphere
    meets the cup is listed as ``uncertified`` (the pinned-mesh screen decides)
    instead of as a violation.
    """
    margin = corridor['margin_m']
    minima = dict(finger_m=math.inf, body_m=math.inf, carried_m=math.inf, neighbors_m=math.inf, reach_m=math.inf)
    violations, uncertified = [], []
    for row in samples:
        palm = (list(row['palm'][0]), list(row['palm'][1]))
        if corridor['enter'] <= row['u'] <= corridor.get('pad_until', corridor['exit']):
            value = envelope_clearance(corridor, palm, row.get('carried'))
            checks = [('finger_m', value['finger_m'], margin), ('body_m', value['body_m'], margin),
                      ('neighbors_m', value['neighbors_m'], 0.)]
            if value['carried_m'] is not None:
                checks.append(('carried_m', value['carried_m'], corridor['carried_min_clearance_m']))
        else:
            checks = [('reach_m', reach_clearance(corridor, palm), margin)]
        for key, value, required in checks:
            minima[key] = min(minima[key], value)
            if value < required:
                item = dict(sample=row.get('sample'), u=row['u'], check=key,
                            clearance_m=value, required_m=required)
                (uncertified if key == 'reach_m' and not strict_reach else violations).append(item)
        if violations and stop_on_violation:
            break
    return dict(model=corridor['model'], margin_m=margin, samples=len(samples),
                minima={k: (None if v == math.inf else v) for k, v in minima.items()},
                violations=violations, uncertified=uncertified, ok=not violations)


def require_planned_clearance(corridor, knots, grip):
    """Planner refusal: corridor knots, straight segments between them, entry/exit reach.

    ``knots`` are (u, measured palm pose) inside the corridor, in time order.
    Between knots the check follows the straight Cartesian segment at <= 1 mm
    spacing. The Isaac runtime interpolates joints, so the actual swept path is
    screened separately from measured frames (``collect_cup_frames``).
    """
    samples = []
    for (u0, p0), (u1, p1) in zip(knots, knots[1:]):
        count = max(1, math.ceil(math.dist(p0[0], p1[0])/.001))
        for k in range(count+(1 if u1 == knots[-1][0] else 0)):
            t = k/count
            q = [a+(b-a)*t for a, b in zip(p0[1], p1[1] if sum(x*y for x, y in zip(p0[1], p1[1])) >= 0 else [-v for v in p1[1]])]
            size = math.sqrt(sum(v*v for v in q))
            palm = ([a+(b-a)*t for a, b in zip(p0[0], p1[0])], [v/size for v in q])
            u = u0+(u1-u0)*t
            samples.append(dict(u=u, palm=palm, carried=compose(palm, grip) if u >= corridor['attach'] else None))
    # Keyframes first, so an infeasible variant is refused without the dense pass.
    report = path_clearance(corridor, [dict(u=u, palm=palm, carried=compose(palm, grip) if u >= corridor['attach'] else None)
                                       for u, palm in knots], stop_on_violation=True)
    if report['ok']:
        report = path_clearance(corridor, samples, stop_on_violation=True)
    for u, palm in (knots[0], knots[-1]):
        value = reach_clearance(corridor, palm)
        if value < corridor['margin_m']:
            report['violations'].append(dict(u=u, check='entry_exit_reach_m', clearance_m=value,
                                             required_m=corridor['margin_m']))
    if report['violations']:
        first = report['violations'][0]
        raise ValueError('Supply-cup clearance below declared margin: %s=%.6f m at u=%.4f (required %.4f m)'
                         % (first['check'], first['clearance_m'], first['u'], first['required_m']))
    report['ok'] = True
    return report


def read_vertices(path, expected_hash):
    raw = Path(path).read_bytes()
    if len(raw) < 84 or len(raw) > 8*1024*1024 or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Pinned public distal mesh hash mismatch')
    count = struct.unpack_from('<I', raw, 80)[0]
    if len(raw) != 84+50*count:
        raise ValueError('Malformed binary STL')
    points = set()
    for index in range(count):
        values = struct.unpack_from('<12fH', raw, 84+50*index)
        for start in (3, 6, 9):
            point = values[start:start+3]
            if any(not math.isfinite(x) for x in point):
                raise ValueError('Nonfinite mesh vertex')
            points.add(point)
    return sorted(points)


def screen(summary, layout, mesh_directory, *, cup_id='supply_cup', meshes=None, margin=None):
    """Actual link frames x pinned STL vertices vs the cup shell.

    ``margin`` additionally reports every link/wall whose minimum vertex
    clearance is below it (vertex sampling, not triangle distance).
    """
    boxes = cup_wall_boxes(layout, cup_id)  # World frame; the frozen cup is unrotated.
    selected = MESH_SHA256 if meshes is None else meshes
    loaded = {name.removesuffix('.STL'): read_vertices(Path(mesh_directory)/name, digest)
              for name, digest in selected.items()}
    rows = []
    for row in summary['rows']:
        hits, clearances = [], []
        for link, points in loaded.items():
            frame = row['frames'][link]
            rotation = frame['rotation_xyzw']
            if len(rotation) != 4 or abs(sum(v*v for v in rotation)-1.) > 1e-5:
                raise ValueError('Measured link rotation is not a unit quaternion')
            actual = [compose(pose(frame), (point, [0., 0., 0., 1.]))[0] for point in points]
            for wall, center, dimensions in boxes:
                depths = [box_vertex_depth(point, center, dimensions) for point in actual]
                selected_depths = [value for value in depths if value > .0001]
                if selected_depths:
                    hits.append(dict(link=link, wall=wall, penetrating_vertex_count=len(selected_depths),
                        max_vertex_depth_m=max(selected_depths)))
                if margin is not None:
                    nearest = min(point_box_signed_distance(point, center, dimensions) for point in actual)
                    if nearest < margin:
                        clearances.append(dict(link=link, wall=wall, min_vertex_clearance_m=nearest))
        result = dict(sample=row['sample'], cup_vertex_intersections=hits)
        if margin is not None:
            result['below_margin'] = clearances
        rows.append(result)
    report = dict(kind='actual_PhysX_frames_pinned_STL_vertex_vs_cup_shell', cup=cup_id,
        intersection_threshold_m=.0001, shell_wall_m=SHELL_WALL_M, meshes=sorted(selected),
        no_intersection_is_not_collision_clearance=True, rows=rows)
    if margin is not None:
        report.update(margin_m=margin, margin_ok=all(not row['below_margin'] and not row['cup_vertex_intersections']
                                                     for row in rows))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--layout', type=Path, required=True)
    parser.add_argument('--mesh-directory', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cup', default='supply_cup')
    parser.add_argument('--margin', type=float, help='Also report vertex clearance below this margin (m)')
    parser.add_argument('--all-hand-meshes', action='store_true',
                        help='Screen all eight pinned right-hand meshes, not only the two distal links')
    args = parser.parse_args()
    summary, layout = args.summary.read_bytes(), args.layout.read_bytes()
    result = screen(json.loads(summary), json.loads(layout), args.mesh_directory, cup_id=args.cup,
                    meshes=HAND_MESHES if args.all_hand_meshes else None, margin=args.margin)
    result.update(summary_sha256=hashlib.sha256(summary).hexdigest(), layout_sha256=hashlib.sha256(layout).hexdigest())
    write_json(args.out, result)


if __name__ == '__main__':
    main()
