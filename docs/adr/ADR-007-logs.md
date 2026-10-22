# ADR-007 — Append-only logs and protocol exports

Status: Proposed. The template headers are verified (2026-10-21). The column
mapping from app exports is in review (#200).

## Context

WBS O5.1.8 / #50. Trial logs, exposure ledger and deviations must conform to
the private protocol templates. The templates are not in this repository.

`analysis/src/av_analysis/templates.py` encodes each template's exact column
list and the SHA-256 of the reviewed file as test oracles. `check_external`
compares those oracles with a supplied copy of the templates. The public
schemas here are still engineering drafts. A study session cannot rely on them
until the field-by-field mapping below has been reviewed.

## Options

Append-only JSONL events plus CSV exports; CSV-only logs; or a database with
derived exports. JSONL retains ordered events and recovery detail without making
the export format the only source of truth.

## Measurements

**Template oracle check (2026-10-21, `main` `0aa8eeb`).**

- **Method.** `av_analysis.templates.check_external` was run against the
  read-only methodology copy on a maintainer machine. It covered the trial log,
  exposure ledger, visit run sheet and deviations templates.
- **Result.** All 4 templates were checked: 0 header differences, 0 data rows
  and 0 SHA-256 drift from the reviewed hashes. The in-repo column lists are
  therefore the template columns.
- **Rerun.** Set `AV_TEMPLATES_DIR`, then run the analysis tests or
  `check_external`. The templates stay outside Git, and CI has no copy, so this
  check runs only locally.

**App export to template mapping (from #200, not yet merged).**

`av-analysis import-export` maps the provisional Unity exports
(`apparatus/data/*.provisional.csv`) and the console run-sheet and deviation
exports. It maps them onto every template column, using one documented source
per column (`TRIAL_SOURCES`, `EXPOSURE_SOURCES`, `DEVIATION_SOURCES`).

At #200's head, some columns have no source yet. The adapter writes them empty
so that `reconcile` reports them:

| Template | Columns | No source yet |
| --- | ---: | ---: |
| Trial log | 46 | 22 |
| Exposure ledger | 24 | 10 |
| Deviations | 14 | 8 |

Some of these are filled offline by design and never come from the headset:
hidden-answer, scoring and condition columns such as `target_action`,
`exact_correct` and `codebook_id`. The others are open export gaps for #72 to
close or justify:

- schedule-derived timing (`scheduled_onset_mono_ms`)
- `audio_offset_mono_ms`
- `commit_mono_ms`
- the lesson display and retrieval fields
- the deviation disposition fields

Each column's classification needs protocol review before G1.

CI still validates only the draft schemas and clearly synthetic fixtures.
Crash, recovery and hash checks belong to #72; this ADR implements no
production logging or store.

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

These are the draft engineering fields. Their mapping to template columns is
`#200`'s source tables above, not this list. Actual study records stay
private. Public fixture IDs are synthetic. Event
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
