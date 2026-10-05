# Provisional fixed-base G1 workcell — O5.2.1

This is an engineering implementation of issue 52. The read-only Common procedures sections 2/3 have not been located. Dimensions, object counts, neutral posture, visual labels and observer placement require human reconciliation before a study configuration is frozen. Development authorization does not mean G1 approval or an Accepted ADR.

The public deterministic layout is `apparatus/workcell_layout.json`. `python -m isaac.workcell.layout --output apparatus/workcell_layout.json` regenerates it using standard Python. It contains60 semantic objects,29+7+7 fixed-base G1 joints, physical anchors and immutable appearance defaults. Left/right is from the observer looking toward the robot: left is negative worldY. Coordinates are right-handed, Z-up metres, radians and quaternion `xyzw`. Unity position mapping is `(-y,z,x)`; rotations require basis conjugation, not component guessing.

## Generated scene and asset policy

`isaac/workcell/build_usd.py` generates only project-authored procedural geometry: matched surfaces, trays, identical ring washers, two-sided cards, rotatable arrows and upright slots, hollow containers, open hinged lids, static code plaques, free clip tags, marked quarantine zones, supply/return cups, fixed materials and lighting. The geometric glyphs are authored in source; there is no font dependency. The robot references the pinned #44 asset through the approved container mount `/assets`. Unitree DDS is never imported or started.

The scene is generated as ignored `workcell.usda` in the evidence directory. It is deliberately not checked into Git: USD/USDA require LFS under repository policy, available LFS upload is blocked, and the user prohibited public redistribution of the asset USD. No missing LFS pointer substitutes for an artifact. Source and public layout are the reproducible deliverable until artifact storage is restored.

The runner exports the root layer, reopens it, compares the 60 actual USD-backed states, exports again and requires identical SHA256. This verifies USD round-trip stability for geometry and the 60 scripted objects; independent-run hashes are reported separately. Load through the runner: the robot neutral joint vector and observer camera pose are applied from the hashed layout, and the complete runtime neutral state is captured by #53. Bare USD replay of the runtime articulation or camera pose is not claimed. Asset pins and the #44 transitive hash inventory remain part of provenance. It does not claim hashing only the root layer covers third-party dependency content.

## Stable registry and state contract

Semantic prim paths are `/World/Workcell/Objects/<id with / replaced by __>`. IDs never depend on display labels or trial content.

| IDs | State keys |
|---|---|
| `surface_left`, `surface_right`, `tray_A`–`tray_D`, `quarantine_E`–`quarantine_H`, `supply_cup`, `return_cup` | empty |
| `tray_A/card`–`tray_D/card` | `card_face` integer0/1 |
| `tray_A/arrow`–`tray_D/arrow` | `arrow_angle_rad` |
| `tray_A/washer_0`–`tray_D/washer_2`, `supply/washer_0`–`supply/washer_11` | `location` registered physical anchor |
| `container_E`–`container_H` | `location` |
| each container `/lid` | `lid_open_fraction` in0..1 |
| each container `/tag` | `tag_attached`, `location` |
| each container `/code` | empty; static code label is in layout |

`StateAccessors(stage, layout)` exposes `read_state()`, `apply_state(mapping)`, `apply_subset(mapping)`, `read_public_state()`, `read_environment()`, `apply_environment(mapping)` and `anchor_ids`. `apply_subset` validates complete records for a selected set of known IDs before writing any of them; it supports a small carried group without rewriting unrelated objects. Every object has `position_m`, `rotation_xyzw`, `visible`, `enabled`, `collision_enabled`, `linear_velocity_m_s`, `angular_velocity_rad_s` and its exact typed `state` mapping. Unknown/missing IDs, extra state fields, unregistered locations, nonfinite vectors and nonunit quaternions are rejected before mutation. `enabled` controls scripted availability and does not silently remove geometry. Collision flags are authored on all geometry primitives and defaultfalse. Props are scripted kinematic objects; their authored velocity attributes are not presented as measured dynamic rigid-body velocities.

Actual card/arrow/lid child rotations are checked against the state values on read. Unexpected semantic transform operations, transformed or hidden workcell ancestry fail closed. Visibility is explicit. Environment capture reads actual UsdShade colors/roughness and light intensity/color, and restoration applies those authored values. The robot adapter independently reads actual43-joint/root/link state from PhysX.

Location anchors include tray IDs, cups, each container `/home` and `/tag_free`, quarantine IDs and `robot/left_hand`/`robot/right_hand` mapped to palm bodies. A location label alone does not move an object: the scripted command must supply its matching world pose. Attachment and lid/card/arrow state are visual apparatus facts; no trial, intended target, mode, answer or scan result is encoded in this public state contract.

## Precondition and reach checks

`preconditions(layout, actual_state)` evaluates all 32 issue-defined action-target pairs. It checks available supply, tray/return capacity, removable washers, available two-sided cards, misaligned arrows, visible nonempty printed codes, free tags, open lids and unoccupied available quarantine zones. These are state-level action availability checks, not proof of collision-free motion. Eleven pure tests exercise deterministic export, matched geometry, negative state cases, strict validation and quarantine separation.

The work surfaces are 0.435 m by 0.43 m, positioned symmetrically. Targets use compact two-by-two grids close to the measured G1 shoulders. The runner attempts palm position-only IK to a 6 cm approach above each physical anchor using actual loaded articulation Jacobians and limits, holding torso/legs neutral. The neutral arm override is shoulder pitch+0.6rad and elbow-1.0rad on both sides; the initial default arm pose intersected props and was rejected. Matched target placards are raised to keep the rear row visible from the provisional observer. Results include target/achieved coordinates and joint vectors. Even successful position checks do not establish hand orientation, collision-free paths, grasp feasibility or ergonomic/protocol acceptability; #56 motion and human review must address those explicitly.

## Reproduce in the approved isolated runtime

Use the pinned #44 installation and existing approved `acoustic-vocab-spike:isaac5.1-rendered-cache-20261004` image. Mount the source read-only at `/work`, approved asset cache read-only at `/assets`, and a new writable results directory at `/results`. Preserve the image's `/lab` and `/unitree` sources and Git safe-directory settings from the #44 runbook. Set `--network none`, GPUdevice0, approved EULA variables and root Kit allowance. No new dependencies or host network configuration are required.

Run `/isaac-sim/python.sh /work/isaac/workcell/run_scene.py --headless --capture --output /results/<unique-run>`. It refuses any namespace beyond loopback, verifies pinned sources before Kit imports, loads the actual fixed-base43-joint articulation, exports/reloads the generated USD, records actual state/environment/preconditions, performs reach diagnostics and renders observer/tray/container views. The evidence directory must be new.

Optional `--integration-overlay /integration --reset-check` imports #53 from a separate ignored integration tree and performs 1000 actual perturb/reset/readback checks. This is a stacked-component integration measurement; reset implementation does not belong in the #52 PR. Reset uses `sim.forward()` with no dynamics/time advance, explicit direct state writes and independent PhysX readback. Neither rendering nor passing reset makes a study qualification claim.
