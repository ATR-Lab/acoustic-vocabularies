"""Compare measured Isaac poses with Unity FK exports in the same ROS/USD frame."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def quaternion_error_degrees(a, b):
    if len(a) != 4 or len(b) != 4 or not all(math.isfinite(v) for v in a + b):
        raise ValueError("Invalid quaternion")
    na = math.sqrt(sum(v * v for v in a))
    nb = math.sqrt(sum(v * v for v in b))
    if na == 0 or nb == 0:
        raise ValueError("Zero quaternion")
    dot = abs(sum(x * y for x, y in zip(a, b)) / (na * nb))
    return math.degrees(2 * math.acos(min(1, max(0, dot))))


def keyed(items, field):
    result = {item[field]: item for item in items}
    if len(result) != len(items):
        raise ValueError("Duplicate " + field)
    return result


def compare(isaac, unity):
    if isaac["coordinate_frame"] != unity["coordinate_frame"] or isaac["coordinate_frame"] != "usd_world_rh_z_up":
        raise ValueError("Mismatched/unknown coordinate frames")
    left = keyed(isaac["poses"], "name")
    right = keyed(unity["poses"], "name")
    if left.keys() != right.keys() or not left:
        raise ValueError("Pose sets differ or are empty")
    rows, missing_unity, unobserved = [], set(), set()
    for name, reference in left.items():
        actual = right[name]
        if reference["joint_names"] != actual["joint_names"] or len(reference["joint_positions"]) != len(actual["joint_positions"]):
            raise ValueError("Joint vector contracts differ")
        if len(reference["joint_names"]) != len(reference["joint_positions"]) or not all(
                math.isfinite(value) for value in reference["joint_positions"] + actual["joint_positions"]):
            raise ValueError("Invalid joint vector")
        if any(abs(x - y) > 1e-7 for x, y in zip(reference["joint_positions"], actual["joint_positions"])):
            raise ValueError("Unity did not apply the same measured joint vector")
        reference_links = keyed(reference["links"], "name")
        actual_links = keyed(actual["links"], "name")
        missing_unity.update(reference_links.keys() - actual_links.keys())
        unobserved.update(actual_links.keys() - reference_links.keys())
        for link in sorted(reference_links.keys() & actual_links.keys()):
            a, b = reference_links[link], actual_links[link]
            positions = a["position"] + b["position"]
            if len(positions) != 6 or not all(math.isfinite(v) for v in positions):
                raise ValueError("Invalid position")
            error_mm = 1000 * math.dist(a["position"], b["position"])
            angle_deg = quaternion_error_degrees(a["quaternion_wxyz"], b["quaternion_wxyz"])
            rows.append(dict(pose=name, link=link, position_error_mm=error_mm,
                             orientation_error_deg=angle_deg, within_proposed_tolerance=error_mm <= 5 and angle_deg <= 1))
    if not rows:
        raise ValueError("No comparable links")
    summary = dict(poses=len(left), compared_link_pose_pairs=len(rows),
                   max_position_error_mm=max(r["position_error_mm"] for r in rows),
                   max_orientation_error_deg=max(r["orientation_error_deg"] for r in rows),
                   missing_unity_links=sorted(missing_unity), unobserved_unity_links=sorted(unobserved),
                   all_observed_links_within_proposed_tolerance=not missing_unity and all(r["within_proposed_tolerance"] for r in rows),
                   full_urdf_link_coverage=not unobserved and not missing_unity,
                   tolerance_status="proposed_5mm_1degree_pending_protocol_review")
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("isaac", type=Path)
    parser.add_argument("unity", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = compare(json.loads(args.isaac.read_text()), json.loads(args.unity.read_text()))
    summary["isaac_pose_sha256"] = hashlib.sha256(args.isaac.read_bytes()).hexdigest()
    summary["unity_pose_sha256"] = hashlib.sha256(args.unity.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "pose_errors.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    (args.output / "pose-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
