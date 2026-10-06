# Private health latency observer (engineering only)

The default service has no recorder, GC callback or diagnostic loop timer.
`run_scene.py --e2e-private-timing-seconds 180` opts a new isolated source into
one bounded trace starting after its actual READY file is durably written.
The lease, source/publisher code, neutral checks, health ages, command journals,
200 ms client exchange deadline and 250 ms admission limit remain unchanged.
Do not patch or restart a source while its native client is active.

The trace covers source-host `time.monotonic_ns` only. It cannot establish a
Windows/source clock mapping or establish when a packet arrived at the kernel.
Pair the exact client failure request ID with `probe_ingress` and `send_end`.
`socket_data` records the asyncio protocol's data callback, byte count and
connection identity without retaining bytes. A callback can itself be delayed
by the GIL or loop scheduling. `send_end` is completion of the WebSocket send
coroutine, not acknowledgement by the client. A probe absent from ingress may
have been delayed before the callback; it does not prove the client never sent.

`provider_begin/end` bracket the unchanged health provider. The provider is
synchronous on the private loop thread, so its nested events bind to the
preceding correlated ingress before that connection's send begins. Cache
events record request/acquisition/release for both reads (`a=0`) and owner
refreshes (`a=1`). Match them by thread and order. No lock scope or refresh
timestamp is moved. Source events bracket physics/update, full hold,
dispatcher drains, full-state sampling, neutral comparison and encoding.

GC events record start/stop, generation, collected and uncollectable counts;
match by thread/generation. GC settings are never changed. The callback only
attempts a nonblocking recorder lock and stores a primitive tuple. Contention,
reentrancy or capacity exhaustion drops events and makes coverage incomplete;
the callback never waits for a lock, serializes, writes or inspects objects.
Loop-lag events compare a requested 20 ms asyncio sleep with actual resumption.
They are scheduler observations, not guaranteed periodic sampling.
Window boundaries can censor a span or a GC start/stop pair. Retain unmatched
events as censored; never assign zero duration or infer an absent pause. Rows
take their timestamp before the nonblocking lock, so cross-thread append order
is not necessarily chronological. Correlate by timestamp, thread and request
identity; do not infer ordering solely from row position.

The preallocated default ring is a non-wrapping120,000-event array (maximum
200,000). Duration is1–900 seconds within the service lease. Every accepted
event allocates one small primitive tuple; each hook reads a clock and attempts
a nonblocking lock. This adds observer/allocation cost and may influence GC.
No zero-overhead or performance qualification claim is supported. Trace output
is exclusively created, flushed and fsynced only after service observers close;
there are no per-probe disk writes. Inspect `dropped`, `errors`, requested/end
times and unchanged GC settings. `complete` means trace coverage only, never
successful health, native visit, latency, throughput or participant admission.

For a fresh run, retain the source commit/archive hash, image digest, READY raw
hash, both source sessions and client binary/manifest. Start the diagnostic
client/native promptly within the trace window. Preserve physics, publisher,
command, reset, client and trace artifacts even if the run fails or the trace
overflows. Perform a real Stop/reset only after the native owner releases
control. Close only task-owned relays/forwarding/container, seal every raw file,
and derive cross-event timing after shutdown. Existing uninstrumented evidence
is kept separately; no old run is relabelled as instrumented or qualified.
