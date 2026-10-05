# Experimental publication from one verified iteration

This candidate is separate from the runtime used by PR142. It is not enabled
by the ordinary publisher, scene runner or joined E2E service. No actual
candidate throughput or qualification result is claimed here.

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
A full-hour run is inappropriate until a useful short screen is demonstrated.
