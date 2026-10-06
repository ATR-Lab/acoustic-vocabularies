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

The preallocated default ring is a non-wrapping 120,000-event array (maximum
200,000). Duration is 1–900 seconds within the service lease. Every accepted
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

## Retained measurements

The [007 derivative](evidence/native007-private-health-trace.json) binds the
actual source revision, image, raw archive and all eight finalized artifact
hashes. The matching native probe timed out after 216.3951 ms with zero received
bytes. Source socket callback to parsed ingress took 0.652366 ms; ingress to
send completion took 0.619387 ms. Those observations bound the retained handler
execution, not delivery to Windows. The trace contains 70,876 events and seven
nonblocking drops; coverage is explicitly incomplete. A 370.385 ms generation 2
collection overlaps an early 371.934 ms encode span, away from the matched
failure. Neither that observation nor the separate fast handler proves a cause
for every native refusal.

The [006 layer screen](evidence/native006-private-health-path.json) retained
200 direct Unix and 200 Linux loopback-relay probes with zero 200 ms deadline
failures. Windows forwarding had 3/200 failures. These sequential standard-library
client windows do not establish a native-client or clock qualification.

The [synthetic echo screen](evidence/private-network-echo-screen.json) reproduced
deadline failures with no Isaac or native player: 9/800 private-only and 4/800
with a synthetic public-size stream. TCP_NODELAY ABBA phases did not remove
them. In a separate private-only comparison with TCP_NODELAY fixed 1, configured
OpenSSH default QoS had 6/200 failures before and 6/200 after; IPQoS=none had
15/200 and 8/200 between. No process option was adopted as a fix. Earlier echo
windows may overlap other desktop/Editor work; the QoS window had no Editor,
Isaac or native player according to the operator, with ordinary reads/API work
continuing. File-time-derived wall boundaries are approximate, not clock
evidence. Compression was not tested because bulk did not worsen the observed
private-only failure. All failures and partial coverage remain retained.

## Reproduce the separate echo screen

These scripts are the executed diagnostic logic with formatting-only changes
(AST equivalence checked). They are not a control server, native client or
qualification harness. Run from the repository root. The server is fixed to
the tested host relay UID 1005, fresh mounted `/diagnostic` directory, 300-second
maximum, 4,000 retained echo rows, 592-byte responses, and synthetic 10,799-byte
public padding at nominal 16 Hz. Use the previously approved image; do not
install dependencies or expose a host-wide listener. For a different relay UID,
make an explicitly reviewed/pinned adaptation instead of broadening peer checks.

1. Create a fresh task output directory with mode 0700. Start
   `spikes/O6.1.4/network_echo_server.py` in the approved image, with
   `--network none`, no GPU, that directory mounted at `/diagnostic`, and the
   script mounted read-only. Both Unix endpoints are permission 0600 and accept
   only the pinned host relay UID.
2. Start the existing `isaac/e2e/relay.py` on each owned socket for at most 300
   seconds: private.sock→127.0.0.1:18868 and public.sock→127.0.0.1:18866.
   Forward them with an owned `ssh -N` process to Windows 127.0.0.1:18867 and
   18865, using existing authenticated credentials and host-key verification.
   Do not put credentials in commands, files or reports. Retain `ssh -V` and
   the relevant `ssh -G` values; the executed client was Windows OpenSSH 9.5p2.
3. Run `python spikes/O6.1.4/network_echo_client.py --out <fresh-private.json>`.
   This sends 200 correlated probes per phase with TCP_NODELAY 0,1,1,0 and a
   measured 200 ms response bound. `--load` adds the synthetic public-size
   stream. The collector reconnects after failed requests only to continue the
   diagnostic; it does not qualify recovery or retry exposure.
4. For the separate QoS comparison, keep TCP_NODELAY 1 and no bulk. Use default
   forwarding plus `--fixed-nodelay 1 --phases 1 --out <fresh-A1.json>`, stop
   only that forward, use a fresh `ssh -N -o IPQoS=none` forward plus
   `--fixed-nodelay 1 --phases 2 --out <fresh-BB.json>`, then restore a fresh
   default forward for the final one-phase file. No host configuration is edited.
   Do not combine compression or another option with this comparison.
5. After all clients close, create the server's owned `/diagnostic/stop` file,
   wait for its ordinary exit and `server.json` fsync, and stop the owned relays
   and forwarding. Verify no owned listeners remain; unlink only the verified
   owned socket files. Retain scripts, process arguments, output hashes, phase
   order, concurrent-work notes and all failures. Successful RTT maxima exclude
   right-censored timeouts. These results do not authorize another native run
   on an unchanged failing route or participant use.
