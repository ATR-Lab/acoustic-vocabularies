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

## Actual revised-scene run

The completed 3,600.012913-second run published and locally received 107,917
actual frames with contiguous sequence and advancing simulation. Both retained
samples passed independent JSON Schema validation, and the CSV summary was
independently recomputed. The strict timing/rate result is **FAIL**: 84 missed
deadlines and 3.575965 ms p99 absolute period error exceed the zero-miss and
3.333333 ms error limits. Median interval was 33.340162 ms; maximum gap was
45.270001 ms. There were no queue overwrites or gaps above 250 ms.

The separate 120.015493-second unpaced disconnect diagnostic also **FAILS** its
5% screen. Physics throughput was 83.033333 steps/s without a client,
74.066667 connected, 83.333333 disconnected and 74.3 reconnected. The initial
connection reduced throughput by 10.798876%; reconnect remained 10.517864%
below the initial baseline. No main-loop client wait occurred, but the local
receiver and Python worker share the process, so this run cannot isolate their
individual costs. No threshold was relaxed and no failed run was discarded.

The [sanitized measured record](publisher/actual-hour-results.json) retains
metrics, phase comparisons, exact scene/snapshot binding and raw-artifact hashes.
This was an unprotected engineering workload on the documented Ubuntu 24.04
deviation. It does not qualify neutral hold, network clocks, headset rendering,
audio or G2. Protected streaming and the Windows receiver are measured separately.

## Frozen publisher with a separate diagnostic receiver

The second completed run changed only the strict diagnostic receiver from a
thread in the simulator process to a separate process. Publisher protocol,
runtime, transport and analyzer bytes matched the original run. The scene,
snapshot, 30 Hz deadline policy, 60 Hz physics and qualification limits stayed
fixed. Neither the experimental workcell handle cache nor the later reset
comparator refactor was present. The receiver still validated every complete
frame and checked sequence continuity.

| Measurement | Original receiver thread | Separate receiver process |
|---|---:|---:|
| Measured duration (s) | 3600.012913 | 3600.013084 |
| Published and received frames | 107917 | 108001 |
| Missed deadlines | 84 | 0 |
| P99 absolute period error (ms) | 3.575965 | 6.069881 |
| Maximum gap (ms) | 45.270001 | 43.906904 |
| Queue overwrites / gaps above 250 ms | 0 / 0 | 0 / 0 |
| Strict timing/rate screen | FAIL | FAIL |

The rerun completed 216,002 actual physics steps and received all 108,001 frames
with no sequence gap or publisher fault. Independent CSV analysis reproduced
the summary, and both retained samples passed JSON Schema validation. Its p99
absolute period error still exceeds the unchanged 3.333333 ms limit. Zero missed
deadlines does not override that failure. Sequential runs include background
workload variation, so the table does not establish a causal jitter improvement.

The separate 120.020712-second **unpaced disconnect diagnostic passed** all five
5% comparisons. Its no-client, connected, disconnected and reconnected phases
measured 82.833333, 80.266667, 83.166667 and 80.6 physics steps/s. Initial connection
was 3.098592% below baseline; reconnect was 2.696177% below it. The largest tested
change was 3.612957%. Each connected window received 896 frames with no sequence
gap or receiver error, and the simulation never waited for a client. This pass
describes that measured unpaced workload; it does not qualify protected throughput,
headset/network performance or a paced station deployment.

The additive [receiver-process measured record](publisher/receiver-process-hour-results.json)
retains exact source revisions, runtime source hashes, independent checks and
raw evidence hashes. The original record remains unchanged. The one-hour rate
screen remains **FAIL**, and no acceptance threshold or deployment gate changed.

## Bounded protected-loop diagnostics

The later [flush diagnostic](publisher/flush-diagnostic.json) is engineering
evidence only. It enabled the experimentally guarded workcell handle cache and
the equivalence-tested reset comparator in an isolated copy. Twelve original
before/after flush pairs had bitwise-equal values across 36 requested, simulation,
PhysX and actuator buffers, equal full-state hashes, and passing neutral checks.
All five actuator groups were exact implicit actuators and both wrench composers
were inactive. Generic `write_data_to_sim` is not universally idempotent: other
actuator models have history, and the call applies/consumes external wrenches.

Only the diagnostic's redundant pre-step flush was omitted after an unchanged
completed hold. The first pre-step flush, every hold flush, full live state reads,
neutral checks and default GC remained in place. Per-step checks rejected writer
epochs, buffer/parameter changes, active wrenches or changed actuator identity.
Both comparison sides paid that guard cost. Four 12-second phases measured
42.573 → 44.559 steps/s without a receiver and 41.398 → 44.859 with the separate
receiver; two additional 5-second cProfile phases retained call counts. All
phases and the following reset completed without a neutral fault. The live guard
check alone cost roughly 1.6–1.9 ms per step, so these results are not directly
comparable to prior unguarded profiles.

The earlier projected saving of about 8.6% could not close the 60 steps/s gap,
and neither measured guarded configuration reached that target. No generic
articulation, hold, reset or production publisher implementation was changed.
No optimized hour, station deployment or G2 pass is inferred. The completed
diagnostic container was removed after its evidence was retained; the task GPU
workload was left idle.

The subsequent [typed projection follow-up](publisher/encoder-projection.md)
retains every public-state and neutral check while reducing median encoder work
by about 1.8 ms on the measured full scene. Eight short paired phases still failed
the unchanged timing screen, including a retained 389.889 ms generation-2 GC pause.
The production handle cache remains off. This follow-up does not supersede the
hour failures or qualify protected throughput.
