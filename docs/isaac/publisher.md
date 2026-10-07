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

## Interval jitter reduction

This section records a software change and **synthetic host-only** results. No
Isaac run has used the change yet. Both recorded hour failures stand, and the
p99 criterion is **not** claimed to pass.

### Hypothesis from retained evidence

The rate harness slept to each absolute deadline and then called `after_step`,
which read the state, projected it and took the host stamp inside
`StateEncoder.build`. Each stamp was therefore *deadline + wake-up + public
readback + projection*, and each interval error was roughly the difference
between consecutive readback durations. The retained unprotected stage timers
show public readback at 11.2–11.6 ms median with 15.9–18.6 ms maxima, against
5.2–5.3 ms physics and 2.4–2.6 ms actuator flush per step and about 1 ms of
encoding. A tick is about 28 ms of work in a 33.3 ms period. A 3.33 ms limit
on the 99th percentile is crossed by the ordinary spread of an 11 ms Python
USD read, plus occasional overruns when physics and readback tails coincide.

GC does not explain the hour p99. The p99 is set by about 1,080 of 108,000
intervals, the hours' maximum gaps were 45.3 and 43.9 ms, and the recorded
350–390 ms generation-2 pauses each collected only about 250 objects. Those
pauses were traversals of the large live Kit/USD heap. They threaten the
zero-missed-deadline and 250 ms gap conditions, and they can set p99 in a
12-second phase. The in-process thread receiver adds GIL competition, which
fits the first hour's 84 missed deadlines. The second hour's higher 6.07 ms
p99 with zero misses fits larger readback variance under background load.
The retained raw CSVs can test this directly: in the legacy loop each row's
`serialize_ms` covers the readback, so interval error should track the change
in `serialize_ms` between consecutive rows (see the rerun procedure).

### Change

- `pacing.py` holds integer absolute deadlines (`epoch + i·10⁹ // rate`,
  without float accumulation) and `claim_deadline`, which publishes once for
  the latest due tick and counts skipped ticks without catch-up bursts. It
  also corrects an exact-nanosecond boundary case. `run_paced_loop` is now
  the loop shared by `run_publisher_check` and the synthetic harness.
- **Pre-sampled publication** (`pacing="presample"`, harness default):
  physics, then sample, neutral check, projection, validation and JSON
  encoding (`StatePublisher.prepare`), then sleep to the deadline, then
  `publish_prepared` stamps and hands off. The simulator thread does not
  step or write between sampling and stamping. `publish_prepared` latches a
  fault if the simulation counters changed, so the published state is still
  the current simulator state at the stamped instant. The stamp is spliced
  into the pre-encoded payload. Output is byte-identical to `encode(frame)`
  (randomized test) and to the legacy path at the same stamp. A prepared
  frame consumes no sequence number until it is committed. The wire schema,
  fields, validation, sequence and neutral-check semantics are unchanged.
  `pacing="at_deadline"` reproduces the recorded loop. `after_step` is
  unchanged for every other caller.
- **GC policy** for the measured loop only (`gc_policy`, harness default
  `freeze`). After warm-up and receiver connection, it runs one full
  collection and then `gc.freeze()`. Later automatic generation-2 passes
  traverse only objects that survived since the freeze, and automatic
  collection stays on. Risk: cycles that become garbage among frozen objects
  are not reclaimed until the policy stops. The policy unfreezes on exit
  unless something else had frozen objects first. `freeze_manual` (opt-in)
  also disables automatic generation-2 passes and runs `gc.collect(2)` right
  after a publication every `gc_manual_interval` frames (default 1,800).
  `default` keeps interpreter behavior. Every policy records per-generation
  pause counts, totals and the 20 longest pauses in `metadata.json`.
- The measured window now opens when the first tick's pre-deadline work is
  done. Before, tick 0's deadline came before its own physics, so the next
  several ticks started late. That transient is negligible in an hour but
  can set p99 in a 12–20 s diagnostic. Every later deadline is absolute.
- The publish log gains `deadline_lag_ms` (stamp minus scheduled tick). The
  analyzer reports its median, p99 and maximum for attribution only; the
  screen is unchanged. Metadata records `pacing_mode`, `gc`,
  `overrun_ticks` and `wake_late_ms_max`. `spin_us` (0..2000, default 0)
  optionally busy-waits the final microseconds of a sleep. It stays off on
  Linux, where wake-up is about 0.06 ms and spinning holds the GIL.
- `run_scene.py` exposes `--publisher-pacing`, `--publisher-gc` and
  `--publisher-collector`.

The guarded handle cache from #170 is **not** used. It would shorten readback
(9.6 → 6.5 ms median in its profile) and add slack. With pre-sampling,
readback duration no longer enters the stamp unless a tick overruns. The cache
remains experimental, is wired only into the joined service, and is the next
lever if the rerun shows overruns.

### Synthetic host-only results (not qualification)

`python -m isaac.publisher.host_timing` drives the production
`StatePublisher` and `run_paced_loop` with a synthetic 43-joint/60-object
source. The source's costs are assumptions calibrated to the retained medians:
physics 7.3 ms/step + Exp(0.3); readback 11.0 ms + Exp(0.6); a 1% tail of
+2–6 ms on each; encoding 1 ms. All runs are `source_kind: synthetic`, so the
analyzer cannot pass them. The [derived record](publisher/host-timing-synthetic.json)
keeps parameters, normalized source hashes and raw-summary/CSV hashes.

**Virtual-clock hour** (deterministic, same seed and cost sequence; 0.06 ms
+ Exp(0.02) modeled wake-up; excludes real GC and scheduling):

| Pacing | p50 | p99 | p99.9 | max | Intervals > 3.333 ms | Deadline lag p99 |
|---|---:|---:|---:|---:|---:|---:|
| `at_deadline` (recorded loop) | 0.425 | **4.150** | 6.076 | 8.799 | 1,783 / 108,000 | 15.676 |
| `presample` | 0.014 | **0.150** | 1.917 | 6.082 | 34 / 108,000 | 0.166 |

Milliseconds. The model's legacy p99 of 4.15 ms is close to the first hour's
measured 3.58 ms, which supports the hypothesis without proving it.
Sensitivity (600 virtual seconds each) moves presample p99 to 1.477 ms with
doubled readback jitter, 0.995 ms with a 3% tail, 1.949 ms at 8.3 ms/step and
2.889 ms at 9.0 ms/step. As slack shrinks, overruns rather than readback
spread set the tail, and pre-sampling cannot remove those.

**Isolated GC pause probe** (this laptop, synthetic 1.5M-object live heap,
600,000 surviving allocations): default policy, one automatic generation-2
pause of 98.6 ms; `freeze`, automatic passes with 19.3 ms maximum,
which scale with post-freeze survivors only; `freeze_manual`, no automatic
pass, and an explicit safe-point collection of 27.8 ms. The publish path
itself leaves 0.0 (legacy) and −0.003 (pre-sampled) net GC-tracked
containers per frame (allocation probe, collection disabled), so frames do
not feed generation 2.

**Wall clock** (this laptop, Python 3.12, perf_counter clock, a validating
in-process receiver thread, a 1.5M-object ballast heap, 60 s per run in ABBA
order; another workload was running on the shared host):

| Order | Configuration | p50 | p99 | max | Intervals > 3.333 ms | Missed | Deadline lag p99 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 0 | legacy | 0.516 | 4.838 | 15.587 | 42 / 1,800 | 0 | 15.846 |
| 1 | presample | 0.222 | 1.270 | 2.632 | 0 / 1,800 | 0 | 1.179 |
| 2 | presample + freeze | 0.225 | 0.991 | 19.079 | 5 / 1,800 | 0 | 0.974 |
| 3 | presample + freeze_manual | 0.218 | 1.415 | 8.576 | 6 / 1,800 | 0 | 1.294 |
| 4 | presample + freeze_manual | 0.202 | 1.746 | 5.463 | 5 / 1,800 | 0 | 1.487 |
| 5 | presample + freeze | 0.183 | 2.501 | 17.982 | 11 / 1,799 | 1 | 1.930 |
| 6 | presample | 0.151 | 2.456 | 21.208 | 16 / 1,800 | 0 | 2.614 |
| 7 | legacy | 0.552 | 5.127 | 21.231 | 58 / 1,800 | 0 | 16.709 |

Pre-sampled runs had lower p99 than both legacy runs, but the spread between
repeats of the same configuration is as large as the differences between
policies. No automatic generation-2 pass occurred in any wall run, and sleep
wake-ups reached 16.5 ms late. These runs show that pre-sampling removes the
readback-driven lag (legacy lag p99 about 16 ms, the readback itself). They do
not rank the GC policies or predict the Linux host. The in-process receiver
recorded 1–3 mailbox replacements in most runs, consistent with the GIL
competition expected from a thread receiver. An earlier run of the same
loop under heavier load, before the final harness revision and not retained
as evidence, was dominated by wake-ups up to 126 ms late.

### Native rerun procedure (remote Linux host)

Use the approved Isaac image, pinned assets, the canonical layout and the same
scene runner flags as the recorded hours, so the scene hash
`3b6e8f9a…119e` and the snapshot binding match. Keep `--network none`, a fresh
ignored output directory and no competing task GPU workload. Record
`nproc`, load average and the CPU governor before and after.

1. Source check: in the repository's validation environment, run
   `python -m pytest -q tests/isaac/test_publisher_pacing.py tests/isaac/test_publisher_schema.py tests/isaac/test_publisher_rate.py`.
   Record the commit and the SHA-256 of `isaac/publisher/{pacing,runtime,protocol,benchmark,analyze}.py`.
2. Optional host characterization with the image's Python (no Isaac):
   `PYTHONPATH=/repo /isaac-sim/python.sh -m isaac.publisher.host_timing --output <ignored>/host-timing --seconds 60`.
   Keep it as host timing, not qualification.
3. Retained-evidence attribution: for each recorded hour's `publish.csv`,
   compute the fraction of intervals over 3.333 ms whose error is within
   0.5 ms of |Δ`serialize_ms`|:
   ```python
   import csv; r=list(csv.DictReader(open("publish.csv"))); t=[int(x["host_monotonic_ns"]) for x in r]; s=[float(x["serialize_ms"]) for x in r]
   bad=[i for i in range(1,len(r)) if abs((t[i]-t[i-1])/1e6-1000/30)>1000/300]
   print(len(bad), sum(abs(abs((t[i]-t[i-1])/1e6-1000/30)-abs(s[i]-s[i-1]))<.5 for i in bad)/max(1,len(bad)))
   ```
   A high fraction confirms readback variation; a low one points to overruns or scheduling.
4. 20-second diagnostic, which must report `rate_screen: false`:
   `run_scene.py … --reset-check --skip-reach --publisher-seconds 20 --publisher-collector process`.
   The defaults are `--publisher-pacing presample --publisher-gc freeze`.
   Check `deadline_lag_p99_ms`, `overrun_ticks`, `wake_late_ms_max` and `metadata.json → gc` before committing to the hour.
5. Hour, in a fresh process with the same flags and `--publisher-seconds 3600`.
   Retain the complete run whatever the outcome, and run the independent
   `python -m isaac.publisher.analyze` recomputation and JSON Schema
   validation of both samples as before.
6. Optional bounded control in separate fresh processes:
   `--publisher-pacing at_deadline --publisher-gc default` against the
   defaults, in ABBA order, for 120 s each. It is engineering evidence only
   and never replaces the hour.

**What the rerun must show to pass the unchanged screen:** a completed live
hour of at least 3,600 s, p99 absolute period error ≤ 3.333333 ms, zero
missed deadlines, zero queue overwrites, no gap above 250 ms, every frame
received with contiguous sequence and passing the runtime contract, and no
publisher fault. Diagnostics should show `deadline_lag_p99_ms` well under
1 ms. If it does not, ticks are overrunning: physics + readback + encoding
exceeded the period, and readback cost (#170 cache) or physics cost is the
next lever, not pacing. If GC pauses dominate `longest_pauses`, compare
`freeze_manual` in a bounded run first. Do not relax a threshold, discard a
failed run or infer an hour from a short run.

Windows verification of this change: the 27 new pacing tests and the existing
publisher, projection, binding, private-timing, view-observation,
same-iteration and E2E service tests passed (224 passed, 2 platform skips). The
full suite, after the normal fixture-generation step, reported 958 passed, 32
explicit platform/dependency skips (websockets/Unix/symlink/locked-CI) and one
failure. That failure, `test_local_release_tag_matches_exact_commit_and_detects_retarget`,
needs `git` on `PATH` and passed when rerun with Git available. All 89 schemas
and 14 synthetic examples validated.
