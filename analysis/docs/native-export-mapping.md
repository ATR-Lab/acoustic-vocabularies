# Native export to protocol mapping

The adapter names existing public headers and code contracts. Private methodology files remain outside the public repository.

Module `native_protocol` verifies native journal projections and maps pinned offline
references plus observed runtime facts to protocol logs. Its `native_events()` and
`map_native_protocol()` functions are called by `export_import` for native version 1.

Observed values are preserved. Offline joins use hash-pinned allocation/schedule/package inputs. Empty is allowed only for known inapplicability or explicitly reported optional unknown; required observations remain a blocking error. Producer headers_qualified=false is never promoted by the adapter.

Supplement: av-native-observations v1 JSON projects exact verified journal events and source hashes. It provides software event evidence, not physical acoustic or headset qualification.

## Trial (46)

| Column | Authority | Exact source / rule |
|---|---|---|
| `study` | offline | bound visit + validated allocation |
| `protocol_version` | native | manifest identity.protocol_version |
| `participant_id` | native + offline | coded_id validated by reveal binding |
| `dyad_id` | offline | Study B allocation unit; legitimately empty for A |
| `batch_id` | offline | Study A allocation unit; legitimately empty for B |
| `codebook_id` | offline | allocation book_id (A) / bank_id (B), package hash mapping |
| `session_id` | native | manifest identity.session_id |
| `visit` | native + offline | identity.visit_id, bound to requested protocol visit |
| `trial_id` | offline identity adapter | opportunity_id for original; stable retry ID; actual attempt retained in provenance |
| `retry_of` | native identity adapter | native retry link mapped to original protocol trial ID; empty only original |
| `role` | offline | allocation dyad member active/yoked; legitimately empty A |
| `method_masked` | native + offline | producer method_masked; verify against allocation when recorded, never silently replace mismatch |
| `scaffold_family` | offline | dyad structured_family; legitimately empty A |
| `semantic_family` | offline | schedule intended.family; empty nonsemantic/no-cue |
| `trial_type` | offline + native | exact opportunity join to schedule; verify slot_context executed trial_type |
| `message_id` | offline + native | schedule cue_of(item); verify slot_context content_id; empty profile/no-cue |
| `target_action` | offline private | schedule intended semantic_action or action atom semantic_label; empty when role inapplicable |
| `target_referent` | offline private | schedule intended semantic_referent or referent atom semantic_label; empty when role inapplicable |
| `trained_status` | offline | schedule item.trained_status |
| `prior_complete_phrase_exposures` | offline observed-history fold | consuming actual plays strictly before this attempt across prior imported visits; no planned credits |
| `prior_atom_exposures` | offline observed-history fold | same fold for actual atom components; no zero if required earlier history absent |
| `waveform_sha256` | native | first requested play producer waveform hash, or actual journal PCM hash when no file identity exists; compare package independently |
| `scheduled_onset_mono_ms` | native | first audio_request scheduled_mono_ms, or observed slot context for no-cue; not schedule wall-time invention |
| `audio_request_mono_ms` | native | first actual audio_request request_mono_ms |
| `audio_onset_estimate_mono_ms` | native | producer calibrated/observed onset estimate; unknown empty |
| `onset_uncertainty_ms` | native | same onset observation uncertainty; unknown empty |
| `audio_offset_mono_ms` | native qualified timing only | only explicit offset authority; software callback completion remains separate provenance and never acoustic offset |
| `playback_status` | native | existing conservative producer summary; never upgrade uncertain |
| `sim_time` | native optional | explicit device simulation time observation only; empty when absent |
| `frame_freeze_ms` | native | producer observed maximum; empty is unknown, never zero |
| `reset_ok` | native | producer reset observation |
| `focus_ok` | native | producer focus observation |
| `response_target` | native | first committed response target only |
| `response_action` | native | first committed response action only |
| `commit_mono_ms` | native | first committed panel response timestamp same host epoch; timeout/abstention remains empty |
| `response_code` | native | first committed/abstain/timeout response; absent never timeout |
| `exact_correct` | offline private scoring | compare first committed tuple with intended components for tests; timeout/abstention false; lessons/menu empty |
| `action_correct` | offline private scoring | compare applicable intended action; absent component empty |
| `referent_correct` | offline private scoring | compare applicable intended referent; absent component empty |
| `response_time_ms` | offline time difference | commit minus actual/estimated onset only same clock epoch; no acoustic onset for no-cue; unknown empty |
| `technical_fault_code` | native | all producer codes preserved, never erase discrepancy |
| `exposure_consumed` | native | producer conservative actual/uncertain exposure state |
| `feedback_shown` | native | true only observed feedback display; false explicit enforced prohibition/complete coverage; missing fact refuses complete mapping |
| `dictionary_available` | native | explicit enforced dictionary gate/policy; unknown refuses complete mapping |
| `actual_delay_hours` | offline observed-time history | elapsed from previous same-item encounter only when timezone-aware session/slot anchors establish mapping; otherwise null with provenance |
| `deviation_id` | native + operator | producer reference; exact pinned operator record(s) retained |

## Exposure (24)

| Column | Authority | Exact source / rule |
|---|---|---|
| `participant_id` | native + offline | coded_id + reveal binding |
| `dyad_id` | offline | B allocation unit; empty A |
| `session_id` | native | manifest identity.session_id |
| `wave` | offline | visit_wave(study,visit) |
| `event_id` | native | audio_request_id; every actual request exactly once |
| `yoked_source_event_id` | native | choice/replay observed source ID; empty only non-yoked |
| `stage` | offline + native | joined schedule trial_type, verified executed context |
| `atom_or_message_id` | offline + native | scheduled cue or actual option item from menu facts; never native audio_id GUID as semantic item |
| `candidate_id` | native | actual menu option/profile choice ID; empty non-menu |
| `accepted_or_rejected` | native | actual terminal decision accepted/rejected; empty non-menu; pending means unresolved, no coercion |
| `waveform_sha256` | native | producer waveform hash, or actual journal PCM hash for in-memory composition; package comparison independent |
| `whole_phrase` | offline | true iff joined item is complete message; false atom/profile/speech |
| `presentation_index` | native derived | 1-based order of request in actual attempt audio_request_ids |
| `meaning_display_id` | native | observed menu/lesson display span associated with play; empty explicit absence |
| `display_start_mono_ms` | native | observed associated display-start event in compatible clock epoch |
| `display_end_mono_ms` | native | matching observed display-end; unfinished interval unknown |
| `audio_onset_mono_ms` | native | same actual/estimated producer onset, never scheduled onset |
| `audio_offset_mono_ms` | native qualified timing only | explicit offset authority only; callback end preserved separately |
| `audible_status` | native | conservative existing producer status unchanged |
| `retrieval_opportunity` | native | observed lesson retrieval event/phase or actual opened assessment panel; false explicit non-retrieval stage |
| `feedback_content_id` | native | actual observed feedback display reference; no planned ID fabrication |
| `active_choice_or_default` | native | actual terminal menu choice/default event; empty non-menu |
| `pause_ms` | native | observed producer pause aggregation |
| `matching_deviation_id` | native + operator | observed replay/deviation link pinned to operator record |

## Deviation (14)

| Column | Authority | Exact source / rule |
|---|---|---|
| `deviation_id` | private operator | pinned complete 14-column deviations template |
| `timestamp` | private operator | pinned complete 14-column deviations template |
| `protocol_version` | private operator | pinned complete 14-column deviations template |
| `operator` | private operator | pinned complete 14-column deviations template |
| `participant_id` | private operator + offline validation | operator scope bound to visit coded participant or intentionally broader record |
| `dyad_or_batch` | private operator + offline validation | operator supplied scope validated against allocation |
| `event_id` | private operator + native validation | operator supplied affected event/trial/visit reference, resolved via provenance |
| `category` | private operator | closed approved category; six-column legacy reason map only in legacy mode |
| `observed_problem` | private operator | pinned complete 14-column deviations template |
| `action_taken` | private operator | pinned complete 14-column deviations template |
| `prior_audio_exposure` | private operator | explicit none/audible/uncertain; no invented decision |
| `affected_endpoint` | private operator | pinned complete 14-column deviations template |
| `resolution` | private operator | pinned complete 14-column deviations template |
| `reviewer` | private operator | coded staff ID supplied, blank only genuinely unreviewed |

## Preservation and refusal rules

- Preserve all native journal bytes, auxiliary lesson/assessment tables and original console inputs; verify every hash and exact inventory first.
- Compare supplemental event envelopes/payloads with their verified journal source record; reject tampering, unknown keys, missing required source events or ambiguous joins.
- Bind opportunity to one pinned schedule item; verify executed native content/type and schedule/package pins. Never use row position as an answer join.
- Preserve audio uncertainty and software callback-end timestamps separately from protocol acoustic-offset cells.
- Keep optional unknown timing null and list the reason in mapping provenance. Do not fill unavailable measurements from nominal schedule duration.
- Require complete preceding participant history before computing exposure counts for later visits; history file hashes enter provenance.
- Keep old provisional importer behavior available explicitly; native full mapping accepts full operator deviations template and refuses incomplete mandatory operator facts.
- Qualification and complete mapping are distinct: no native template approval, physical validation or clean reconciliation is implied by successful import.

## Native wire format and invocation

The existing `av-analysis import-export` command detects `data-export-native-1` in
its pinned ExportBundle manifest. The base provisional CSV headers remain unchanged.
A native bundle requires `native-observations.json` (`av-native-observations`, version 1).
Each projected event must exactly match its verified journal record, including source
hash, sequence, clock, context IDs, kind and payload. The projection must include every
record of the closed set of native event kinds. Missing or changed records are refused.

Native imports require the pinned, complete 14-column operator deviations CSV. A
header-only file records no deviations; a six-column legacy console file cannot supply
missing operator decisions. The legacy provisional importer remains available for
`data-export-provisional-1` and keeps its existing behavior.

Each executed slot binds the exact private schedule and package hash, person slot,
actual owner-module facts, loaded session event, and enforced dictionary/feedback
policies. Pre-cue replacement contexts are retained. Only a context preceding the first
actual request or response and covering the actual requests may supply that attempt.
Actual requests spanning incompatible replacement contexts are refused.

Import each person's earlier visits first. For Study B selection visits, import the
active partner before the yoked partner. The adapter resolves a yoked source MenuEvent
ID through the active partner's preserved native export to its actual audio-request ID.
It records both IDs and hashes the source history. It never copies an active event's
monotonic timestamps into the yoked participant's time domain.

The adapter preserves original bundles and operator bytes and writes
`mapping/native-protocol.json` beside the protocol CSVs. That report contains source
hashes, offline input/history hashes, attempt identities, optional unknown measurements,
software completion observations and rounding rules. The original producer
`headers_qualified` flag is retained. Native version 1 remains unqualified.

Protocol timing columns use integer milliseconds, rounded half to even from the exact
native values. Original fractional values remain in the native journal and projection.
Response time is the difference of those recorded millisecond timestamps, only with a
first commit, actual/estimated onset and the declared shared host-monotonic clock.
No-cue trials have no acoustic response time. Software playback completion is preserved
as software evidence; it never supplies the protocol acoustic offset column.

Current optional unknowns are reported explicitly: acoustic offset (no acoustic offset
authority in this format), simulator time (not observed in this format), and per-item
elapsed wall-clock delay (no observed wall-clock/monotonic anchor). An unreported onset
also leaves response time unknown. These cells are not invented from nominal schedules,
software completion, planned exposure counts, or function return values.

For in-memory composition, a missing native file hash maps to the actual journal PCM
hash in the protocol waveform cell. The adapter records the source basis, preserves
the original null/file-hash absence, and leaves playback uncertainty unchanged. It never
substitutes an expected package hash for an observed requested-content hash.
