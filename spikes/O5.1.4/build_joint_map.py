"""Produce identity-name candidates; require complete pose proof before marking verified."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("description", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verified-pose-summary", type=Path)
    args = parser.parse_args()
    description = json.loads(args.description.read_text())
    joints = {j["name"]: j for j in description["joints"] if j["type"] == "revolute"}
    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        inventory = list(csv.DictReader(handle))
    names = [j["name"] for j in inventory]
    if not names or len(names) != len(set(names)):
        raise ValueError("Empty or duplicate Isaac joints")
    records = []
    verified = False
    proof = None
    if args.verified_pose_summary:
        proof = json.loads(args.verified_pose_summary.read_text())
        verified = (proof["poses"] == 92 and proof["full_urdf_link_coverage"] and
                    proof["all_observed_links_within_proposed_tolerance"] and bool(proof["isaac_pose_sha256"]))
        if not verified:
            raise ValueError("Pose summary does not establish the complete proposed tolerance check")
    for item in inventory:
        joint = joints.get(item["name"])
        records.append(dict(isaac_index=item["index"], isaac_joint=item["name"], unity_joint=joint["name"] if joint else "",
                            axis_urdf=" ".join(map(str, joint["axis"])) if joint else "", candidate_sign=1,
                            candidate_zero_offset_rad=0, urdf_lower_rad=joint["lower"] if joint else "",
                            urdf_upper_rad=joint["upper"] if joint else "", isaac_lower_rad=item["lower_rad_or_m"],
                            isaac_upper_rad=item["upper_rad_or_m"], pose_verified=str(verified).lower()))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "joint_map.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(records)
    result = dict(isaac_joints=len(names), unity_driven_joints=len(joints),
                  name_matched=sum(name in joints for name in names),
                  unmapped_isaac=sorted(set(names) - joints.keys()), unmapped_unity=sorted(joints.keys() - set(names)),
                  left_hand=sum(name.startswith("left_hand_") for name in names if name in joints),
                  right_hand=sum(name.startswith("right_hand_") for name in names if name in joints),
                  pose_verification="92_poses_passed_proposed_tolerance" if verified else "pending",
                  identity_sign_offset="verified_on_recorded_poses" if verified else "candidate_only",
                  verification_isaac_pose_sha256=proof["isaac_pose_sha256"] if verified else None,
                  canonical_map_encoding="UTF-8 without BOM, LF newlines, observed Isaac index order",
                  canonical_map_sha256=hashlib.sha256((args.output / "joint_map.csv").read_bytes()).hexdigest())
    (args.output / "mapping-summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
