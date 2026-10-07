# Dedicated backend receiver/replay diagnostic

This bounded engineering driver supports #58 and #81. It does not substitute
for the eight-hour multi-station soak or a native Unity mock visit. No actual
result is claimed by the driver tests. The currently running native source is
outside its scope; the driver refuses a readiness record for `station-01`.

Use a separate approved `--network none` simulator container with the ordinary
joined service, a fresh control session, a `fault-` station ID, and a maximum
90-second lease. Keep the experimental same-iteration path off. The private
Unix sockets must be permission0600 and owned by the UID running the diagnostic;
no host relays, TCP listeners or network settings are needed. Do not run this
beside a native visit or another task GPU workload.

Start a bounded readiness watcher immediately with the source. Once the actual
`joined-e2e/ready.json` exists, hash those raw bytes and start the driver in the
same isolated namespace using the existing approved Python and websockets
dependency. Pin scene/snapshot identities independently of that readiness file.
The driver requires at least 75 seconds of remaining source lease on the shared
host monotonic clock before it opens any control socket. A delayed manual launch
therefore refuses admission rather than overrunning the source lease.

```text
python -m isaac.e2e.fault_check
  --source /results/<fresh-source>
  --output /results/<fresh-driver-output>
  --ready-sha256 <actual-ready-file-sha256>
  --scene-sha256 <expected-scene-sha256>
  --snapshot-sha256 <expected-neutral-sha256>
  --control-session-id <fresh-pinned-control-session>
  --station-id fault-01
```

The driver runs this sequence through the real default transports:

1. Start a separate strict receiver. Validate every full v2 frame, its live
   session binding, 43 canonical joint values and 60 public object states against
   the pinned neutral snapshot. Fsync each arrival row for a recoverable prefix.
2. After ten seconds, SIGKILL only that owned child. Verify the expected signal
   exit and retain its unterminated journal as intentionally interrupted evidence.
3. Send an explicit diagnostic operator pause. This is not an automatic Unity
   fault response. Observe ten seconds without a public client and require the
   source publication count to progress without a source fault.
4. Reconnect a fresh strict receiver. Require the same public session and
   advancing sequence/simulation step, then observe ten seconds.
5. Send reset and wait for the source's durable terminal command row, while
   deliberately never consuming its WebSocket acknowledgement. Close that
   connection. This is a discarded application acknowledgement, not a claim
   that a network packet was dropped.
6. Reconnect and replay exactly the same request. Require a correlated duplicate
   successful receipt and independent reset-log counts proving one execution.
   Reusing its ID for `hold_neutral` must return `REQUEST_ID_CONFLICT` with no
   extra reset. A fresh target command must return `PROTECTED_TARGET_COMMAND`.
7. Explicitly resume, continue the new receiver through a 60-second total driver
   window, and close it with a verified terminal/count. In every driver exit path,
   request stop/reset on this independently bound dedicated source and attempt
   all owned child/log cleanup, recording any failure.

The output contains a durable fault/command timeline, both receiver journals,
the intentionally absent first receiver terminal, the second receiver terminal,
actual acknowledgements, reset counts, hashes and a completion/failure summary.
Keep the source's publisher, command, reset, physics and final service logs with
the same evidence set. Every actual timing gap remains evidence; completion of
this short recovery sequence does not override the failed timing gate.

The driver cannot establish Unity pause-before-exposure, audio-ledger preservation,
no novel-cue replay, headset recovery, Isaac restart, Wi-Fi/uplink recovery, clock
qualification or eight-hour stability. Those remain separate required native and
apparatus tests. No station power or network fault is injected here.

## Measured bounded result, 2026-10-05

[The sanitized evidence](backend-fault-results.json) retains both attempts and
their source, archive and artifact hashes. Attempt001 failed before the planned
fault injection because the driver's event metadata collided with its event-name
parameter. Its final real Stop/reset succeeded. The regression correction also
added remaining-lease admission and automatic startup coordination. This failed
attempt remains failed evidence.

Attempt002 completed the 60.188-second recovery sequence. The first receiver
durably validated 148 frames before its intentional SIGKILL; the fresh receiver
validated 622 frames and closed cleanly. Both journals have contiguous sequences
within their own observation windows and zero measured public-neutral deviation
for all 43 joints and 60 objects. Publication continued during the ten-second
receiver absence. Reset-event counts were 1,2,2,2 before reset, after its unconsumed
ACK, after exact replay, and after conflicting reuse: only one reset execution.
The final Stop/reset and container exit succeeded; the dedicated sockets and
container were removed. No host relay was used.

**The timing screen failed.** The source published 939 frames over 60.499 seconds
and recorded 873 missed deadlines. The first receiver had one 451.407ms arrival
gap; the reconnected receiver's maximum gap was 122.867ms. Six consumed command
ACKs had a maximum measured Unix RTT of 93.477ms. The deliberately unconsumed ACK
has no measured application RTT. These short observations do not qualify timing
or establish #58/#81 acceptance. The source's accepted-reset-event count includes
the successful duplicate receipt; the independent reset journal establishes the
execution count.
