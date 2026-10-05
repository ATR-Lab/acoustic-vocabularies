# Experimental state-reader handle cache

The handle cache is **disabled by default**. No production runner enables it.
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
