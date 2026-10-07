# Experimental publication from one verified iteration

This candidate is separate from the runtime used by PR142. It is not enabled
by the ordinary publisher or joined E2E service. The scene runner exposes only
an explicit bounded diagnostic flag. The measured candidate still fails timing;
no deployment or qualification result is claimed.

A read-only 119.383-second slice of that runtime observed 16.192 physics steps
per host second. The actual physics call averaged 5.631 ms, accounting for
9.12% of the host interval. Publication's inclusive full-read, neutral-check
and encoding timer had a 23.084 ms median; transport queue handoff had a
0.035 ms median. The median publication interval was 61.036 ms. These are
separate scopes: the interval also includes the dispatcher's independent full
read/check, robot hold, actuator flush and evidence writes. The existing trace
does not isolate every one of those remaining costs. Its private prefix
archive is SHA-256
`17670e502a92af1a2410b897fc9f350b37e6e0a3c53cc37e2301432caa5fe56a`.

The candidate retains the actual physics step, robot hold, complete live
readback, complete neutral comparator and strict public encoder. It replaces
the publication's second identical read/check with a service-local proof of
the state just checked. The second private-command drain moves after
publication, so no control/reset/demo callback can mutate state inside that
proof interval. This can add publication cost to command response latency;
the existing native 250 ms refusal remains unchanged and must be measured.

`ResetManager.verify_current(capture=...)` requires the exact owning manager,
performs the same read and comparator, and only then supplies the result to
the capture observer. A different manager or failed capture closes the reset
gate. The default call remains unchanged. The experimental dispatcher call
only forwards this observer after its normal hold.

The proof contains immutable, detached JSON bytes for the **complete** state,
including velocities, collision flags, all frames and environment values.
Insertion order is retained so the existing wire encoding stays byte-exact.
It binds the owner thread, progressing simulation step/time, manager, scene,
snapshot, stage/layer identities, mutation generation and capture-start time.
A proof expires after at most the existing 250 ms stale bound. It cannot be
sampled twice or carried into another physics/control/write iteration.

Every scene notice, including allowed value edits and structural/layer changes,
invalidates an in-flight proof. Required missing USD bindings refuse opt-in.
The candidate's transport wrapper checks the exact projected values and full
sample again, then consumes the proof and performs the bounded queue insertion
under one lock relative to notification callbacks. The ordinary transport and
publisher are unchanged.

This is limited to a standalone owner that serializes **all** simulation and
control authoring, keeps the neutral hold active and has no demo factory or
active/nonneutral paused demo. These restrictions are checked before physics
and publication. A notification lock does not lock arbitrary external USD
authors; a concurrent-author application must not opt in. No value cache
crosses a physics, write or control boundary. This also does not enable the
experimental USD handle cache, alter actuator flushing, tune GC or change
neutral tolerances.

Pure regressions cover exact manager binding, 250 randomized byte-equivalent
readbacks, immutable/detached state, every required notice, missing-binding
cleanup, command interleavings, failed readback/encoding, hidden full-state
fields, token replay/staleness and the check-to-queue notification ordering.
They do not prove the pinned USD notice bindings or actual control latency.
Before a candidate benchmark, run the same notice/readback checks in the
existing isolated runtime, review any failure, then compare short baseline
and candidate phases using the same scene, snapshot, checks and receiver.
A full-hour run is inappropriate until the short timing screen passes.

## Bounded actual runner

`tests/isaac/usd_same_iteration_check.py --output <fresh.json>` runs in the
approved pinned USD runtime without a GPU. Its articulation values are explicitly
synthetic; objects/environment and notice behavior are real USD. It refuses to
continue after a restored mutation is accepted. A byte-identical import is checked
separately as a possible no-op, never counted as rejection evidence.

After that structural screen passes, the explicit `run_scene.py
--same-iteration-check` hook runs `isaac.e2e.diagnostic.run_same_iteration_check`.
It requires the usual actual `--reset-check --reset-cycles 1 --capture --skip-reach`
scene setup. Four 30-second phases run baseline/candidate/candidate/baseline in
one isolated process, with a fresh durable reset and fresh private Unix listeners
before each phase. The original service `advance_once` remains the baseline.
No host relay, DDS or TCP listener is needed.

Each phase uses the same separate-process strict v2 receiver and private command
client, full state comparator, neutral hold, 60 Hz physics configuration, 30 Hz
publication target, default GC, and telemetry wrappers. At 1/3/5/7/8/9 seconds the
client requests teaching, test, reset, pause, resume, and a protected demo rejection.
Actual correlated replies and Unix round-trip durations are retained; these are
not Windows/network latency measurements. The output includes every receive time,
publication log, command/reset logs, iteration times, full-read/comparator/hold
inclusive durations, physics-only durations and observed GC pauses. Telemetry
overhead is retained equally in all phases. The first failure stops later phases;
the final full reset is recorded independently. Completion means the diagnostic
finished, not that timing or participant acceptance criteria passed.

## Actual bounded result, 2026-10-05

The [measured record](same-iteration-results.json) binds source
`679769b09c34eb1651e310757d62be265d892fc5`, the approved Isaac 5.1 image,
canonical scene `3b6e8f9a…19e` and neutral snapshot `e2628102…80e`.
All four 30-second phases completed, all 2,074 published frames were strictly
received with no sequence gap or overwrite, and all 24 actual command exchanges
matched their requests. Each test-mode transition and reset returned `reset_ok`;
each target command returned exactly `PROTECTED_TARGET_COMMAND`. Unix command
RTTs stayed below 250 ms (maximum 122.742 ms). The final full reset passed.

| Phase | Physics steps/s | Frames / missed deadlines | P99 absolute period error (ms) | Maximum publication gap (ms) |
|---|---:|---:|---:|---:|
| Baseline 1 | 15.359 | 461 / 439 | 50.140 | 423.927 |
| Candidate 1 | 20.193 | 606 / 295 | 27.185 | 125.851 |
| Candidate 2 | 20.143 | 605 / 296 | 28.844 | 105.318 |
| Baseline 2 | 13.389 | 402 / 498 | 56.001 | 455.870 |

**All four timing screens failed.** The unchanged limits require zero misses,
P99 absolute period error at most 3.333333 ms and no gap above 250 ms. The short
duration also cannot qualify the required hour. Baseline variation and the
sequential order prevent treating the observed ratio as a precise causal effect.

The full-read count verifies the intended reduction: baseline counts were
925/807 for 461/402 steps; candidate counts were 609/608 for 606/605 steps. The
three additional reads in each phase came from the actual mode/reset/pause
commands. All required full neutral comparisons remained. In the candidate,
the full read still had a 20.844–21.002 ms median, and the comparator another
4.718–4.776 ms. Physics averaged 5.568–5.758 ms; robot hold medians were
4.370–4.403 ms. Those measured scopes already exceed the 16.667 ms physics
budget before the remaining work. Publication sample/check/encoding fell to
2.816–2.838 ms median, with an additional 1.443–1.459 ms proof/queue handoff.
These scopes are inclusive in places and must not all be summed together.

Default GC remained enabled. Generation-2 pauses of 360.852 and 382.623 ms were
observed in the two baseline phases; neither candidate phase happened to include
a generation-2 collection. That observation does not establish its elimination.
No GC tuning, actuator-flush removal or experimental USD handle cache was used.

Before the GPU run, actual pinned USD 0.24.5 rejected eight restored
mutation/reload cases and preserved a true no-write positive control. Importing
identical serialized text emitted a metadata notice and conservatively rejected
the proof even though a fresh reader found identical live values. Two earlier
harness outcomes remain retained: the default anonymous `.sdf` format refused
USDA content, then the harness incorrectly expected identical text import to be
a no-op. Only the harness expectations/file-format binding changed; no notice
guard was weakened. The final structural report records all ten observations.

The diagnostic container exited zero and was removed. The unrelated GPU process
and container remained present. Private raw evidence archive SHA-256 is
`d15aeb955f13127cd58084dc49b63a7c495cff7de7c5af07d396618f686e6da6`;
all phase artifact hashes and CSV/log counts were independently recomputed.
This candidate stays opt-in and default-off. The actual read/compare cost and
missed deadlines remain engineering blockers; no longer run is justified by
this result alone.
