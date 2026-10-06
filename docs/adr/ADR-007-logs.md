# ADR-007 — Append-only logs and protocol exports

Status: Proposed — exact external template mapping blocked

## Context

WBS O5.1.8 / #50. Trial logs, exposure ledger and deviations must conform to
private protocol templates. Those files are absent from the searched locations.
The public schemas here are engineering drafts; a study session cannot rely on
them until a field-by-field reconciliation is reviewed.

## Options

Append-only JSONL events plus CSV exports; CSV-only logs; or a database with
derived exports. JSONL retains ordered events and recovery detail without making
the export format the only source of truth.

## Measurements

No protocol-template compatibility evidence yet. CI validates only the draft
schemas and clearly synthetic fixtures. Future crash/recovery/hash checks belong
to #72 after G1; no production logging/store is implemented in this ADR.

## Decision

Propose append-only JSONL events and derived trial-log, exposure-ledger and
deviations CSVs, with a manifest and SHA-256 at clean exit. Preserve partial logs
on interrupted exit, record a recovery event and distinguish interrupted from
complete sessions. Flush boundaries and crash durability require later tests.

Common envelope: `schema_version`, `protocol_version`, `apparatus_version`,
opaque private `session_id`, increasing `event_seq`, `event_type`,
`host_mono_ms`, `clock_id`, optional diagnostic `sim_time`, typed `payload`.
Record raw audible/estimated-onset and Commit timestamps with calibration ID;
never overwrite raw observations with derived RT. Monotonic clocks reset across
boots/processes, so each clock domain has a recorded origin/ID and mappings.

| Draft export | Engineering fields to reconcile with the external template |
| --- | --- |
| Trial log | versions, session/trial ID, trial kind, onset estimate, Commit time, response tuple, timeout/fault, calibration ID |
| Exposure ledger | versions, session/exposure ID, package SHA-256, cue reference, request/onset, completion/fault, calibration ID |
| Deviations | versions, session/event reference, category, monotonic time, reason, recovery/disposition |

These are candidate fields, **not claims about the unseen templates**. Actual
study fields and records stay private. Public fixture IDs are synthetic. Event
types proposed: session_started, exposure_requested, audio_scheduled,
onset_estimated, selection_changed, response_committed, response_timeout,
state_fault, input_lost, connection_lost, paused, resumed, deviation,
session_ended, recovery. Final payload schemas require protocol review.

`schemas/log-event.schema.json` is the strict engineering envelope. It includes
only safe engineering event payloads until protocol mapping is complete; it must
not pretend to validate full study trials. Every future schema version gets
positive and negative examples and reviewed migration rules. The public bridge
must never receive private logging fields.

## Consequences

#72 owns the actual store/export implementation after G1. Schema validation alone
does not enforce semantic privacy or protected-test behavior. Public CI uses
fixtures; actual logs and participant IDs never enter Git/LFS or CI artifacts.

## Manifest fields

`schema_version`, `protocol_version`, `apparatus_version`, template hashes,
clock domains, calibration references, component build hashes, session file hashes,
exporter version and completion/recovery status.

## Revisit trigger

Any template or field change, clock-calibration change, recovery data loss,
ambiguous event order, export mismatch or newly required deviation category.
