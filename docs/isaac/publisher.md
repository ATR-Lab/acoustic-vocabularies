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

This section records the pacing change, synthetic host-only results and two
native hours on 2026-10-09.
- **Pacing change alone:** the hour **failed** the strict timing/rate screen
  with 2 missed deadlines, while meeting p99 (0.626 ms) and every other
  condition ([native rerun result](#native-rerun-result-2026-10-09)).
- **With the opt-in guarded handle cache added for publisher readback:** the
  hour **passed** the unchanged screen, with p99 0.104 ms and zero missed
  deadlines ([cached-readback hour](#native-cached-readback-hour-2026-10-09)).

The two earlier recorded hour failures stand. The pass covers only this
unprotected engineering workload with the cache explicitly enabled.

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

This first change did not use the guarded handle cache from #170. The native
rerun below then showed overruns, so the cache became the follow-up
[readback cost reduction](#readback-cost-reduction).

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

### Native rerun result (2026-10-09)

The run used commit `b714e75`, delivered as a `git archive` whose SHA-256
(`108c887c…07d6`) and embedded commit ID were verified on the host. It ran in
the approved `isaac5.1-rendered-cache-20261004` image (`sha256:38495e05…0c80`)
with `--network none`, one GPU, read-only source and assets, and no
integration overlay. Scene runner flags were `--capture --reset-check --headless
--command-check --publisher-seconds 3600 --publisher-collector process`, with
the defaults `presample`/`freeze`, reach included and 1,000 reset cycles. The
scene hash `3b6e8f9a…119e` and reset snapshot `e2628102…a80e` match the recorded
hours. All 1,000 reset cycles passed. The receiver-process hour also ran
`--disconnect-check`, after its hour; this rerun omitted it, so the disconnect
result is not re-measured. A 5-second GPU/host monitor ran throughout. Only
this run's process used the GPU (28–35% utilization). Another user's
`rosenv` container stayed present and idle, and host 1-minute load was
1.93–9.98 on 64 logical CPUs. The run is recorded as **uncontended**.

**Steps.** (1) Source check: 70 tests passed at the commit. (2) Host
characterization with the image's Python 3.11 (synthetic, not Isaac). Wall-clock
p99 was 4.43/4.24 ms for the recorded loop, 0.10–0.28 ms pre-sampled and
0.12 ms pre-sampled with `freeze`. The GC probe gave a 136.4 ms automatic
generation-2 pause by default and at most 25.2 ms with `freeze`.
(3) Retained-hour attribution: interval deviation correlates with the change
in `serialize_ms` at Pearson r = 0.79 (thread-receiver hour) and 0.97
(receiver-process hour). Within 0.5 ms, that change explains 43% and 73% of
the 1,417 and 9,357 intervals over 3.333 ms. This confirms readback variation
as the main p99 driver. (4) 20-second diagnostic: p99 2.475 ms, 0 missed
deadlines, lag p99 2.73 ms, 23 of 601 ticks overrun. The warm-up full
collection took 421.7 ms over 640,008 objects before the window. (5) A fresh
hour, below.

| Unchanged screen condition | Hour result | Pass |
|---|---:|:---:|
| Completed live hour ≥ 3,600 s | 3600.000514 s | yes |
| p99 absolute period error ≤ 3.333333 ms | **0.626009 ms** | yes |
| Zero missed deadlines | **2** | **no** |
| Zero queue overwrites | 0 | yes |
| No gap above 250 ms | max 45.057 ms, 0 gaps | yes |
| Contiguous, validated frames | 107,999 published = received, 0 sequence gaps, all runtime-validated | yes |
| No publisher fault | none | yes |
| Diagnostic `deadline_lag_p99_ms` well under 1 ms | 0.265 ms | yes |
| **Strict timing/rate screen** | `timing_screen: false`, `rate_screen: false` | **FAIL** |

Interval median was 33.333484 ms and p99.9 error was 2.644 ms. Only 54 of
107,998 intervals exceeded 3.333 ms; each had a late endpoint. The independent
analyzer recomputation matched the summary, and both retained samples passed
JSON Schema validation. No generation-1 or generation-2 collection occurred
during the hour; there were 8 generation-0 passes, the longest 0.105 ms. The
two missed ticks, at 647.4 s and 2,251.9 s, each ended a stretch of about 1 s
in which sample plus encode rose to 15.5–19 ms (median 12.9 ms). Physics plus
readback then exceeded the period on consecutive ticks, and lag grew by about
1 ms per tick until one tick was skipped, without a burst. Ticks overran in
1,173 of 107,999 cases (`overrun_ticks`). The remaining failure is therefore
**readback/physics overrun, not pacing jitter or GC**. The next lever is
readback cost, such as the #170 guarded handle cache, or physics cost. The
strict zero-miss condition was not relaxed.

The [measured record](publisher/native-jitter-hour-results.json) retains
source/archive/image pins, screen results, lag attribution, monitor summary,
the diagnostic, host characterization and raw-artifact hashes. Raw CSVs, logs
and monitor samples remain on the host under
`~/.cache/acoustic-vocab-spikes/2026-10-09/`. All three containers started for
this run were removed. An unrelated exited container from 2026-10-07 and the
other user's container were left untouched. This is an unprotected engineering
workload; it does not qualify protected throughput, network, headset, audio
or G2.

### Readback cost reduction

This subsection gives the software change and the pre-run analysis and
estimates. The native hour that tested it is reported
[below](#native-cached-readback-hour-2026-10-09).

**Where the time goes.** In the 2026-10-09 hour, sample plus encode
(`serialize_ms`) had a median of 12.93 ms, p99 15.34 ms and maximum 25.29 ms.
The distribution is bimodal: the main mode is 12.5–13 ms, and about 9% of
frames sit at 14–15 ms. It is strongly autocorrelated (0.65 at one frame, 0.40
at 60 frames, 0.22 at 300 frames), and per-minute medians range from 12.86 to
14.63 ms. Spikes are therefore slowdowns that persist for seconds to minutes,
consistent with host-level contention on the Python/USD readback, not isolated
per-frame events. The non-USD part of the path is small. On this laptop the
synthetic harness's `encode_stage_probe` measures accessor public validation
at 0.15 ms, projection at 0.03 ms, `validate_frame` at 0.16 ms, JSON at
0.10 ms (`prepare` 0.32 ms in total) and the stamp join at 0.0003 ms; natively
encoding was about 1 ms. Almost all of the 12.9 ms is the 60-object USD
attribute readback plus the articulation copy, which on a GPU pipeline also
waits for the step's device work. The retained CSV has no finer split, so the
rate harness now records one.

**Recovered physics.** `isaac/publisher/replay.py` inverts the presample
loop's lag equation on the 875 ticks whose lag exceeded 0.5 ms. Their median
physics time is 18.42 ms, leaving roughly 2 ms of slack at the median
readback. That is why seconds-long slowdowns of 2–4 ms accumulated lag into
skipped ticks.

**Change.**
- `run_publisher_check(handle_cache=True)` and the scene-runner flag
  `--publisher-handle-cache` (default **off**, as in #170) enable the existing
  guarded cache on the accessor. This happens after reset, reach and command
  checks and before any publisher output.
  - It follows #170's rules. Handles, never values, are reused, and every
    sample still reads all values live. It still runs the structure checks:
    ancestor identity transforms and visibility, per-object handle validity
    and transform-op order, visual/semantic agreement, and the layer
    stack/edit-target/muting signature.
  - Resyncs, metadata or type changes, layer replacement, reload, muting and
    edit-target changes fault permanently. Ordinary reset/motion value writes
    remain live reads.
  - There is no rebuild and no uncached fallback. Initialization failure
    aborts before any output. A mid-run guard fault latches
    `PUBLISHER_FAILURE` and fails the run.
  - The flag is refused before simulator startup with the E2E cache option or
    any workflow that would run afterwards on the cached accessor
    (disconnect, demo, grip, protected stream, E2E, same-iteration,
    published command).
  - Metadata records `handle_cache_requested`, `handle_cache_enabled` and the
    runtime SHA-256 of `state.py`/`cache_guard.py`. The LF-normalized sources
    are byte-identical to those qualified on 2026-10-05 (USD 0.24.5: the
    cached matrix rejected 20/20 mutations and verified 2/2 no-ops).
- Per-frame stage timing (`stage_timing=True` by default) writes
  `stages.csv`: articulation copy plus joint-order check, object public read,
  and encode. Metadata gains `stage_ms` percentiles. Storage is packed arrays,
  with no per-row GC-tracked objects.
- The wire schema, frame contents and validation are unchanged.

**Expected effect (estimates).** The cache's measured public-read median was
9.63 → 6.48 ms across unprotected phases, a saving of about **3.15 ms per
tick** (24% of the 12.9 ms sample+encode median).

| Replay of the 2026-10-09 hour | Missed | p99 (ms) | Intervals > 3.333 ms | Lag > 0.5 ms ticks | Max lag (ms) |
|---|---:|---:|---:|---:|---:|
| Identity (recorded S) | 2 | 0.626 | 54 | 875 | 33.05 |
| S − 3.15 ms | 0 | 0.122 | 6 | 32 | 15.98 |
| Object share × 0.673 | 0 | 0.122 | 2 | 15 | 8.95 |

The identity replay reproduces the measured misses, p99 and over-limit count.
In this hour's realization, about 0.75 ms of saving already removes both
misses. With the cache saving, the replay stays at zero misses with 1 or 2 ms
of extra physics on every tick, and returns to one miss at +3 ms. The seeded
synthetic virtual hour, at 18.4 ms physics and 12.9 ms sample+encode, is
harsher than the real host (uncached p99 3.85 ms against 0.63 ms native) and
is useful for direction only. Its cached read gives p99 1.45 ms, intervals
over the limit 1,520 → 122 and overrun ticks 31,812 → 1,813. See the
[analysis record](publisher/readback-cost-analysis.json).

**Next native hour.** Use the 2026-10-09 procedure and flags, adding
`--publisher-handle-cache`, in a fresh process at the new commit with the
cache tamper/live-read checks re-run if the image or USD build changed:
`--capture --reset-check --headless --command-check --publisher-seconds 3600
--publisher-collector process --publisher-handle-cache`. A 20 s diagnostic
with the same flags comes first. To pass the **unchanged** screen it must show
all of the following:
- a completed live hour of at least 3,600 s
- p99 ≤ 3.333333 ms
- **zero missed deadlines**
- zero queue overwrites
- no gap above 250 ms
- contiguous, validated frames
- no publisher or guard fault

It should also show:
- `handle_cache_enabled: true` with source hashes matching the qualified
  ones
- `stage_ms.objects_ms` median near 6.5 ms
- `deadline_lag_p99_ms` below 1 ms
- `overrun_ticks` well below the previous 1,173

If misses remain, `stages.csv` will show whether the articulation copy, the
object read or encoding grew in the slow stretches.

Windows verification of this follow-up:
- 25 new tests cover the cache opt-in order, default-off, initialization
  abort without output, refusal when already enabled, mid-run guard-fault
  latch without retry, stage rows matching frames, the runner profile, and
  replay fidelity and counterfactuals.
- The full suite, after fixture generation, passed: 984 passed and 32
  explicit platform/dependency skips.
- All 89 schemas and 14 synthetic examples validated.
- The live `pxr` cache guard itself is not importable here and is covered by
  the pinned-runtime checks above.

### Native cached-readback hour (2026-10-09)

**Setup.**
- **Source:** commit `d8c7849`, delivered as a `git archive` whose SHA-256
  (`2f0d05b3…71d6`) and embedded commit ID were verified on the host.
- **Runtime:** the same approved image (`sha256:38495e05…0c80`, USD 0.24.5),
  `--network none`, one GPU, and read-only source and assets.
- **Flags:** `--capture --reset-check --headless --command-check
  --publisher-seconds 3600 --publisher-collector process
  --publisher-handle-cache`, with the defaults `presample`/`freeze`, reach
  included and 1,000 reset cycles (all passed).
- **Bindings:** scene `3b6e8f9a…119e` and snapshot `e2628102…a80e` match the
  recorded hours.
- **Cache qualification:** the image ID and USD build are unchanged, and the
  runtime `state.py`/`cache_guard.py` (and the tamper test) are byte-identical
  to the 2026-10-05 qualification, so the tamper/live-read matrix was not
  re-run, per the documented rule.
- **Contention:** the 5-second monitor saw only this run's process on the GPU
  (28–32% utilization). The other user's `rosenv` container stayed present
  and idle, and host 1-minute load was 1.66–6.26 on 64 logical CPUs. The run
  is recorded as **uncontended**.

The 20-second diagnostic with the same flags gave p99 0.353 ms, zero missed
deadlines and zero overrun ticks. Its object read median was 6.57 ms and
sample plus encode 8.07 ms, against 14.02 ms in the uncached diagnostic.

| Unchanged screen condition | Hour result | Pass |
|---|---:|:---:|
| Completed live hour ≥ 3,600 s | 3600.000249 s | yes |
| p99 absolute period error ≤ 3.333333 ms | **0.103785 ms** | yes |
| Zero missed deadlines | **0** | yes |
| Zero queue overwrites | 0 | yes |
| No gap above 250 ms | max 40.444 ms, 0 gaps | yes |
| Contiguous, validated frames | 108,001 published = received, 0 sequence gaps, all runtime-validated | yes |
| No publisher or guard fault | none | yes |
| **Strict timing/rate screen** | `timing_screen: true`, `rate_screen: true` | **PASS** |

The diagnostics specified beforehand were all met:
- `handle_cache_enabled: true`, with runtime source hashes equal to the
  qualified ones.
- `stage_ms.objects_ms` median 6.594 ms (p99 8.846 ms, max 15.487 ms).
- `deadline_lag_p99_ms` 0.184 ms.
- `overrun_ticks` 44, against 1,173 uncached.

The articulation copy took 0.091 ms median and encoding 1.397 ms. Sample plus
encode fell from a median of 12.93 to 8.10 ms, p99 15.34 to 10.42 ms and max
25.29 to 18.86 ms. That is a 4.8 ms median saving, above the 3.15 ms estimate.
Intervals over 3.333 ms fell from 54 to 13 and maximum lag was 14.69 ms.
No generation-1 or generation-2 collection ran, and the warm-up collection of
352 ms over 640,893 objects happened before the window. The independent
analyzer recomputation matched, both retained samples passed JSON Schema, and
every reported file hash matched.

The [measured record](publisher/native-cache-hour-results.json) keeps the
pins, screen and diagnostic results, stage percentiles, the comparison with
the uncached hour, the monitor summary and raw-artifact hashes. Raw files
remain under `~/.cache/acoustic-vocab-spikes/2026-10-09/`. Both containers
started for this run were removed, and the other user's container and the
earlier exited container were left untouched.

Limits of this result:
- It is one hour on one shared host, and the comparison with the uncached
  hour is sequential.
- It is an unprotected engineering workload with the experimental cache
  explicitly enabled. The cache remains default-off.
- It does not qualify protected throughput, neutral hold, network, headset,
  audio, deployment or G2, and the Ubuntu 24.04 container deviation remains
  open.
