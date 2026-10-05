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
The virtual grip is initially 60 mm below the palm; this is a visible engineering
attachment reference and does not establish fingertip contact. Container lids,
codes and already-attached tags move with their container; free tags stay put.
Arrow angle follows measured wrist yaw, and lid angle follows the measured palm
along its hinge arc. SCAN's printed result stays in the private result/index.

REMOVE_ONE uses a two-hand center transfer. Both measured virtual grip frames
must agree within 3 mm and 0.02 rad before ownership changes. The hands remain
separate; a failure leaves ownership unchanged and reports an execution failure.
No layout change or cross-body reach assumption is hidden in this operation.
The initial actual-G1 feasibility and all-pair visual collision review are pending.

Every execution starts from verified neutral, returns the robot to neutral and
leaves its intended object consequence. The following explicit reset restores
the complete neutral snapshot. This follows the issue's Intended behavior; it
does not silently erase the consequence before the viewer can observe it.
Cancellation leaves the current visible state until the dispatcher's explicitly
logged reset. No result is a semantic response score.

## Fixed sample and recording contract

Orientation and execution both provisionally use **300 samples at 30 Hz** and a
**10 s nominal display duration**. First-to-last sample span is nominally
299/30 = 9.9667 s; playback holds the last frame to the nominal boundary. These
are engineering choices, not approved protocol timing. The command scheduler
must call one demo `advance()` per 30 Hz sample; physics may advance at 60 Hz
between samples. The benchmark uses exactly two physics steps per sample.

Each opaque `000.ndjson` file contains plain public v2 state frames, with actual
host-monotonic sample timestamps, advancing simulation time, all 43 measured
joints and all 60 public object states. No action/target or trial fields enter
these frames. A separate **private** index records group (8 orientation or 32
execution), action/target, SHA-256, registry hashes, expected consequence,
execution outcome, following reset and replay result. The index records both
measured host span and nominal duration. It preserves incomplete captures,
overruns and gaps; timestamps are never replaced or time-compressed. Playback
must use the host-stamp offsets, not simulation time. The provisional timing
screen requires 300 frames, span no more than 10 s, and no gap over 250 ms.

`TrajectoryWriter` flushes/fsyncs on completion or failure. JSON summaries are
written through a flushed replacement. Hash-checked replay validates every
frame, applies recorded joints/props to the actual articulation/USD, and checks
the expected final state before the following reset. Raw files and render
captures remain in a fresh ignored output directory. Do not publish private
indices or raw station information. Public derivative reports must retain all
failures and state the measured revision.

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
they are deliberately excluded from timing evidence. Inspect each sequence for
reach, self-collision, prop crossings and intelligibility. `collision_reviewed`
stays false until that review is explicitly recorded. Grasp/contact accuracy is
outside scope, but visibly impossible paths are not acceptable demonstrations.

Pure tests use a clearly synthetic IK backend to verify all 32 meanings,
attachment transforms, handoff refusal, interruption, matching frame counts,
hashes, clock progression and failed timing preservation. They do not establish
actual robot reach or collision-free motion.
