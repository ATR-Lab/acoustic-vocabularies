# Durable preallocation operator handoff

`av_schedules.admission.DurableRevealLog` is the operator boundary around the
existing deterministic `RevealLog` policy. The latter remains for old generator
fixtures and is explicitly **not admission authority**. The facade opens only the
independently pinned allocation list, private allocation journal and orientation
receipts/journals. It does not open a study package, schedule or audio file.

The caller provisions a local private directory under `.local`, `private` or
`local-data`, below a user-provisioned parent, with restricted OS permissions.
A top-level directory such as macOS `/private` is not a privacy marker. Constructor arguments are
`(list_path, list_file_sha256, journal_path, expected_head=...)`. The list pin is
SHA-256 of the actual raw producer file. `expected_head` is an independently
retained latest acknowledged allocation-journal head, or 64 zeroes only for a new
empty journal. Reusing genesis after participation has begun defeats rollback
detection and is forbidden. An older retained head is accepted only when it
occurs in the intact current chain; this permits safe serialized operator use.

Every operation takes an OS writer lock, reloads and validates the full current
journal, makes one policy decision, fsyncs its new line, and atomically writes and
fsyncs the retained-head checkpoint before returning. POSIX parent directories
are also fsynced. Files must be regular, local, unlinked and bounded. Journals
are capped at 8 MiB and 2,048 rows. Windows inherits the private parent's ACL.
The caller retains the returned head outside this journal/checkpoint pair; an
attacker able to roll back both files and that independent pin is outside this
local integrity model. No cryptographic identity or human approval is inferred.

## Eligibility before reveal

`log_eligibility(screening_ids, staff=..., checks=..., orientation_files=...)`
requires one A screening ID or two B IDs in screening-completion order. It checks
the existing A consent/compatibility/orientation flags or B
consent/screening/compatibility/scheduling flags, all exactly true. Each party also
needs a finalized non-draft passed orientation receipt with independent raw file
pin and its journal path: `(receipt_path, receipt_file_sha256, journal_path)`.

Native orientation receipt keys (all required, no extras): `schema_version=1`,
`receipt_type="orientation-outcome"`, `screening_id`, `station_id`,
`protocol_version`, `orientation_id`, `plan_sha256`, `demo_index_sha256`,
`journal_sha256`, `journal_bytes`, `outcome` (`pass_first` / `pass_second` / `fail`),
`engineering_draft`, `eligible`, `receipt_sha256`. The hash covers sorted compact
ASCII JSON excluding only `receipt_sha256`. Draft, failed, inconsistent or changed
evidence is refused. The reader checks finalized journal bytes, header identities,
the terminal outcome, eight first-check responses and, when required, eight
second-check responses after exactly one re-explanation. These are software
integrity checks; operator checks and approved plan/demo custody remain external.

`inspect_orientation(receipt_path, receipt_file_sha256, journal_path)` is a
display-only reader for the operator console (#66/#73). It runs the same checks
but also accepts a consistently recorded `fail` (8 second-check responses, not
all correct) or engineering-draft receipt. It requires `eligible` to equal a
non-draft pass, and the journal's terminal draft flag to match the receipt. Its
result is never admission evidence; `read_orientation` and `log_eligibility`
still refuse those receipts with `ORIENTATION_NOT_ELIGIBLE`.

Returned eligibility receipt keys: `schema_version=1`,
`receipt_type="allocation-eligibility"`, `study`, `set`, `list_sha256`,
`eligibility_id`, ordered `screening_ids`, ordered
`orientation_receipt_sha256`, `journal_head_sha256`, `journal_line`,
`receipt_sha256`. The journal permanently binds the orientation receipts and
operator checks. Repeating the same logical eligibility returns the same receipt;
different evidence for an already used screening identity is refused.

`reveal_next(eligibility_id, eligibility_receipt_sha256=..., staff=...)` returns a
closed allocation receipt: `schema_version=1`,
`receipt_type="allocation-reveal"`, `study`, `set`, `list_sha256`,
`eligibility_id`, `eligibility_receipt_sha256`, ordered `screening_ids`, `entry`,
`entry_sha256`, `journal_head_sha256`, `journal_line`, `receipt_sha256`.
`entry` is exactly the existing A or B revealed-entry shape in
`schema/reveal-log.schema.json`. For A it binds participant/slot/unit/book/profile;
for B it binds both participant/member slots and roles, unit/bank, SQ, swap and
profile-menu order. The entry hash covers canonical JSON. Receipt hashes use the
same convention as the orientation receipt. Duplicate reveal returns its
immutable original receipt and never consumes another slot.

The native preallocation entry must verify independently pinned eligibility and
reveal receipts, current screening identity and its exact allocated entry before
invoking a deferred joined-config/package loader. It must also match the existing
package-hash/run-sheet mapping. Orientation alone never reveals allocation or
grants participant admission. Engineering/draft materials remain blocked.

## Private CLI

With the package available on `PYTHONPATH`, use:

```text
python -m av_schedules.admission_cli --allocation-list <absolute-list.json> --list-file-sha256 <raw-pin> --journal <absolute-private-journal.jsonl> --expected-head <independent-head> --request <absolute-private-request.json> --request-sha256 <raw-pin> --output <new-absolute-private-receipt.json>
```

Request shape for eligibility: `schema_version=1`, `operation="eligibility"`,
`screening_ids`, `staff`, `checks`, `orientation_files`, where each file entry is
`receipt_path`, `receipt_file_sha256`, `journal_path`. Reveal request:
`schema_version=1`, `operation="reveal"`, `eligibility_id`,
`eligibility_receipt_sha256`, `staff`. Recover/read an acknowledged eligibility
receipt with `schema_version=1`, `operation="eligibility_receipt"`,
`eligibility_id`. Paths are absolute and private; no extra keys are accepted.
The output file is exclusive/fsynced. Stdout contains only receipt hashes and the
current journal head, never the assignment itself. No automatic retry or reveal
occurs. Missing replies require explicit operator recovery of the same logical
request, not a new screening ID.

## Crash and rollback behavior

If the journal append is complete but checkpoint publication failed, normal open
refuses with recovery required. After preserving evidence, explicit constructor
`recover_tail=True` / CLI `--recover-tail` can re-fsync and adopt exactly one
complete, semantically validated trailing record. It never reexecutes the
allocation. A partial line, missing/invalid checkpoint, moved list, more than one
uncheckpointed row, or a journal missing the independently retained head remains
blocked. A failed writer is latched until reopened. Original evidence is not
truncated or silently repaired. If output receipt writing failed after a durable
reveal, the allocation is consumed; repeat the same reveal to retrieve it.

Independent process locks, append/checkpoint/fsync failure injection, retained-head
rollback, actual producer lists, A/B binding, spare mapping and receipt tampering
are tested with synthetic IDs only. This is not a participant allocation or a
waiver of orientation/material/device qualification.
