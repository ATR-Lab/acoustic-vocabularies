# Experimental state-reader handle cache

The handle cache is **disabled by default**. The joined engineering runner has
an explicit `--e2e-handle-cache` option for a bounded comparison; ordinary runs
remain uncached. This option does not enable the separate same-iteration path.
The receiver-only hour uses its original frozen state reader and is independent
of this change. Pinned-runtime binding and tamper evidence is required before
the opt-in path can be used for a bounded profile; no optimized full-hour result
is implied by implementation or pure tests.

`StateAccessors.read_state()` and `read_public_state()` construct fresh ordinary
dictionaries and lists from live USD attribute values, then validate that result
without another deep copy. `validate_states()` still returns a defensive deep
copy for caller-owned mutation inputs. Full-state and subset writes validate all
selected records before changing any USD value. The accessor detaches its layout
authority once on construction.

Explicit `accessors.enable_handle_cache()` after scene construction captures
semantic prim and attribute handles, including visual rotations and collision
attributes. It never caches pose, visibility, state, velocity, material/light
values, a neutral verification result or a previous output frame. Every read
still checks live transform operation order and ancestor visibility/identity,
fetches all requested values, validates visual-state consistency, and returns
detached data. Environment and articulation readback remain live.

Prim/property/ancestor resyncs permanently invalidate the accessor. Only default
value changes on the declared mutable attribute allowlist are accepted; changing
types, time samples, connections, transform order, child visibility, material
bindings or other structure fails closed. Cache opt-in also requires stage edit
target, stage muting, layer replacement/reload, identifier, layer-info and global
layer-muting notice bindings. It compares the live root/session stack, edit
target, muted-layer list and contributing layer identifiers/sublayers/offsets.
Stage reassignment and invalid handles fail closed. There is no automatic cache
rebuild or fallback after invalidation; reconstruct and independently verify the
scene. Normal reset/motion writes remain live-read on the next sample.

The notice API choice follows the official [USD notice bindings](https://github.com/PixarAnimationStudios/OpenUSD/blob/v24.05/pxr/usd/usd/wrapNotice.cpp)
and [Sdf notice bindings](https://github.com/PixarAnimationStudios/OpenUSD/blob/v24.05/pxr/usd/sdf/wrapNotice.cpp).
Their availability and behavior in the pinned Isaac USD build must be checked
directly; a missing required binding rejects opt-in rather than weakening the
guard. This guard covers generated workcell geometry, not the separately loaded
robot asset's appearance or arbitrary third-party code that mutates guard internals.

The paced publisher rate check has a second explicit opt-in,
`--publisher-handle-cache` (`run_publisher_check(handle_cache=True)`). It
applies the same rules: it is enabled after the reset/reach/command checks and
before the publisher, has no rebuild or uncached fallback, aborts on
initialization failure and fails the run on a guard fault. It is refused with
the E2E option or any later workflow on the cached accessor. It records the
runtime `state.py`/`cache_guard.py` hashes. See the
[publisher report](../publisher.md#readback-cost-reduction-pending-native-hour).

## Validation order

1. Finish and preserve any active frozen measurement.
2. Run `python tests/isaac/usd_workcell_check.py` in the approved runtime. Both
   uncached and cached paths must pass live mutation, detached output/layout,
   atomic rejection, visual mismatch, environment and stable save/reload checks.
3. Run `python tests/isaac/usd_cache_tamper_check.py --output <ignored-result.json>`.
   Each independent case runs with caching off and on, begins with a valid
   full/public read, then tests both readers, environment read and write
   rejection after tampering. The uncached results describe the new reader,
   not the original frozen baseline; transient restored composition changes
   may remain readable without retained handles and are reported explicitly.
   Cases include content-changing imports, forced same-content reload, and
   transient insert/restore or mute/unmute operations; an apparently restored
   scene must not clear a fault. Byte-identical root/session imports are separate
   no-op checks: unchanged bytes/spec handles/state, no notices, and correct
   live values through retained and fresh handles after a subsequent write.
4. Preserve source hashes, exact USD bindings, every result and any failed case.
   All 20 cached mutation cases must reject and both no-op proofs must pass;
   keep caching off if any required check fails. The uncached comparison is
   retained without a qualification claim.
5. After structural success, repeat the earlier bounded profile using the same
   actuator flushes, neutral thresholds, receiver checks and GC behavior. Report
   separate before/after sources and measured timings for review before any new
   full optimized hour. The earlier observed 348.500073 ms generation-2 GC pause
   remains evidence; this change does not disable or tune GC.

## Pinned-runtime structural result

The approved Isaac 5.1 image exposes USD **0.24.5**. On 2026-10-05, both
reader modes passed the live round-trip check. The final cached matrix rejected
20/20 mutations and verified 2/2 no-ops; the new uncached reader rejected 18/20
mutations and permitted restored muting/edit-target changes. The cache and its
guard remained unchanged throughout these tests.

The earlier 18/20 cached rejection result is retained. Its two non-rejections
were byte-identical `ImportFromString` calls. A separate actual probe observed
unchanged serialized bytes and spec/attribute handles, no Sdf/USD change notices,
and agreement between retained and fresh handles after a later value write.
Actual content-changing imports produced notices and were rejected. This agrees
with the upstream [Sdf layer update implementation](https://github.com/PixarAnimationStudios/OpenUSD/blob/v24.05/pxr/usd/sdf/layer.cpp#L3649),
which applies fine-grained changes for non-streaming layers. A no-op is not
reported as a rejected mutation. No guard was weakened to obtain a passing count.

The first launch failed before any check because `pxr` was absent from the
launcher search path. Checks then used the image's existing
`extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311` directory on `PYTHONPATH`
and its `bin` directory on `LD_LIBRARY_PATH`; no package was installed and the
structural containers had no GPU or network access.

## Bounded profile result

The separate profile completed all eight phases with no reported fault and a
verified final neutral reset. Scene and snapshot hashes match the earlier
profile; the instrumentation, GC settings, actuator flushes and neutral
thresholds are unchanged. The first five phases are 12 seconds each; the three
additional cProfile phases are 5 seconds each and incur extra instrumentation.
These unpaced measurements are diagnostic, not publisher or station acceptance.

| Physics steps per host second | Earlier reader | Experimental cache |
| --- | ---: | ---: |
| Unprotected, no client | 80.882 | 95.347 |
| Unprotected, receiver thread | 68.756 | 84.471 |
| Unprotected, receiver process | 79.490 | 93.830 |
| Protected, no client | 40.980 | 44.875 |
| Protected, receiver process | 41.596 | 45.454 |

In the no-client phases, median public-object read time fell from 9.630 to
6.483 ms, full-object read from 16.388 to 11.879 ms, and full adapter read from
17.153 to 12.770 ms. Neutral comparison remains about 4.8 ms and encoding about
3.0 ms. These are inclusive timers; parent and child durations must not be added.

Protected throughput remains below the 60 physics steps per host second needed
by the tested two-steps-per-publication arrangement at 30 Hz. A generation-2 GC
pause of **351.319198 ms** persists, alongside the earlier 348.500073 ms
observation. No optimized full-hour run was started. The cache remains an
explicit experimental opt-in.

[The measured result and artifact hashes](state-reader-results.json) preserve
the initial failed matrix, no-op probe, final matrix and both profiles. Exact
deployed source hashes and normalized tracked-text hashes are separate because
the local Windows checkout may use CRLF line endings. Raw USD, images and
traces remain in ignored local evidence directories.

## Current joined-service comparison

The optional runner flag enables the existing guarded accessor immediately
before `run_joined_service`, after the ordinary uncached reset fixture has been
captured and verified. It does not replace the full hold/readback/comparison,
publisher sampling, physics steps, actuator flushes, durable telemetry, GC
policy, private exchange deadline or freshness limits. Initialization failure
aborts; there is no uncached fallback. The top-level run summary records the
actual accessor's `e2e_handle_cache_enabled` state. READY and control/public
wire contracts are unchanged.

Use the existing isolated runtime and pinned assets. Add `--e2e-handle-cache`
only to a fresh `run_scene.py --reset-check --skip-reach --e2e-seconds <5..3600>`
run with all existing explicit station, control-session, UID and Unix endpoint
arguments. The flag rejects reach or additional diagnostic workflows
before simulator startup. Other measurements must use separate fresh runs.
The existing `--capture` camera setup and initial images remain allowed; they
occur before enabling the cache and before the measured service. Keep that
choice identical in both comparison arms to retain the same scene identity.
The normal source remains unchanged when the option is absent.

Before any comparison, retain the pinned-runtime live-read and 20-mutation /
2-no-op checks above. Compare the same committed source with the option off/on
in separate processes, keeping image, scene, snapshot, GC, host workload and
observers fixed. Preserve complete runs and report any common predeclared
warm-up separately. Require matching actual projections and inspect failures,
publication deadlines and frame intervals. The option itself and its 19
configuration checks are not evidence of a throughput improvement or completed
native visit. No new full-hour, route, headset or participant qualification is
granted by this engineering option.

### Actual current-service screen

The comparison used the same committed source `96131c9` and approved Isaac
image in four fresh OFF/ON/ON/OFF processes, requesting 40 seconds each. Camera
setup, scene and snapshot matched. There was no receiver, private client,
timing observer or same-iteration optimization. The state-reader, cache guard,
geometry generator and tamper-test normalized hashes match the retained USD
0.24.5 matrix above; no structural guard changed. Every process exited 0,
reported the expected actual cache flag and retained matching first/last
43-joint/60-object projections. This is endpoint projection evidence, not an
assertion that every intermediate full state was compared.

| Phase | Full-service physics steps/s | Maximum publication gap (ms) | Gaps >250 ms |
|---|---:|---:|---:|
| OFF 1 | 15.358 | 407.156 | 1 |
| ON 1 | 17.767 | 845.715 | 2 |
| ON 2 | 18.345 | 373.627 | 3 |
| OFF 2 | 14.685 | 403.787 | 1 |

All complete timing screens failed; missed publication deadlines numbered
586, 489, 466 and 613. The observed improvement does not meet the required
60 physics steps/s or 30 public frames/s. Host load varied across the ordered
phases: one-minute load rose from 3.62 to 16.22 and ended at 13.35. Unrelated
workloads were preserved, so these four short processes do not establish an
isolated causal estimate or predict performance under a native receiver.

The separate predeclared window begins 10 seconds after the first retained
publication and ends at the last actual publication. Its rates were 15.147,
19.973, 18.370 and 14.889 steps/s. In those windows, the median inclusive
sample/comparison/serialization duration was 26.692 and 24.473 ms with the
cache off, versus 18.465 and 19.062 ms with it on. This timer does not isolate
JSON encoding. The second ON phase still had two >250 ms gaps after warm-up;
excluding warm-up never changes the complete-run failures. GC policy was
unchanged and no GC timing observer ran, so these stalls cannot be assigned
to GC from this evidence.

The [current-service result and artifact hashes](state-reader-joined-results.json)
retain complete and secondary measurements, exact source/image pins and raw
artifact hashes. Task containers and sockets were removed; unrelated GPU and
ROS processes were preserved. The option remains experimental and off by
default. No optimized hour, completed native visit, network, headset or
participant qualification follows from this screen.
