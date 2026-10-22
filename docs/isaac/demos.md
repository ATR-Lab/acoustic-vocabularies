# Kinematic visualization development (#56)

This implements the eight public issue meanings for all 32 legal pairs through
the existing private command dispatcher. It is a **kinematic visualization**, not
a grasp policy. The missing methodology has not been inferred. The joint paths,
virtual grip offsets and duration below are engineering proposals requiring
actual reach and visual review before use.

`isaac.demos.planner.compile_plan` solves bounded arm keyframes against actual
PhysX Jacobians and joint limits. Both seven-joint Dex3 hands are present in every
43-joint vector; active fingers display a provisional closing/opening motion.
The planner requires position residual below 0.5 mm and, when constrained,
orientation residual below 0.008 rad. An infeasible keyframe rejects the pair.
Planning runs before a public or private demonstration service is exposed, and
finishes with an explicit verified neutral reset.

The runtime interpolates joint keyframes and reads the actual articulation after
each write. It requires measured joint error at most 0.001 rad. Washers, tags,
cards and container groups follow rigid transforms relative to measured palms.
The virtual grip is initially 60 mm below the palm, except card flips use a
75 mm side grip parallel to the flip axis so the palm is not driven below the
table during the half-turn. Intermediate IK constrains the carried grip point
and permits orientation change; placement constrains the complete pose. A
bounded grip/orientation search retains all failed attempts. Washers may end
with a multiple-of-45-degree yaw, which preserves their 32-segment ring geometry;
the chosen quaternion is explicit in the expected end-state record. These are
engineering attachment references, not established fingertip contacts. Container lids,
codes and already-attached tags move with their container; free tags stay put.
Arrow angle follows measured wrist yaw, and lid angle follows the measured palm
along its hinge arc. SCAN's printed result stays in the private result/index.

REMOVE_ONE uses a two-hand center transfer. Both measured virtual grip frames
must agree within 3 mm and 0.02 rad before ownership changes. The hands remain
separate; a failure leaves ownership unchanged and reports an execution failure.
No layout change or cross-body reach assumption is hidden in this operation.
All 32 pairs are feasible on the actual G1 at `a30e950`; the all-pair visual
collision review is pending.

Every execution starts from verified neutral, returns the robot to neutral and
leaves its intended object consequence. The following explicit reset restores
the complete neutral snapshot. This follows the issue's Intended behavior; it
does not silently erase the consequence before the viewer can observe it.
Cancellation leaves the current visible state until the dispatcher's explicitly
logged reset. No result is a semantic response score.

## Fixed sample and fixed sim-step recording contract

> **Status: recorded on Isaac at `a30e950`; human visual review pending.** The
> native procedure at the end of this page stopped at its preflight at `d185b7c`
> (31/32, ADD_ONE/tray_D infeasible). At `a30e950` it passed steps 2-6
> (*Native run record (a30e950)* below): 32/32 feasible, 40/40 recordings on
> the fixed 600-step schedule with replay and following reset, the suite
> validation, the declared-envelope check on actual palm poses and the
> pinned-mesh screen. The private index has `recording_complete=true`.
> `collision_reviewed` and `grasp_contact_validated` stay false, and the library
> is not qualified until the human visual review (step 7) and the Unity consumer
> update (step 8). The earlier retained suite (4/40 under the host-span screen)
> is kept unchanged as failed evidence.

Orientation and execution both provisionally use **300 samples at 30 Hz** and a
**10 s nominal display duration**. These are engineering choices, not approved
protocol timing. The command scheduler must call one demo `advance()` per 30 Hz
sample.

**Recording (maintainer decision, October 2026).** The earlier capture slept
toward host-clock deadlines and screened the measured host span; under host
contention 36/40 captures ran long (9.96-11.34 s). Recording now follows a
fixed sim-step schedule instead (`isaac.demos.runtime`):
`PHYSICS_STEPS_PER_SAMPLE = 2`, `PHYSICS_DT_SECONDS = 1/60`, so every one of the
8 orientation and 32 execution recordings spans exactly
`TOTAL_PHYSICS_STEPS = 600` physics steps (10 s at 60 Hz) by construction.

- `recording.record_fixed_schedule` takes exactly two physics steps, then one
  `advance()`, then one sample, 300 times. It never sleeps or reads a host
  deadline, so host load changes how long capture takes, never what is recorded.
- The benchmark refuses to record if the simulator's physics dt is not 1/60 s.
- Each frame's `sim_step` is the demo-relative step count (2, 4, ..., 600).
  `TrajectoryWriter` refuses a frame whose step count is not exactly
  2 x (sample + 1), whose `sim_time` did not advance by 2/60 s (+/-10 us; the
  integer step count is the authority), or that would exceed 300 samples.
  `read_trajectory` re-checks the same schedule before any replay.
- After the suite, `recording.validate_suite` refuses unless all 40 recordings
  are complete, carry the identical schedule and span the identical 600 steps.
  A refusal keeps `recording_complete=false` and is written, with the measured
  physics dt, to the private `schedule-validation.json`.

**Playback.** The existing rule is kept: simulation time is never the
participant-facing clock. Playback is paced on the playback host's monotonic
clock at the fixed sample period: sample *i* is displayed at *i*/30 s after the
start and the last sample is held to 10 s (`recording.playback_offset_seconds`,
`recording.playback_index`; neither accepts a frame or a sim time). Captured host
stamps stay in every frame unchanged as provenance and are summarized as
`capture_host_seconds` / `capture_interval_ms`; they are never rewritten or
compressed, but because capture is unpaced they are not a playback schedule.

**Index schema change.** Index, row and execution keys are unchanged. The
per-row `capture` record replaces the host-span screen fields (`timing_ok`,
`measured_first_to_last_host_seconds`, `interval_ms`) with `schedule`,
`recorded_physics_steps`, `recorded_duration_seconds`, `schedule_ok`,
`capture_host_seconds`, `capture_interval_ms` and `playback_clock`. The Unity
orientation loader (#66) and snapshot player (#62) still expect the old fields
and pace by recorded host-stamp offsets, so they reject a new-format index
(`ORIENTATION_FIELDS`): they fail closed. They must be changed to this contract
(host-clock pacing by sample index) before a re-recorded library can be shown.
That Unity change is not part of this PR.

Each opaque `000.ndjson` file contains plain public v2 state frames, with actual
host-monotonic capture timestamps, the demo-relative step count, advancing
simulation time, all 43 measured joints and all 60 public object states. No
action/target or trial fields enter these frames. A separate **private** index
records group (8 orientation or 32 execution), action/target, SHA-256, registry
hashes, expected consequence, execution outcome, following reset and replay
result. It preserves incomplete captures and failures.

`TrajectoryWriter` flushes/fsyncs on completion or failure. JSON summaries are
written through a flushed replacement. Hash-checked replay validates every
frame and the fixed schedule, applies recorded joints/props to the actual
articulation/USD, and checks the expected final state before the following
reset. Raw files and render captures remain in a fresh ignored output directory.
Do not publish private indices or raw station information. Public derivative
reports must retain all failures and state the measured revision.

## Actual run

Use the approved isolated Isaac environment and the current frozen #52 layout.
The scene runner's optional `--demo-preflight` hook calls
`run_demo_check(manager, layout, output, preflight_only=True)` after loading its
verified snapshot. It writes all 32 keyframe outcomes and returns to verified
neutral. Only a complete feasible library proceeds to `--demo-check`.

The full hook tests all 32 protected-mode requests with the real factory bound,
records 8 orientation plus 32 execution files, checks actual end states and every
following reset, then replays all recordings into the articulation. Optional
captures are a separate unpaced pass at seven sample indices per execution;
they are deliberately excluded from timing evidence. Every completed ADD_ONE
item also gets a separate unpaced pass that writes actual right-hand link frames
for each supply-cup corridor sample (and any other sample whose reach sphere
meets the cup) to `cup-clearance-NNN.private.json`, plus the declared-envelope
check on the actual palm poses to `cup-clearance.json`. Inspect each sequence for
reach, self-collision, prop crossings and intelligibility. `collision_reviewed`
stays false until that review is explicitly recorded. Grasp/contact accuracy is
outside scope, but visibly impossible paths are not acceptable demonstrations.

Pure tests use a clearly synthetic IK backend to verify all 32 meanings,
attachment transforms, handoff refusal, interruption, matching frame counts,
hashes, clock progression, the fixed sim-step schedule (including all 40 items
recorded under a synthetic contended host clock with identical 600-step
durations, and refusal of skipped/extra steps, a wrong physics dt, an early end
or any suite member with a different duration), and the supply-cup clearance
of the planned ADD_ONE path. They do not establish actual robot reach or
collision-free motion.

## Bounded grip repair diagnostic

The first actual ADD_ONE capture exposed an unacceptable visual result: the
washer followed a point near the wrist, outside the fingers. The earlier
`virtual_grip_offset_m` is not a measured contact point. Keep those recordings
as failed visual evidence; numerical IK and final-state success do not repair it.

`isaac.demos.grip_probe.run_grip_check(manager, layout, output,
capture_image=...)` is a separate, optional one-item diagnostic. It approaches
the top supply washer selected for ADD_ONE/tray_A, closes a thumb/middle pinch,
lifts, and replaces it in the supply cup. It does **not** complete ADD_ONE or
create a playable recording index. The probe is retained unchanged as failed
evidence; the forty-item planner's ADD_ONE pickup has since been replaced by the
supply-cup corridor below, which does not use this pinch posture.

The candidate finger angles and palm-local grip point are derived from the
pinned G1 URDF and actual distal STL vertices; their source mesh hashes are in
the module. The index finger stays at its neutral pose. The offline geometry
screen includes a supporting-plane margin; it does not establish whole-hand,
cup-wall or robot collision clearance. The narrow supply cup may obstruct this
candidate, and fixed-orientation transfer to the tray failed the bounded offline
IK search. No workcell geometry is changed to hide either limitation.

The diagnostic writes actual PhysX distal-link poses, selected mesh-vertex
distances to the washer's outer cylinder, all 43 joint values, observer and
close-up images, and a following verified reset. A vertex distance is a sampled
geometry check, not a physical contact force or complete triangle collision
test. Rendering is outside timing measurement. `grasp_contact_validated`,
`collision_reviewed`, and `complete_action_demonstrated` remain false. Review
the actual pickup/closure/lift and cup clearance before extending this candidate
to the action library; keep any failed probe and its full diagnostics.

The first complete actual suite produced 40 captures / 12,000 public v2 frames;
all 40 endpoint replays and following resets passed. Only 4 of 40 passed the
provisional timing screen (host spans 9.9626–11.3376 s), so
`recording_complete=false`. The ADD_ONE wrist-offset visual failure independently
prevents acceptance. The raw failed suite is retained unchanged.

The bounded corrected probe at `ed268fa` completed pickup/lift/replacement and
reset. At the closed/lift samples, the selected thumb/middle mesh vertices were
approximately -0.0224 mm / +0.4946 mm from the washer's outer surface; the actual
close-up places the washer between the fingers. However, a separate geometry
screen transforms the pinned STL vertices using actual PhysX link poses and
finds them inside the supply cup's 4 mm walls during approach, closure and
replacement, up to about 2 mm from a wall boundary. This is a confirmed collision
failure; clear lifted samples do not qualify the pickup. Complete transfer to
the tray remains unresolved. No layout change or full-action qualification is
implied. Sanitized hashes and counts are in [demo-diagnostics.json](demo-diagnostics.json).

Reproduce the read-only cup screen with
`python -m isaac.demos.grip_geometry --summary <private-probe-summary> --layout apparatus/workcell_layout.json --mesh-directory <approved-pinned-G1-meshes> --out <private-diagnostic-output>`.
It verifies both distal STL hashes. A positive vertex/box intersection proves a
failure; its absence would not prove triangle or whole-robot collision freedom.

The retained current-posture orientation screen also found no candidate for a
new actual probe: all 180 yaw-only candidates (2-degree steps) intersected the
cup. Of 600 bounded yaw/pitch/roll combinations, 480 failed the selected contact
range and all remaining 120 intersected a cup wall. The calculation uses eight
pinned hand meshes in captured link frames, rigidly rotated about the unchanged
washer. It is hypothetical geometry, not a motion trajectory or IK result; no
layout changed. Because every candidate already failed, table, other-link and
full-triangle clearance were not claimed. A materially different finger posture
would require a new bounded plan.

Reproduce the rejection screen in the approved NumPy environment using
`python -m isaac.demos.grip_orientation_search --summary <private-summary> --summary-sha256 <captured-sha256> --layout apparatus/workcell_layout.json --mesh-directory <pinned-meshes> --out <private-output>`;
add `--tilted` for the 600-candidate contact-preserving screen. Positive vertex
intersections reject a candidate; their absence would not establish clearance.

## Supply-cup grasp plan (redesigned after the d185b7c preflight; see *Transport reorientation*)

The frozen layout leaves 2.5 mm between the outer supply washers and the cup's
inner walls, and 3 mm between neighbouring washers. No lateral pinch of a supply
washer fits, which is what the probe and the orientation screen showed. The
planner's ADD_ONE pickup therefore uses a different plan (`planner._compile_add_one`
and `_compile_one`, geometry in `grip_geometry`):

- **Posture.** Right Dex3 hand with a straight middle finger
  (`middle_0 = middle_1 = 0`), a curled index finger (1.55 / 1.50 rad) and the
  thumb straight along palm +y. All values are within the pinned URDF limits.
  The posture is fixed from corridor entry to release.
- **Orientation in the cup.** Fingers-down palm (palm +x to world -Z), optionally
  leaning by a bounded tilt (below), at one of eight pickup palm yaws (45-degree
  grid). It is fixed from corridor entry to exit.
- **Contact point.** The selected top washer's ring at its mid radius
  (9.375 mm from its centre), on the side facing the cup axis.
- **Path.** The fingertip enters 120 mm above the rim and descends vertically
  in keyframes at most 10 mm apart (15 knots, u = 0.06-0.20). It dwells at
  contact and attaches kinematically, then rises back along the same line
  (u = 0.24-0.38).
- **Transport (u = 0.38-0.64).** See *Transport reorientation* below. Placement
  constrains the full washer pose; release is at u = 0.70 and the fingers return
  to neutral only after release.
- **Grasp physics.** Not claimed. The attachment is a kinematic visualization,
  with `grasp_contact_validated` and `collision_reviewed` still false.

**Declared envelope and margin.** The model uses the frozen five-box 4 mm cup
shell and three declared shapes:

- **Finger capsule.** Radius 12 mm along the straight middle finger. It runs from
  the fingertip at palm-local (0.17059, -0.0016, -0.0285) m up to palm x =
  0.095 m. The tip comes from the URDF middle_0 and middle_1 origins plus the
  pinned middle_1 pad vertex.
- **Rest of the hand.** The half-space palm x <= 0.095 m.
- **Reach sphere.** 0.19 m around the palm origin.

Required clearances:

- **Wall margin (5 mm).** Applies to the capsule and the half-space against every
  wall box, at every sample where the pad posture is held.
- **Carried washer (>= 1 mm).** Applies to its 64 outer-ring build_usd vertices.
- **Other washers.** The capsule must not intersect the bounding boxes of the
  other 11 supply washers. The finger may touch only the selected washer's top
  face.
- **Reach sphere (5 mm).** Must be clear at corridor entry and exit, and at
  every sample where the pad posture is not held.

The planner checks each corridor keyframe, then the straight segments between
keyframes at <= 1 mm spacing, then (new) the joint-interpolated path the runtime
will actually command from corridor entry to release, evaluated through the
backend and subdivided until no declared hand point moves more than 1 mm
between rows. It refuses any variant that violates a margin (`Supply-cup
clearance below declared margin`) or a carry limit (`Supply-cup carry check
above declared limit`). A cup too narrow for the margin is refused. Where the
whole reach sphere clears every wall by the margin and every other washer, the
finger, body and neighbour checks are implied by it and are not re-evaluated.

**Synthetic results.** With synthetic Cartesian IK, every one of the 300 samples
for all four trays meets these limits:

| Check | Clearance |
|---|---|
| Finger capsule to wall | 9.63 mm |
| Rest of hand above rim | 55.6 mm |
| Carried washer to wall | 2.50 mm (the frozen layout gap) |
| Finger to other washers | 2.92 mm |
| Reach sphere outside pad posture | >= 14.3 mm |

The finger goes below the rim only inside the corridor, and stops on the washer
face. These are *planned-path* numbers. They are not Isaac link poses, STL
triangles, or forearm/elbow/torso clearance. The capsule radius, body bound and
fingertip extent are declarations from the URDF chain and one pinned vertex,
not measured mesh extents. Isaac interpolates joints between keyframes, so the
actual swept path must be screened from measured frames (procedure below).

**Native preflight (d185b7c, 2026-10-07).** Planned against actual PhysX
Jacobians, ADD_ONE/tray_A, tray_B and tray_C compiled, including the declared
clearance check. ADD_ONE/tray_D did not; all 32 variants (4 palm yaws x 8 washer
yaws) were refused:

| Palm yaw | First failing keyframe | Best residual (position / orientation) |
|---|---|---|
| 0 | u = 0.06, corridor entry | 4.89 mm / 0.0528 rad |
| pi/2 | u = 0.64, full-pose placement on tray_D | 11.8 mm / 0.262 rad (8 washer yaws; worst 208 mm) |
| -pi/2 | u = 0.06, corridor entry | 79.6 mm / 0.836 rad |
| pi | u = 0.06, corridor entry | 215 mm / 1.29 rad |

The limits are 0.5 mm and 0.008 rad. They were not changed. Under palm yaw pi/2,
the corridor entry solved with a position residual of 0.499 mm, just inside the
0.5 mm limit. The pickup corridor is reachable for this yaw, but placing the
washer on tray_D with the pad posture held is not reachable. A repeat preflight
gave a byte-identical `preflight.json`. Neither the margin nor the variant set
was changed to obtain a pass.

### Transport reorientation and bounded lean (maintainer decision, October 2026)

The maintainer decided that only the finger posture must stay fixed while the
washer is attached. The palm may turn once the hand and the carried washer are
clear of the supply cup, with every existing check still enforced. The redesign
(`isaac.demos.planner`, `grip_geometry`):

- **What the d185b7c plan actually fixed.** The old plan already let the palm
  yaw change after the corridor exit: the u = 0.50 keyframe constrained only the
  washer position, and the u = 0.64 placement yaw was the pickup yaw plus the
  washer yaw. The binding constraint was different. Under the rigid attachment,
  the palm's lean relative to the washer is fixed at contact, so a level washer
  at release needs the pickup's lean. A purely fingers-down pickup therefore
  forces a fingers-down placement, and no fingers-down placement on tray_D was
  reachable (both development probes below: 0/16 screened placements, best
  16 mm). A turn about the vertical alone cannot change that.
- **Bounded lean.** The pickup palm may lean from fingers-down by 0.15 or 0.30 rad
  (`MAX_PICKUP_TILT_RAD = 0.30`) about a palm-local axis perpendicular to the
  finger, in eight directions. Fingers-down is always tried first. The fingertip
  path in the cup stays vertical, and every corridor check applies unchanged.
- **Transport.** From the corridor exit (u = 0.38) to 120 mm above the
  destination (u = 0.50), the carried washer moves in keyframes at most 30 mm
  apart. Meanwhile the palm turns about the world vertical by the washer yaw, in
  steps of at most pi/16. The keyframes then descend vertically, at most 30 mm
  apart, to placement (u = 0.64). A turn about the vertical keeps the lean and
  keeps the washer level. Every transport keyframe constrains the full pose at
  the washer centre, so placement error is bounded by the IK limits at the washer.
- **Clear of the cup before turning.** Inside the corridor (entry to exit), the
  palm orientation must stay within 0.02 rad of the pickup orientation. After the
  exit, every sample must have the whole-hand reach sphere at least 5 mm from
  every wall, and the carried washer at least 5 mm outside the cup's whole
  volume. These are the new `transport_reach_m` and `transport_carried_m`
  checks. The finger, body, carried-washer and other-washer checks still apply.
- **Attachment under turning.** The checks follow the existing attachment model
  (carried pose = palm x grip, rigid). At every attached sample the carried pose
  must match that model (planned: 1e-7 m / 1e-7 rad; measured frames:
  0.5 mm / 5 mrad). The fingertip must stay within 1 mm of its planned contact
  point on the washer face. The washer must stay level within
  `CARRIED_TILT_LIMIT_RAD = 0.05`; release still requires 0.012 rad.
- **Variant search and headroom.** For each lean in order, the planner screens
  the corridor-entry IK for all eight pickup yaws. The residual is driven to 20 %
  of the limits, so it measures headroom rather than stopping just under
  0.5 mm. For the two best pickups, it then screens the full placement pose for
  all eight placement yaws. Pairs are ranked by headroom tier (>= 0.8, >= 0.5,
  else), then by the smallest transport turn. Up to four pairs per lean are
  compiled in full. Entry screens are deterministic, use a target-independent
  seed label and are reused across trays. Transport keyframes are seeded with the
  previous keyframe, then the joint interpolation from the exit to the screened
  placement solution. The IK limits (0.5 mm, 0.008 rad), the margins and the
  layout are unchanged.
- **Float32 readback.** PhysX quaternions are float32. `2*acos(|a.b|)` turns a
  norm error of about 1e-7 into milliradians. The carry checks therefore use
  `geometry.rotation_angle`, which normalizes both quaternions first.

**Development probes (not the procedure's preflight).** Two ADD_ONE-only probes
ran the uncommitted planner on the Isaac host. They used the same image,
isolation and archive verification as the procedure. Only the `--demo-preflight`
hook was replaced, and they used `--reset-cycles 10`. The GPU was shared, with
a median of 97-100 % from other users' load.

| Probe (source) | Outcome |
|---|---|
| dev1 (`58e1bb7` working-tree snapshot) | **tray_D infeasible.** Lean 0: 2/8 entries feasible, with 0.091-0.098 mm residuals (the d185b7c entry was 0.499 mm), and 0/16 placements feasible. Most lean-0.15/0.30 pairs were refused at contact (u = 0.20-0.25) by the new attachment check, which reported 0.5-2.2 mrad between identical rotations. This was the float32 artefact above, not a carry error. The others were refused for genuine reasons: one IK failure in the corridor, and lean-0.30 pairs with the finger 2.7 mm from the wall. Stopped after 8075 s, during tray_A. |
| dev2 (`45b99f5`, artefact fixed) | **tray_D feasible.** Pickup yaw -3pi/4, lean 0.30 rad toward pi/4, washer yaw -pi/4. Corridor entry 0.091 mm / 0.0004 rad (headroom 0.82); placement 0.37 mm (headroom 0.26). Swept joint path (1265 rows) minima: finger 9.64 mm, rest of hand 44.6 mm, carried washer 2.30 mm, other washers 0.77 mm, transport reach sphere 70.0 mm, carried washer outside the cup 114 mm. Maxima: carried tilt 5.2 mrad, contact offset 0.34 mm, corridor orientation 3.6 mrad. Largest transport joint step 0.36 rad. 21 earlier pairs were refused, mostly at transport keyframes (IK). tray_A at lean 0 had 4 screened pairs, but all 4 were refused at transport keyframes. The transport seeding toward the screened placement was added for this. Stopped after 3501 s. |

These probes are development evidence only. The procedure's preflight on the
committed revision is recorded under *Native run record*.

**Not covered:**

- REMOVE_ONE's release into the return cup still uses the legacy virtual grip
  and is outside this model. The mesh screen accepts `--cup return_cup` for a
  native check.
- The retained pinch probe is unchanged failed evidence.
- No layout change was made.

## Native validation and re-record procedure (d185b7c: stopped at step 2; a30e950: steps 2-6 passed, step 7 pending)

Run this only when the shared Isaac host is free. Use the approved isolated
runtime from `isaac/scenes/README.md`: pinned image, `--network none`, source
read-only at `/work`, assets at `/assets`, and a new `/results` directory. No
integration overlay is needed because `isaac.demos`, `isaac.reset` and
`isaac.commands` are in this branch.

1. Check out this PR's head commit at `/work` and record `git rev-parse HEAD`.
2. Run the preflight:

   ```
   /isaac-sim/python.sh /work/isaac/workcell/run_scene.py --headless --reset-check --skip-reach --demo-preflight --output /results/<new-run>-preflight
   ```

   All 32 pairs must be feasible in `demo-check/preflight.json`. ADD_ONE
   refusals keep their IK or `Supply-cup clearance` reasons under
   `prior_planning_failures`/`error`. Do not relax the margin to pass.
3. Run the full suite:

   ```
   /isaac-sim/python.sh /work/isaac/workcell/run_scene.py --headless --capture --reset-check --skip-reach --demo-check --output /results/<new-run>
   ```

   It records 40 NDJSON files unpaced on the fixed schedule, with no sleeps.
4. Check `demo-check/schedule-validation.json`:
   - `measured_physics_dt_seconds` is 1/60.
   - `refusal` is null.
   - `suite.identical_physics_steps` is 600.

   Every index row must have `capture.schedule_ok`, `replay_end_state_ok` and
   `reset_ok`. `recording_complete` is true only if all of these hold.
5. Check `demo-check/cup-clearance.json`. For each ADD_ONE item (one orientation
   and four executions), `declared_envelope_on_actual_palm.ok` must be true and
   `violations` must be empty. List any `uncertified` samples. They are decided
   in step 6.
6. Run the pinned-mesh screen offline on each corridor file:

   ```
   python -m isaac.demos.grip_geometry --summary /results/<new-run>/demo-check/cup-clearance-NNN.private.json --layout apparatus/workcell_layout.json --mesh-directory <approved-pinned-G1-meshes> --all-hand-meshes --margin 0.005 --out <private>/cup-mesh-NNN.json
   ```

   It must report `margin_ok: true`, with no `cup_vertex_intersections` and no
   `below_margin` rows. Vertex sampling is not triangle distance, so also
   review the close-up captures.
7. Visually review all 40 sequences: reach, self-collision, prop crossings,
   intelligibility, and the forearm near the cup. Keep every failed capture.
   Only then update `demo-diagnostics.json` with the new sanitized counts and
   hashes, and revisit the timing-screen and `recording_complete` statements.
8. Update the Unity #62/#66 consumers to the new capture fields and
   sample-index host pacing before any orientation or fallback use.

### Native run record (2026-10-07, d185b7c)

The host was shared, not free. Other users' processes kept the GPU at 81-83%
before launch, and at 76-99% (median 91%) during each run. They were not
touched. Contention affects wall time only. The source was a `git archive` of
`d185b7c42d79e09c832beda44307b35979ca81b9`, with its embedded commit id and all
2059 files byte-verified on the host before each launch. The runtime was the
approved rendered-cache image (`sha256:38495e05...`, Isaac Sim 5.1.0) with the
isolation flags above. Both containers were removed after exit. Sanitized
hashes are in `demo-diagnostics.json` under `native_validation_d185b7c`. Raw
outputs stay private on the host.

| Step | Outcome |
|---|---|
| 1. Source | `d185b7c` (archive; no `.git` in `/work`) |
| 2. Preflight | **31/32 feasible: ADD_ONE/tray_D infeasible** (grasp-plan section above). 1000/1000 reset cycles and the planning reset passed; the scene and reset snapshot hashes were identical across both runs. A repeat run gave a byte-identical `preflight.json`. |
| 3. Full suite | **Not run.** Step 2 requires 32/32, and `run_demo_check` refuses to record otherwise. |
| 4. `schedule-validation.json` | Not produced. `identical_physics_steps`, `schedule_ok`, replay and reset counts: none. |
| 5. `cup-clearance.json` | Not produced. |
| 6. Pinned-mesh screen | Not run: no corridor frames. |
| 7. Visual review | Not started (human). |

`recording_complete` stays false. The retained suite (4/40 under the earlier
host-span screen) is still the only recording evidence. The fixed sim-step
schedule and the supply-cup corridor still need a native recording, and that
waits until ADD_ONE/tray_D is resolved. This run does not change the plan, the
margins or the IK limits. (Superseded by the `a30e950` run below; this record
is kept unchanged.)

### Native run record (a30e950; 2026-10-08 and 2026-10-10)

The source was a `git archive` of `a30e9508fcb3a24b303e9a126bb48f7fb89187ef`
(archive hash reproduced locally), with its embedded commit id and all 2059
files byte-verified on the host before each launch. The image and isolation
were the same as for `d185b7c` (`network_interfaces: lo` only). Both containers
were removed after exit. No planner change was made: the margins, IK limits,
variants and layout are those of `a30e950`. Sanitized hashes are in
`demo-diagnostics.json` under `native_validation_a30e950`. Raw outputs and a
private evidence archive stay on the host.

- **preflight-001 (2026-10-08).** Launched by an earlier session that was
  interrupted before it finalized. The exited container was inspected and
  removed on 2026-10-10. It ran to completion (exit 0, 5795 s) and is a valid
  step 2 of `a30e950`. Another user's process was on the GPU at launch.
- **full-001 (2026-10-10).** The host was otherwise idle (0 % GPU before
  launch). Exit 0, 3220 s. Its planning pass repeats step 2. `--capture` loads
  the canonical camera-bearing workcell (scene `3b6e8f9a...`, snapshot
  `e2628102...`). The preflight-only run loads the camera-free scene
  (`7a13f7b5...`, snapshot `4e3d9890...`).

| Step | Outcome |
|---|---|
| 1. Source | `a30e950` (archive; no `.git` in `/work`) |
| 2. Preflight | **32/32 feasible**, in both runs, with byte-identical `preflight.json`. Planning reset ok; 1000/1000 reset cycles in each run. |
| 3. Full suite | **40/40** recordings complete, 300 frames each (12,000 public v2 frames). 40/40 executions ok, replay end states ok and following resets ok. Protected real factory: 32/32 rejected, state unchanged, factory not run. 32 execution capture sets (224 images). |
| 4. `schedule-validation.json` | Measured dt 1/60 s, `refusal` null, `suite.identical_physics_steps` 600 over 40 recordings. Every row has `schedule_ok`, `replay_end_state_ok` and `reset_ok`, so the private index has `recording_complete=true`. Unpaced capture took 9.74-10.98 s per recording (provenance only). |
| 5. `cup-clearance.json` | 5/5 ADD_ONE items `ok`, `violations` empty. Uncertified: tray_C samples 268-277 and tray_D samples 260-268 (below). |
| 6. Pinned-mesh screen | 5/5 `margin_ok: true`: no `cup_vertex_intersections`, no `below_margin` rows (eight right-hand meshes, 5 mm). |
| 7. Visual review | **Not started (human).** The material is collected; review flags are below. |

**Selected ADD_ONE variants.** The fingers-down pickup was again infeasible for
tray_D (lean 0: 2/8 entries, 0/16 placements). Every tray used a leaning pickup.

| Pair | Pickup yaw, lean | Washer yaw | Entry / placement residual | Refused before |
|---|---|---|---|---|
| tray_A | pi, 0.15 rad toward 3pi/4 | 0 | 0.091 mm / 0.488 mm | 13 |
| tray_B | pi, 0.15 rad toward 3pi/4 | -pi/4 | 0.091 mm / 0.053 mm | 14 |
| tray_C | -3pi/4, 0.15 rad toward pi/4 | 0 | 0.053 mm / 0.004 mm | 9 |
| tray_D | pi, 0.30 rad toward 0 | -pi/4 | 0.093 mm / 0.107 mm | 19 |

The refused variants failed at transport IK, at the carried-tilt limit or at a
declared clearance, or their lean had no screened pair. Each reason is kept in
the private plans.

**Clearances.** The first value is from the planned swept joint path, the
second from the measured palm poses. Values for trays A / B / C / D, in mm:

| Check | Planned (swept) | Measured |
|---|---|---|
| Finger capsule to wall | 7.21 / 7.21 / 9.61 / 9.58 | 7.21 / 7.21 / 9.62 / 9.59 |
| Rest of hand | 50.4 / 50.4 / 49.9 / 44.7 | 50.4 / 50.4 / 49.9 / 44.7 |
| Carried washer to wall | 2.46 / 2.46 / 2.47 / 2.48 | 2.47 / 2.47 / 2.47 / 2.48 |
| Finger to other washers | 3.53 / 3.53 / 1.88 / 0.82 | 3.53 / 3.53 / 1.88 / 0.82 |
| Transport reach sphere | 101.9 / 8.08 / 96.0 / 80.3 | 102.2 / 8.08 / 96.4 / 80.3 |
| Carried washer outside cup | 114.0 / 75.9 / 114.0 / 113.9 | 114.3 / 75.9 / 114.2 / 114.1 |
| Max carried tilt (mrad) | 8.2 / 28.7 / 3.2 / 36.2 | 8.2 / 27.9 / 2.7 / 28.8 |

**Review flags for step 7.** These pass every automated check. They are listed
because the checks do not establish them:

- **tray_D wrist turn.** `right_wrist_yaw_joint` turns 2.73 rad between two
  adjacent transport keyframes (u = 0.4883-0.5017, about 0.13 s of the 10 s
  display), with the washer attached. The swept-path checks passed across it.
  The largest transport joint step on the other trays is 0.65 rad.
- **Return near the cup.** After release, on the return to neutral, the palm's
  0.19 m reach sphere meets the cup: tray_C samples 268-277 (clearance down to
  1.43 mm) and tray_D samples 260-268 (overlap up to 10.7 mm). These are the
  `uncertified` rows. The eight hand meshes in those rows clear the walls by at
  least 5 mm. The corridor frames hold only hand links, so wrist and forearm
  clearance near the cup is not screened.
- **tray_A placement.** The residual is 0.488 mm, inside the unchanged 0.5 mm
  limit with little headroom.
- **Arm swing.** In all ADD_ONE plans, `right_shoulder_pitch_joint` moves about
  2.8-3.7 rad from neutral to corridor entry (u = 0-0.06), and back after
  u = 0.82.

`collision_reviewed` and `grasp_contact_validated` remain false. The library is
not qualified until the human review (step 7) is recorded. The Unity consumers
(step 8) still need the new capture fields and sample-index pacing.
