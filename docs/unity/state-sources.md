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
is reached. There is no extrapolation. The provisional interpolation delay is
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
Returning to neutral is an explicit operation.

Reset comparison checks both the newest sample and the currently rendered pose:
43 joints within 0.5 degrees, object positions within 1 mm, object rotations
within 0.5 degrees, and exact visibility/enabled/visual-state values. It is a
sensor-side confirmation, to be paired with #55's distinct private reset reply
by the session engine. Receipt of a command reply alone cannot satisfy it.

Private `state-source.local.json` follows
`apparatus/schemas/state-source.schema.json`. It identifies the generated scene,
exact imported layout bytes, neutral file/hash, interpolation delay and clock
evidence. The neutral filename is a basename in the provisioned private asset
directory; traversal is rejected. The public example has placeholder hashes
and cannot load an actual neutral file.

Source events are durably logged. Per-render receive-age samples go through a
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
source switching on-device and final neutral render comparison remain separate
operator evidence.
