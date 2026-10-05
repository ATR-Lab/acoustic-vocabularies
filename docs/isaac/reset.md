# Neutral snapshot and deterministic reset

Phase 2 development was explicitly authorized using the G1 in Isaac Sim. This
implementation does not resolve the unavailable read-only methodology or freeze
pilot tolerances. Issue #53 supplies the engineering contract.

`isaac.reset.ResetManager` restores the complete versioned snapshot, verifies an
independent readback, and opens `exposure_ready` only after the reset event has
been durably logged. Any mismatch closes that gate. Merely observing a corrected
state never reopens it: a new explicit, successful `reset()` is required. There
is no automatic reset retry and no target/action argument to the reset path.
Restore and verification enforce the owning simulation thread; network workers
must queue commands to it. This prevents concurrent writes during verification
or durable logging. The exposure gate is read-only to callers.

## Snapshot and startup

`isaac/snapshots/neutral.schema.json` defines the portable JSON format. It covers
all 43 canonical joints and velocities, fixed-base root pose/velocities, every
registered workcell object and its pose/velocity/visibility/enabled/collision
flags and closed discrete state, material/light authoring values, and measured
rigid-body frames including the head and both palms. Card faces, arrows, lid
fractions, tags and location anchors are restored; washer counts follow the
complete washer inventory and exact location fields.

Appearance coverage is the registered workcell material colors/roughness and
fixed workcell dome-light colors/intensity. It does not read or restore the
robot asset's MDL shader inputs or per-mesh visibility. The private command API
does not expose those robot appearance properties, but arbitrary external stage
edits to them are outside this accessor's verification guard.

The #52 registry independently validates the exact object IDs and per-object
state keys. Snapshot comparison rejects missing or additional objects, changed
joint ordering, environmental property changes and missing measured frames.
Unknown keys, nonfinite numbers, non-unit quaternions and nonzero neutral
velocities are rejected. The scene fingerprint binds the snapshot to the scene
and its asset/layout configuration; loading the same snapshot into a different
scene fails before state writes.

Create the neutral once using `IsaacResetAdapter.prepare_neutral()` followed by
`capture(adapter)`. Review its observer image and gaze/hand direction before use
with protected trials. Write `snapshot_bytes(value)` once to a new file. Record
its SHA-256 as `reset_snapshot_sha256` in the apparatus manifest. Production
startup must call `load_snapshot(path, expected_sha256)` using that configured
hash, then instantiate `ResetManager`; it must never recapture to bypass a hash
mismatch. Files use canonical UTF-8 JSON, LF and a final newline. The manager
also verifies the canonical digest, so noncanonical serialized snapshots are
rejected even if someone separately hashes their raw bytes.

## Simulator method and proposed tolerances

The adapter writes root state, joint state, position/velocity/effort targets and
all object values directly. It performs exactly the configured number of
kinematic forward frames (default one), then reads PhysX tensor views directly.
It does not verify against position target buffers. `SimulationContext.forward`
updates articulation kinematics and rendering fabric without physics integration
([Isaac Lab API](https://isaac-sim.github.io/IsaacLab/v2.3.0/source/api/lab/isaaclab.sim.html)).
Simulation time therefore does not advance during this reset. This is an
explicit engineering refinement of the issue's proposed fixed-frame method:
stepping dynamics after a zero-velocity write can introduce gravity-driven
motion. It is not a settling loop. The protected hold/publisher must continue
checking neutral state; a subsequent dynamic step is not assumed neutral.

Proposed tolerances are 0.5 degrees for every joint and observed orientation,
1 mm Euclidean position error, exact discrete state/flags, 1e-5 m/s linear
velocity, 1e-5 rad/s joint/angular velocity, and 1e-7 absolute environmental
authoring values. The velocity/environment limits are additional engineering
proposals. Quaternions `q` and `-q` are equivalent. Every observed body frame is
checked, including head and hand frames. Pilot validation and methodology review
remain required; these values are not claimed to be frozen thresholds.

`reset()` returns `reset_ok`, individual failures, worst error by unit,
`sim_time`, host-monotonic verification completion, elapsed verification time and
snapshot SHA-256. The runtime benchmark additionally measures the full call,
including durable log I/O. A sink failure raises and leaves the exposure gate
closed, so no successful reply can escape a logging failure.

## Logging and publisher handoff

`DurableResetLog` creates a fresh JSONL file, appends one event per reset attempt,
flushes and fsyncs before returning. Existing or interrupted files are preserved.
The ADR-007 common envelope carries explicit apparatus/protocol/clock versions,
opaque local session ID, event sequence, host monotonic time, simulation time,
and typed reset payload. This engineering extension uses schema version 0.2.0
and `neutral_reset`/`reset_fault` event types; it does not claim compatibility
with unavailable private logging templates. Supply an explicit unresolved
protocol label until those templates are validated.

The #54 publisher may call `verify_state(sampled_state)` on an already acquired
full sample, or `verify_current()`. A protected-mode mismatch must suppress the
public frame and report `NEUTRAL_DIVERGED` on the separate health/control path.
Reset metadata, private command arguments and mode must not enter public state.
The session engine must require both a successful reset acknowledgement and the
current exposure gate before enabling sound; that engine is a later issue.

## Reproduction and evidence

Run pure tests with `python -m pytest tests/isaac/test_reset.py -q`. These include
1,000 deterministic randomized synthetic perturb/reset cycles, all 43 joints,
object/discrete/appearance changes, locked-joint failure, snapshot/scene hash
mismatch, quaternion sign, missing objects/frames, malformed values, a synthetic
32-step demo sequence, and durable-log failure. Synthetic results are not
simulator measurements.

Inside the isolated #52 scene, construct
`IsaacResetAdapter(robot, accessors, sim, scene_sha256)` and invoke
`run_reset_check(adapter, fresh_output_directory, cycles=1000)` from
`isaac.reset.benchmark`. It explicitly prepares neutral, captures/hashes it,
perturbs every actual joint and object, resets and records per-cycle deviations
and full-call timing. The final deliberately wrong joint write must fail; an
explicit separately logged recovery follows. The output contains a snapshot,
JSONL, CSV, optional observer PNG, hash manifest and summary. Original raw logs
and PNG remain local while LFS is blocked. Sanitized numeric evidence may be
published; never substitute synthetic timings for actual measurements.

The corrected-label workcell was measured with reset source `f319440` and scene
source `8802e2e`. All **1,000 actual perturb/reset cycles passed**. Full-call
latency including durable logging was median **140.562 ms**, p95 **145.539 ms**,
maximum **557.445 ms**. Maximum position deviation was 1.1921e-7 m; measured
joint, orientation, velocity and environment deviations were zero. The injected
wrong joint write was rejected, followed by an explicit successful recovery.
All 1,002 terminal events, event ordering, manifest hashes and CSV statistics
were independently checked. Root state was restored/verified but not randomized
because this is a fixed-base articulation.

The portable measured file is `isaac/snapshots/neutral_v1.json`, SHA-256
`e2628102a9a85dfc7566052297e0b038cdf4aa4025319d0e868425cecbb1a80e`, bound to scene
`3b6e8f9a3572c6c1b67e9bb406697745d6304fb97ec1138ef955f177e5ee119e` and layout
`173048c7109741b3c8b3e34e9166a480e17b72fcc3b60f15a1edacba482879eb`.
`docs/isaac/reset/actual-reset-results.json` records provenance, coverage and raw
evidence hashes; `docs/isaac/reset/reset-cycles.csv` contains all numeric rows
with only line endings normalized. The observer PNG was inspected and remains
local while LFS is blocked. This confirms visible head/hands and the workcell;
it does not substitute for methodology review or gaze/hand neutrality sign-off.

The previous label layout also passed 1,000 cycles, but its scene-bound snapshot
is superseded. No old snapshot was relabeled as evidence for the current scene.
Actual #56 demonstration sequences, private methodology, gaze neutrality review
and final pilot tolerance approval remain pending. Protected hold while physics
advances is measured separately by #55.

The later comparator lifetime correction moves the recursive comparator to a
module function, with the same explicit failures, worst deviations and tolerance
values. This avoids a nested recursive closure retaining its manager/adapter
until cyclic collection. Twenty reset tests pass, including immediate manager
release and exact failure ordering. An independent synthetic comparison of 500
valid/invalid states produced identical status, failure ordering and worst values
without mutating input (result digest
`0cf481fb5895d8a8d000646d94c4994247e78c58788915dc20408297f4ff7834`).
The allocation probe observed one self-cycle per initial call before the change
and none afterward, with GC enabled and its thresholds unchanged. This is object
lifetime evidence, not a new timing/throughput qualification or a rerun of the
historical 1,000-cycle apparatus result.

The subsequent [bounded actual profile](reset/comparator-profile.json), using
the same cached state reader, observed protected throughput without a receiver
at 44.875 → 47.310 steps/s and with the process receiver at 45.454 → 46.287.
Both remain below 60 steps/s. The short after-run observed no generation-2
collection; it does not establish that long pauses are gone. No full hour or
1,000-cycle reset rerun is inferred from this diagnostic.
