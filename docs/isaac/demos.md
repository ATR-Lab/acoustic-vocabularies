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
The actual-G1 feasibility and all-pair visual collision review are pending.

Every execution starts from verified neutral, returns the robot to neutral and
leaves its intended object consequence. The following explicit reset restores
the complete neutral snapshot. This follows the issue's Intended behavior; it
does not silently erase the consequence before the viewer can observe it.
Cancellation leaves the current visible state until the dispatcher's explicitly
logged reset. No result is a semantic response score.

## Fixed sample and fixed sim-step recording contract

> **Status: software only, not yet re-recorded on Isaac.** The contract below
> was implemented and unit-tested on Windows while the GPU host was unavailable.
> The only actual evidence is still the retained suite described under
> *Bounded grip repair diagnostic*: 4/40 captures passed the earlier host-span
> timing screen and its index keeps `recording_complete=false`. Neither number
> changes until the native procedure at the end of this page is run.

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

## Supply-cup grasp plan (software only; not yet run on Isaac)

The frozen layout leaves 2.5 mm between the outer supply washers and the cup's
inner walls, and 3 mm between neighbouring washers. No lateral pinch of a supply
washer fits, which is what the probe and the orientation screen showed. The
planner's ADD_ONE pickup therefore uses a different plan (`planner._compile_one`,
geometry in `grip_geometry`):

- **Posture.** Right Dex3 hand with a straight middle finger
  (`middle_0 = middle_1 = 0`), a curled index finger (1.55 / 1.50 rad) and the
  thumb straight along palm +y. All values are within the pinned URDF limits.
- **Orientation.** Fixed fingers-down palm orientation (palm +x to world -Z),
  with four bounded palm yaws tried in turn.
- **Contact point.** The selected top washer's ring at its mid radius
  (9.375 mm from its centre), on the side facing the cup axis.
- **Path.** The fingertip enters 120 mm above the rim and descends vertically
  in keyframes at most 10 mm apart (15 knots, u = 0.06-0.20). It dwells at
  contact and attaches kinematically, then rises back along the same line
  (u = 0.24-0.38).
- **No finger motion while attached.** Posture and orientation are fixed from
  entry to exit. The posture is held through the transfer and release at
  u = 0.70; the fingers return to neutral only after release. Placement still
  constrains the full washer pose.
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

The planner checks each keyframe, then the straight segments between keyframes
at <= 1 mm spacing, and refuses any variant that violates a margin (`Supply-cup
clearance below declared margin`). A cup too narrow for the margin is refused.

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

**Not covered:**

- REMOVE_ONE's release into the return cup still uses the legacy virtual grip
  and is outside this model. The mesh screen accepts `--cup return_cup` for a
  native check.
- The retained pinch probe is unchanged failed evidence.
- No layout change was made.

## Native validation and re-record procedure (pending GPU host)

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
