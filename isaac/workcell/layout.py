"""Deterministic, public engineering layout for O5.2.1.

Common procedures have not been located. These dimensions/counts are explicit
engineering choices for review, not a frozen study configuration.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def neutral_layout():
    objects = []
    anchors = {}

    def add(identifier, kind, position, dimensions, material="neutral", state=None,
            rotation=None, label="", **extra):
        record = dict(id=identifier, kind=kind,
                      prim_path="/World/Workcell/Objects/" + identifier.replace("/", "__"),
                      position_m=position, rotation_xyzw=rotation or [0., 0., 0., 1.],
                      dimensions_m=dimensions, material=material, label=label,
                      visible=True, enabled=True, collision_enabled=False,
                      linear_velocity_m_s=[0., 0., 0.], angular_velocity_rad_s=[0., 0., 0.],
                      state=state or {})
        record.update(extra)
        objects.append(record)
        return record

    def anchor(identifier, position):
        anchors[identifier] = dict(position_m=position, rotation_xyzw=[0., 0., 0., 1.])

    add("surface_left", "surface", [.235, -.225, .815], [.47, .43, .04], "surface")
    add("surface_right", "surface", [.235, .225, .815], [.47, .43, .04], "surface")
    for index, letter in enumerate("ABCD"):
        tray = "tray_" + letter
        x, y = .115 + (index // 2) * .22, -.105 - (index % 2) * .19
        add(tray, "tray", [x, y, .851], [.18, .16, .032], label=letter, capacity=6)
        anchor(tray, [x + .045, y, .870])
        add(tray + "/card", "card", [x - .048, y - .035, .871], [.055, .048, .004],
            "card", dict(card_face=0))
        add(tray + "/arrow", "arrow", [x - .048, y + .035, .872], [.052, .052, .005],
            "arrow", dict(arrow_angle_rad=math.pi / 2))
        for washer in range(3):
            add(f"{tray}/washer_{washer}", "washer",
                [x + .045, round(y + (washer - 1) * .044, 6), .871], [.025, .025, .006],
                "washer", dict(location=tray))
    for index, letter in enumerate("EFGH"):
        container = "container_" + letter
        x, y = .115 + (index // 2) * .22, .075 + (index % 2) * .21
        home, tag_free, quarantine = container + "/home", container + "/tag_free", "quarantine_" + letter
        anchor(home, [x, y, .875])
        anchor(tag_free, [x + .065, y, .840])
        anchor(quarantine, [x, y + .105, .875])
        add(container, "container", [x, y, .875], [.095, .09, .075], state=dict(location=home))
        add(container + "/lid", "lid", [x - .0475, y, .9125], [.095, .09, .006],
            state=dict(lid_open_fraction=1.0), hinge_axis="Y", open_angle_rad=-math.radians(110))
        add(container + "/tag", "tag", [x + .065, y, .840], [.027, .021, .006],
            "tag", dict(tag_attached=False, location=tag_free))
        add(container + "/code", "code", [x + .049, y, .884], [.002, .078, .024],
            "label", label=letter + "001")
        add(quarantine, "quarantine", [x, y + .105, .837], [.095, .095, .004],
            "quarantine", label=letter)
    for identifier, y in (("supply_cup", -.405), ("return_cup", .405)):
        add(identifier, "cup", [.045, y, .856], [.066, .066, .042], capacity=24,
            label="SUPPLY" if identifier == "supply_cup" else "RETURN")
        anchor(identifier, [.045, y, .845])
    for index in range(12):
        col, row, level = index % 2, (index // 2) % 2, index // 4
        add(f"supply/washer_{index}", "washer",
            [round(.045 + (col - .5) * .028, 6), round(-.405 + (row - .5) * .028, 6),
             round(.842 + level * .006, 6)], [.025, .025, .006], "washer", dict(location="supply_cup"))
    # Dynamic hand anchor positions are supplied by the loaded articulation.
    hand_anchors = {"robot/left_hand": "left_hand_palm_link", "robot/right_hand": "right_hand_palm_link"}
    return dict(
        version=1, status="provisional_issue_52_engineering_layout",
        authority="GitHub issue52 only; Common procedures sections2/3 require human reconciliation",
        coordinate_frame="isaac_world_rh_z_up", length_unit="m", angle_unit="rad",
        quaternion_order="xyzw", unity_position_mapping="(-y,z,x)",
        left_right_reference="observer looking toward robot; left is negative world Y",
        neutral_washers_per_tray=3, neutral_supply_washers=12,
        robot=dict(configuration="G129_CFG_WITH_DEX3_BASE_FIX", prim_path="/World/Robot",
                   position_m=[0., 0., .75], fixed_base=True, body_joint_count=29,
                   left_hand_joint_count=7, right_hand_joint_count=7),
        observer=dict(status="provisional_not_ADR_accepted", position_m=[1.65, 0., 1.30],
                      look_at_m=[.15, 0., .96], up_axis="Z", focal_length_mm=24.,
                      horizontal_aperture_mm=36., width=2560, height=1440),
        materials={"surface": [.55, .57, .60], "neutral": [.72, .74, .76],
                   "washer": [.28, .30, .32], "card": [.90, .90, .88],
                   "arrow": [.12, .14, .16], "tag": [.52, .54, .56],
                   "label": [.96, .96, .94], "quarantine": [.68, .68, .66],
                   "ink": [.015, .015, .015]},
        lights=[dict(id="fixed_dome", kind="dome", intensity=2000., color=[1., 1., 1.])],
        mutable_materials=[], mutable_lights=[], anchor_ids=sorted([*anchors, *hand_anchors]),
        anchors=anchors, articulation_anchors=hand_anchors,
        objects=sorted(objects, key=lambda record: record["id"]))


def neutral_state(layout):
    fields = ("position_m", "rotation_xyzw", "visible", "enabled", "collision_enabled",
              "linear_velocity_m_s", "angular_velocity_rad_s", "state")
    return {obj["id"]: {key: obj[key] for key in fields} for obj in layout["objects"]}


def preconditions(layout, states):
    """Evaluate all32 issue-defined pairs against actual readable state."""
    definitions = {obj["id"]: obj for obj in layout["objects"]}
    if set(states) != set(definitions):
        raise ValueError("State object registry differs from layout")
    def available(identifier):
        return states[identifier]["enabled"] and states[identifier]["visible"]
    def washers_at(location):
        return [key for key, item in states.items() if definitions[key]["kind"] == "washer"
                and item["enabled"] and item["visible"] and item["state"]["location"] == location]
    results = []
    for letter in "ABCD":
        target = "tray_" + letter
        conditions = {
            "ADD_ONE": bool(washers_at("supply_cup")) and len(washers_at(target)) < definitions[target]["capacity"],
            "REMOVE_ONE": bool(washers_at(target)) and len(washers_at("return_cup")) < definitions["return_cup"]["capacity"],
            "FLIP_CARD": available(target + "/card") and states[target + "/card"]["state"]["card_face"] in (0, 1),
            "ALIGN_ARROW": available(target + "/arrow") and abs(math.remainder(states[target + "/arrow"]["state"]["arrow_angle_rad"], 2 * math.pi)) > .01,
        }
        for action, possible in conditions.items():
            results.append(dict(action=action, target=target, possible=bool(possible and available(target))))
    for letter in "EFGH":
        target, quarantine = "container_" + letter, "quarantine_" + letter
        conditions = {
            "SCAN": available(target + "/code") and bool(definitions[target + "/code"]["label"]) and states[target + "/code"]["visible"],
            "TAG": available(target + "/tag") and not states[target + "/tag"]["state"]["tag_attached"] and states[target + "/tag"]["enabled"],
            "CLOSE": available(target + "/lid") and states[target + "/lid"]["state"]["lid_open_fraction"] > 0.,
            "QUARANTINE": available(quarantine) and not any(item["state"].get("location") == quarantine for item in states.values()),
        }
        for action, possible in conditions.items():
            results.append(dict(action=action, target=target, possible=bool(possible and available(target))))
    return dict(pair_count=len(results), possible_count=sum(row["possible"] for row in results), pairs=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    layout = neutral_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(layout))
    print(json.dumps(dict(layout_sha256=digest(layout), objects=len(layout["objects"]),
                          preconditions=preconditions(layout, neutral_state(layout))), indent=2))
