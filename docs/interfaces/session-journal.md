# Session engine journal boundary (#67 → #72)

`ISessionJournal` exposes immutable `Records` for recovery and
`Append(SessionRecord)`. An append must return only after durable persistence or
throw; the engine stops before subsequent cue actions when persistence fails.
Never substitute a buffered queue which reports success before its transaction
is durable. A #72 adapter may add its own private events/exports without changing
the cue-intent ordering or synthesizing missing onset evidence.

`SessionRecord` serializes these exact fields in the minimal #67 journal:

| Field | Meaning |
| --- | --- |
| `event` | Bounded transition/response/fault/recovery event name |
| `clock_epoch` | Random GUID32 for one process clock lifetime |
| `schedule_sha256` | Actual private visit-schedule bytes |
| `opportunity_id` | Original scheduled trial ID, stable across retry |
| `trial_id` | Current attempt ID; new ID for retry |
| `retry_of` | Original attempt ID for one technical retry, otherwise null |
| `audio_request_ids` | Distinct GUID32 IDs for scheduled play credits; empty for no-cue or no active item |
| `block_index`, `item_index` | Zero-based schedule positions |
| `host_mono_ms` | Observed local monotonic milliseconds; cannot decrease within an epoch |
| `scheduled_onset_mono_ms` | Requested audible-onset anchor, null outside an item |
| `state` | Loaded, Ready, CueRequested, ResponseOpen, Closed, Reset, Done; null at visit/boundary events |
| `audible_status` | NotRequested, Uncertain, ConfirmedAudible, ConfirmedNoOnset, NoCue |
| `exposure_consumed` | True for Uncertain or ConfirmedAudible |
| `reset_ok`, `focus_ok` | Observed gate values at the event; not inferred acoustic evidence |
| `technical_fault_code` | Bounded machine code or null |
| `response_code` | commit, dont_know, timeout, or null; response values belong to the private response logger |
| `evidence_sha256` | Trusted onset-evidence hash when supplied, otherwise null |

No intended tuple, semantic answer, method, allocation seed or participant name
is included in this interface. Records remain private because schedule/trial
identifiers and behavior are sensitive even without those fields. Participant
and operator displays must use their own masked view, not journal serialization.

The provided `SessionJournal` is a minimal recovery implementation, not the #72
CSV/export system. It writes append-only numbered `.local.jsonl` segments with
sequence, previous-record hash and SHA-256, using write-through plus `Flush(true)`.
Every envelope has `version`, `sequence`, `previous_sha256`, `record`,
`recovered_tail`, `sha256`; the hash covers the compact UTF-8 JSON plus LF with
the `sha256` field omitted. `record` is a `SessionRecord` and `recovered_tail` is
null for ordinary events.

A final incomplete line is never truncated. The next segment first writes a
hash-bound recovery marker (`record=null`) describing each preserved torn tail
by segment basename, byte offset, tail SHA-256 and whole-segment SHA-256. A crash
while writing that marker can itself be recovered with both tails preserved.
Subsequent reopen must verify these markers; corrupt complete lines or a broken
hash chain fail closed. Limits are 16 MiB per segment, 1,000 segments, 16 KiB per
complete line and 32 simultaneously unresolved recovery tails. Existing files
are never overwritten. A storage failure latches the writer unavailable.

`state_before(CueRequested)` is durable **before** `RequestCue` and carries
uncertain/consumed status. Both state-before and state-after writes precede the
content callback. Recovery treats either as a potentially exposed opportunity.
`retry_queued` is durable before the replacement attempt can run. Audio delivery
events must retain their independent per-play IDs and evidence qualification;
they must not replace uncertainty with guessed acoustic onset or absence.

This development event contract is not a claim to match missing methodology CSV
templates. #72 must retain explicit pending header review for those exports.
