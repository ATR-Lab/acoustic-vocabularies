# Public workcell publisher

Issue #54 adds a version 2 public rendering contract. It uses the existing
approved `websockets` 12 runtime and a custom WebSocket channel at a provisional
30 Hz development rate (60 Hz is selectable for measurement). This makes the
small public allowlist explicit and reuses the tested Phase 1 implementation.
ADR-002 remains Proposed: network/headset qualification and the final transport
and rate decision are outstanding. The Phase 1 version 1 contract remains
unchanged; old clients must reject version 2 until upgraded by #62.

`state-v2.schema.json` is closed at every wire-object level. Each frame has:

- Protocol version, source provenance (`live` or `synthetic`), logical station
  ID, scene hash, neutral snapshot hash and a random publisher-session epoch.
- Contiguous sequence, host monotonic nanoseconds as a decimal string, diagnostic
  simulation time and simulation step.
- The 43 measured joint names in canonical map order and finite joint positions.
- The full sorted object inventory: stable ID, world position in metres,
  normalized xyzw rotation, visibility, enabled flag and permitted visual state.

Visual state includes card face, arrow angle, lid fraction, tag attachment and
registered physical location. It never includes the private command, requested
target, trial identity, condition, schedule, correctness or scan result. Explicit
projection excludes other simulator fields. The runtime checks the immutable
scene registry as well as types, completeness, exact order and normalized poses.
The station ID grammar matches the Unity foundation; use anonymous logical IDs
such as `station-01` in public examples.

Only the simulation thread reads USD/PhysX. An integer-deadline scheduler offers
one validated frame to a bounded worker mailbox. Missed ticks are counted and
skipped, never backfilled. Slow receivers have a one-frame queue; replacement is
counted. The default experiment policy treats such replacements as a failed rate
screen. Disk logging uses a separate bounded writer and fsync on finalization.
Logs are created exclusively so reruns cannot overwrite evidence.

`/state` accepts only a strict clock echo request. Duplicate JSON keys, nonfinite
values and all command-shaped messages close that public socket with code 1008.
Echo replies return server receive/send stamps for the same client correlation
value. They do not establish clock accuracy without the #47 qualification.
`/health` returns rate, count, last publish time, age, stale flag, client count,
queue replacements and a public fault code. `/health` is advisory and contains
no private session mode or answer material. Commands use #55's separate endpoint.

Transport setup requires either an explicit Unix socket or explicit TCP host and
station port; it does not alter networking. A Unix listener in a network-disabled
container is the development default. Production client authorization, routes
and station isolation are part of #57; do not expose the development listener
to a general network.

Protected publication requires #53's read-only full-state verifier. It compares
joints, body frames, objects, discrete state and appearance with the hashed
neutral snapshot. Failure suppresses the outgoing frame and latches
`NEUTRAL_DIVERGED`; subsequent matching samples do not silently resume the stream.
The current publisher needs an explicit restart after recovery. Reset success
alone must not be represented as publisher recovery. #55 supplies the explicit
robot hold and protected-mode transitions; ordinary gravity-driven dynamics are
not evidence of a neutral hold.

The rate harness advances real 60 Hz physics and publishes against absolute host
deadlines. Its initial configuration is explicitly an **unprotected engineering
rate test**. It reads real articulation/object state, checks each frame's runtime
contract, verifies it again in a local socket collector, and saves first/last
samples for independent JSON Schema validation. This measures paced publisher
timing, not maximum simulator throughput, Wi-Fi latency, headset rendering or
audio onset. A full run needs at least 3,600 measured seconds; shortened tests
can report diagnostic timing but cannot pass the one-hour acceptance screen.
