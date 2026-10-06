# Private source observations for the view-capture diagnostic

This opt-in records the complete actual owner-thread readback used to construct
a public frame. It does not substitute the neutral snapshot, infer velocities
from poses, or claim that transport queue insertion means delivery or rendering.
The normal source creates no observation journal or extra timestamp when these
options are absent. No private command or public wire schema changes.

Use the ordinary isolated joined service with its existing explicit reset,
station, UID, session and endpoint arguments. All five additional inputs are
required together:

```text
--view-observation-plan-sha256 <independently pinned plan SHA-256>
--view-observation-source-commit <verified deployed Git commit>
--view-observation-max-records <1..4096>
--view-observation-max-bytes <1024..268435456>
--view-observation-max-seconds <1..900, within service lease>
```

Only digests and capacity limits enter this source option; no target, trial,
answer or condition is an input. The independently retained launch manifest
must verify the deployed source/archive against the supplied commit. The plan
digest binds source and native journals without giving the simulator the plan's
hidden contents. The option refuses other diagnostic workflows, the experimental
handle cache and timing observer. Ordinary pre-service camera setup may remain.

## Binding and failure behavior

The command sink first completes the existing durable command-log write. Only
an actual explicit, successful, nonduplicate `reset` in test mode establishes
capture lineage, with matching station, private client and control session.
Every terminal `reset`, `hold_neutral`, `set_mode`, `pause`, `resume` or `stop`
clears the prior lineage, including failure/replay. A rejected nonmutating
command cannot create proof. Durable-log failure also invalidates capture.

For each successful publication, the callback receives the exact full state
already returned by the owner's sampler and checked by the full neutral
comparator. The completion timestamp is taken immediately after that read,
before `frame.host_monotonic_ns`. It verifies exact joint/object projection and
wire JSON equality, then serializes detached immutable bytes. Environment,
body frames, root/joint velocities, collision and visibility state remain in
the complete #53 record. No additional USD read is substituted later.

Serialization, projection, clock, owner-thread or callback errors latch capture
incomplete and stop collection. They do not change source control/health policy
or weaken its existing guards; a source fault independently makes capture
incomplete. This is an observation failure, never admission authority. The
exception is a defective observer whose failure latch itself throws: the
publisher then faults and the source fails closed rather than swallowing an
inability to mark evidence incomplete. A focused regression covers that case.
The callback adds validation/serialization cost on the owner thread and may affect
timing. It cannot be described as a zero-overhead or timing-qualified run.

The combined reset-reply and observation rows are bounded by `max_records`;
the header and all retained serialized rows are bounded by `max_serialized_bytes`.
Reaching either capacity invalidates completeness, retains existing rows and
never wraps. The duration limit is producer-enforced; offline consumers can
check observed spans but cannot reconstruct an unrecorded lifetime. A full
160-reset matrix may exceed 4096 publication/reply rows. Such a partial archive
must not be relabeled complete. No live-loop disk write/fsync is added.

## Frozen private formats

`joined-e2e/view-observation/source-observations.jsonl` begins with a version1
`view_state_observation_header`: `plan_sha256`, `source_commit`, `station_id`,
`control_session_id`, `public_session_id`, `scene_sha256`, `snapshot_sha256`,
`source_clock_id` (opaque32hex), `source_clock_kind="host_monotonic_ns"`, and
`limits` containing the three explicit capacities. A clock ID identifies a
domain; it grants no clock calibration.

Each version1 `view_state_observation` row contains `observation_id` (32hex),
`observed_host_ns` (decimal string), `reset_request_id`,
`reset_reply_canonical_sha256`, `public_session_id`, `sequence`, `sim_step`,
`frame_sha256`, `frame_utf8` (the exact emitted text), `frame` (the same parsed
public object), and `state` (the complete #53 shape). Exact UTF-8 bytes determine
the frame hash; parsing the stored text must equal the stored frame.

`reset-replies.jsonl` has version1 `view_reset_reply` rows with `request_id`,
`reply_canonical_sha256`, and the actual validated `reply` object. The hash uses
Python JSON with sorted keys, compact separators, finite numbers and a terminal
newline (`isaac.reset.snapshot.canonical_bytes`). It is explicitly a canonical
object hash, **not a raw wire ACK hash**. The offline verifier parses the native
retained wire reply and applies this same canonicalization; it must not assume
C# floating-point serialization spells the same bytes.

After listeners, publisher and command log close, the collector independently
writes/fsyncs the two journals and an exact copy of finalized `commands.jsonl`
(bounded256MiB/4096lines). The version1 `view_state_observation_manifest` has
exact fields `plan_sha256`, `source_commit`, `complete`, `fault`,
`observation_count`, `reset_reply_count`, and `files`. The three file roles are
`observations`, `reset_replies`, `commands`; each successful descriptor is
`{path,sha256,bytes}` with a local basename. A failed/missing output descriptor
is null and completeness is false. Zero paired rows, source/cleanup failure,
overflow or any output failure is incomplete. Failure to write the manifest
itself is reflected in the source summary and cannot yield a valid archive.
The final manifest name is published without replacement only after its own
temporary bytes have been flushed/fsynced; a failed manifest write/fsync cannot
leave a successfully named `complete:true` manifest. Retained temporary output
is diagnostic data, not a finalized manifest.

The native capture must bind the same plan, private session and accepted reset,
then match exact applied frame identity/hash. Interpolated output requires both
bracketing observations and the actual interpolation receipt; one source row
cannot silently stand for an interpolated complete state. Missing or uncertain
pairing refuses. This source seam alone does not fulfill the 32-target matrix,
pilot head-pose/tolerance approval, signed code review, timing or G3 acceptance.

## Validation scope

Focused synthetic tests cover detached full-state/wire data, durable-before-
lineage ordering, failed/duplicate reset and transition barriers, hold-neutral,
clock/owner/projection mismatches, rejected target input, capacity/duration,
partial output and manifest failure, plus ordinary publisher behavior when a
capture callback throws. Existing service/rate/timing tests remain applicable.
These tests and the seam implementation are not actual rendered capture evidence.
