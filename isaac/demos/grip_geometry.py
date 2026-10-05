"""Read-only, standard-library vertex/box collision screen for the grip probe.

Positive intersections prove a geometric failure. No intersections do not prove
triangle collision freedom. This tool does not modify the scene or recordings.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from .geometry import compose, pose, relative
from .grip_probe import MESH_SHA256
from .recording import write_json


def box_vertex_depth(point, center, dimensions):
    if any(not math.isfinite(x) for x in (*point, *center, *dimensions)) or any(x <= 0 for x in dimensions):
        raise ValueError('Finite positive box geometry required')
    return max(0., min(size/2-abs(x-c) for x, c, size in zip(point, center, dimensions)))


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


def screen(summary, layout, mesh_directory):
    cup = next(item for item in layout['objects'] if item['id'] == 'supply_cup')
    x, y, z = cup['dimensions_m']
    w = .004  # Exact shell wall thickness in the frozen #52 build_usd.shell.
    if cup['kind'] != 'cup' or min(x, y, z) <= w*2:
        raise ValueError('Unsupported cup geometry')
    boxes = [('Bottom', (0, 0, -z/2+w/2), (x, y, w)),
             ('Back', (-x/2+w/2, 0, 0), (w, y, z)),
             ('Front', (x/2-w/2, 0, 0), (w, y, z)),
             ('Left', (0, -y/2+w/2, 0), (x, w, z)),
             ('Right', (0, y/2-w/2, 0), (x, w, z))]
    meshes = {name.removesuffix('.STL'): read_vertices(Path(mesh_directory)/name, digest)
              for name, digest in MESH_SHA256.items()}
    rows = []
    for row in summary['rows']:
        hits = []
        for link, points in meshes.items():
            frame = row['frames'][link]
            rotation = frame['rotation_xyzw']
            if len(rotation) != 4 or abs(sum(v*v for v in rotation)-1.) > 1e-5:
                raise ValueError('Measured link rotation is not a unit quaternion')
            local_frame = relative(pose(cup), pose(frame))
            actual = [compose(local_frame, (point, [0., 0., 0., 1.]))[0] for point in points]
            for wall, center, dimensions in boxes:
                depths = [box_vertex_depth(point, center, dimensions) for point in actual]
                selected = [value for value in depths if value > .0001]
                if selected:
                    hits.append(dict(link=link, wall=wall, penetrating_vertex_count=len(selected),
                        max_vertex_depth_m=max(selected)))
        rows.append(dict(sample=row['sample'], cup_vertex_intersections=hits))
    return dict(kind='actual_PhysX_frames_pinned_STL_vertex_vs_cup_shell',
        intersection_threshold_m=.0001, shell_wall_m=w,
        no_intersection_is_not_collision_clearance=True, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--layout', type=Path, required=True)
    parser.add_argument('--mesh-directory', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    summary, layout = args.summary.read_bytes(), args.layout.read_bytes()
    result = screen(json.loads(summary), json.loads(layout), args.mesh_directory)
    result.update(summary_sha256=hashlib.sha256(summary).hexdigest(), layout_sha256=hashlib.sha256(layout).hexdigest())
    write_json(args.out, result)


if __name__ == '__main__':
    main()
