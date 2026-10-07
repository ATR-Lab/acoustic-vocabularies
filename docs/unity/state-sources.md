# Robot state sources

Issue #62 supplies scene-independent state sources under
`Assets/ExperimentApp/Runtime/StateSources`. The rendering adapter is kept
separate so the scene assembly never needs a trial, target or answer reference.
The station's `robot_state_source` selects `live` or `snapshot`; this value is
recorded in foundation and source log headers.

The live parser implements the closed publisher version 2 contract. It verifies
the station, scene and reset hashes, all 43 names in their measured order, and
the full sorted semantic-object registry. Unknown fields, duplicate keys,
nonfinite values, invalid quaternions, missing objects and wrong identities are
rejected before they reach the rendering source. Returned object state is
copied; callers cannot mutate a buffered frame's discrete state.

Joint coordinates interpolate linearly, positions linearly and rotations
spherically. Coordinates remain Isaac RH Z-up until the workcell adapter's
single conversion. Discrete state uses the earlier sample until the newer sample
is reached. Arrow angle and lid fraction interpolate continuously. A card's
effective visible rotation is interpolated across the recorded release that
transfers its half-turn from the root pose to `card_face`, avoiding a spurious
second turn. There is no extrapolation. The provisional interpolation delay is
two 30 Hz periods (66.7 ms), configurable from 0 to 200 ms. It must be revisited
after a qualified headset path supplies its jitter measurements.

The receiver uses a bounded eight-message queue and a reconnecting
`ClientWebSocket`. No network callback accesses a Unity transform. A correlated
echo is sent once per second; its four timestamps bound clock offset without
assuming symmetric network delays. An independently supported drift bound and
evidence hash are required before a live source can confirm reset readiness.
With no clock evidence the source can be used for engineering preview, but it
cannot authorize a cue. Echo alone is not clock qualification.

The valid-sample receive gap is measured using the host monotonic clock. Above
250 ms it emits one `STATE_STALE` event and holds the last rendered pose.
Recovery emits a separate `STATE_RECOVERED` record with the full gap duration,
measured from the last valid sample. Invalid, replayed, nonprogressing, overly
queued or clock-stale samples do not refresh qualified state. A server restart
clears interpolation history and reset confirmation; retired session epochs
cannot be replayed. Simulation time remains a diagnostic value.

The snapshot source checks the exact neutral-file SHA-256 before parsing and
binds its scene hash, canonical joint names and objects to the rendering
registry. It projects only public visual state from the #53 snapshot. Recorded
trajectories are private NDJSON files with one version 2 live-captured frame per
line. Files are hash checked before use; sequence, session, simulation progress
and host timestamp order must be consistent. Playback uses elapsed host-stamp
offsets, never simulation time. The first frame must match neutral. A completed
trajectory holds its final frame and does not implicitly confirm a reset.
Returning to neutral is an explicit operation. Snapshot validation also checks
the complete #53 field structure, exactly zero commanded velocities, root/frame
quaternions and finite environment values. Internal recorded gaps above 250 ms
are refused. Nonfinite/regressing playback start clocks are refused. An optional
nominal duration holds the last sample through the declared boundary; a capture
that overruns that boundary is refused rather than compressed.

Reset comparison checks both the newest sample and the currently rendered pose:
43 joints within 0.5 degrees, object positions within 1 mm, object rotations
within 0.5 degrees, and exact visibility/enabled/visual-state values. It is a
sensor-side confirmation, to be paired with #55's distinct private reset reply
by the session engine. Receipt of a command reply alone cannot satisfy it.
Any new nonneutral frame revokes a previous confirmation. The source
also revokes sample eligibility after transport, parser, queue or replay
faults. A retained neutral image cannot regain readiness before a new valid,
progressing sample arrives and another explicit reset confirmation occurs.
The active-root
`StateSourceHost` supplies `ConfirmReset()` and `CheckExposureReady()`; the latter
synchronously pumps, ages and applies current state at the actual cue boundary.
Its `ResetConfirmed` property invokes that same fresh check, so a stalled frame
cannot expose an old grant before `LateUpdate`. Tracking/foundation unavailability
revokes the host's boundary grant; recovery needs another explicit confirmation.

Private `state-source.local.json` follows
`apparatus/schemas/state-source.schema.json`. It identifies the generated scene,
exact imported layout bytes, neutral file/hash, interpolation delay and clock
evidence. The neutral filename is a basename in the provisioned private asset
directory; traversal is rejected. The public example has placeholder hashes
and cannot load an actual neutral file.
Both this file and its neutral asset belong in `Application.persistentDataPath`,
beside `station.local.json`. A live endpoint must explicitly end in `/state`.

Build the integrated scene with `-Scene StateSources -G1Description <verified-description>`.
The builder places the host outside the hidden presentation hierarchy. The
renderer validates the complete frame before changing any transform, converts
coordinates once and refuses unknown objects or out-of-range joints. It checks
the fixed-base root and imported neutral against the captured snapshot. Exact
semantic values remain in the hashed layout; float conversion in Unity is not
used to redefine the neutral state. Initialization, rendering or evidence-writer
failure hides the workcell and prevents readiness. A stale stream holds its last
rendered pose and exposes its fault to the session controller.

Source events are durably logged. Receive-age samples at up to 30 Hz go through a
bounded writer to a private CSV; writer failure or overflow is a fault. These
ages describe local validated arrival, not an independently established
end-to-end network latency. Record the source configuration and clock evidence
alongside the run before interpreting them as apparatus qualification.

The edit-mode suite checks parsing, interpolation, replay, hashes and reset
readiness. An optional test reads actual Isaac snapshot/wire evidence when the
private `STATE_SOURCE_EVIDENCE` directory is supplied. Play-mode tests advance
an injected 90 Hz clock once per actual Unity test frame and check 200 ms,
300 ms and 2-second gaps. Those tests are deterministic fault injections, not
elapsed-time network/headset measurements. The 30-minute headset capture,
source switching on-device and a matched live/headset neutral recording remain
separate operator evidence. The captured #53 neutral is checked against all
43 imported joints and 60 objects in Unity. A synthetic wire projection of that
snapshot also exercises source parity; it is not a measured live-neutral run.

The local engineering result at source `e5bf8a0` has 90 passing edit-mode and
three passing play-mode tests, plus Windows and Android builds with zero errors.
Meta XR Simulator exposed a delayed startup-origin notification; #60 now waits
for a stable acknowledged Device origin before showing the scene. The revised
app showed the G1/workcell, latched focus loss, and refused a configured snapshot
hash mismatch. The simulated headset-input toggle did not produce `tracking_lost`,
so that injection remains inconclusive. The first diagnostic required a forced
stop after native shutdown stalled; subsequent runs exited normally. See the
[sanitized evidence](state-sources-validation.json) and the simulator section of
the [runbook](../spikes/O5.3.4-runbook.md) for scope and remaining checks.

An actual protected Isaac stream subsequently drove the same Windows binary in
Meta XR Simulator. Its retained first and last public frames passed a separate
Unity test against the captured neutral and imported renderer (43 joints and
60 objects). This checks actual recorded pose parity without manufacturing a
clock qualification or reset acknowledgment. The Simulator displayed the workcell;
application-specific screenshot capture failed with a foreground-process error.

This preview did **not** pass performance qualification. The backend ran for
300.009159 seconds with 6,082 published frames and 2,918 missed deadlines, without
a neutral-verification fault. The Windows preview retained 3,940 sample-age rows
over 187.613259 seconds, including 1,117 stale rows, two receive-queue overflows,
249 queued-too-long events and 12 stale events. That window includes startup and
the natural stream shutdown under concurrent development load. Local receive age
is not cross-host latency. Every sampled reset grant stayed false; natural stream
termination logged disconnects and stale state. The app closed normally and the
owned diagnostic relay/tunnel were stopped. Failed timing evidence remains retained.

The later lifecycle revision `f9957fd` passes 93 edit-mode tests and fresh Windows
and Android builds. Four play-mode tests pass with the new host-disable regression:
disabling an initialized host immediately removes its grant, hides the workcell,
closes the source and latches `STATE_HOST_DISABLED`. Re-enabling cannot restart it.
Foundation readiness also requires an active component and explicit operator
recovery after disable. Simulator observations above retain their original binary
identity; these newer builds were not used to retroactively label those runs.
