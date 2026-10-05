# Private menu-to-store bridge, engineering version 1

`tools/menu_store_bridge.py` connects #70 selection records to the actual #11
`VocabularyStore`. It accepts only a pinned, provisional **DEMO** `DyadBank` and
an independently pinned #13 Study B package. The qualified #26 bank is absent;
every receipt and verified read says `source_kind: synthetic` and
`participant_ready: false`. The function/CLI and bounded private file mailbox
connect to the actual store; they do not grant participant authorization or
qualify a vocabulary.

The adapter does not generate options, substitute a reserve, change the store's
default separation threshold/reserved signals, or skip admissibility. The test
suite uses the existing deterministic synthetic bank/package producer and a
fixed adversarial fixture to exercise the actual store's rejection path. No
fixture is study material or bank qualification evidence.

## Private configuration and request

The closed [schema](../../apparatus/schemas/menu-store-bridge.schema.json) defines
`config`, `request`, `receipt`, `verified_snapshot` and `mailbox_response`.
All fields are required;
unknown fields are rejected. Keep real configuration, paths, banks, packages,
store logs, requests and receipts in private/ignored storage.

Config pins `schema_version:1`, `demo_only:true`, logical `unit_id`, `book_id`,
the raw bank file's `bank_file_sha256`, its canonical `bank_sha256`, package
`package_sha256`, plus explicit `bank_path`, `package_path` and `store_root`.
Both logical IDs must use the `DEMO-` namespace. The CLI additionally requires
the separately provisioned SHA-256 of the raw configuration file. Canonical
`config_sha256` in responses binds the decoded configuration; these two hashes
have different byte representations and must not be confused.

Each request contains `schema_version`, a fresh 32-character lowercase hex
`request_id`, `operation` (`profile`, `atom` or mailbox-only `verify`), exact unit/book/bank/package
bindings, the caller's retained `expected_head` and
`expected_snapshot_sha256`, a `profile` (`P1`–`P3`), `menu_key` and `rank`.

- Initial profile selection uses `menu_key:profile`, `rank:null`,
  `expected_head:null` and the SHA-256 of compact canonical `{}` as its empty
  snapshot pin. It creates one actual fixed-profile synthetic book.
- An atom selection uses its exact atom ID as `menu_key`, rank 1–3 and the latest
  returned head/snapshot pins. Rank 4 is refused. A different profile or a new
  request attempting to replace an already committed atom is refused.
- An exact repeated request ID/bytes returns its original durable receipt after
  rechecking current store integrity. Reusing the ID with different bytes fails.
- `verify` uses `menu_key:verify`, `rank:null`, the retained latest head/snapshot
  and selected profile (null only before profile selection). It revalidates the
  actual store without a journal append or selection receipt. Yoked clients use
  fresh verification rather than treating a cached readiness flag as evidence.

Before each operation, the bridge reloads the raw bank and actual package,
checks all 192 option PCM/file bindings, semantic permutation agreement and all
1,536 composite combinations through the existing loader. The raw bank and
package are read only. Prior store entries are re-read and verified, including
recipe hash, actual PCM/file hashes, fixed profile and semantic binding. The
entire previous snapshot must remain unchanged after a commit.

## Receipts and verified reads

The receipt echoes the request's identity/binding/profile/menu/rank fields and
adds `request_sha256`, `config_sha256`, `status`, `accepted`, `reason`,
`before_head`, `after_head`, `before_snapshot_sha256`, `after_snapshot_sha256`,
`pcm_sha256`, `file_sha256`, `source_kind`, `participant_ready` and
`receipt_sha256`.

`profile_selected` is an accepted profile-only receipt with null audio hashes.
`committed` means the actual store appended the exact chosen recipe, and its
re-read PCM bytes/file hash match the pinned package. `rejected` carries the
actual upstream `E_REJECTED`, `accepted:false`, null audio hashes, and unchanged
store head/snapshot. It does not authorize an alternate option or extra audition.
Other integrity/protocol errors fail closed without a success receipt. The
session owner must handle the fault; no active/yoked or wave authorization is
inferred by this operator-side CLI.

`verified_snapshot(expected_head=..., expected_snapshot_sha256=...)` requires
the caller's latest independent pins. It refuses unresolved intents, re-reads
the store and independently matches each exported rank against the pinned bank
and actual committed recipe/PCM/file. Its result contains the config/unit/book/
bank/package bindings, selected profile, `book_head`, `snapshot_sha256`,
`journal_head`, `profile_selection_receipt_sha256` (null before selection, then
the original profile receipt hash), scope flags, and `entries` with `atom_id`, profile, rank,
PCM/file hashes and `selection_receipt_sha256`. No recipe or semantic label is
returned. `manifest_sha256` binds that result. An empty profile-only book does
not invent committed atom hashes or permit teaching of uncommitted options.

All response hashes use SHA-256 of sorted-key, compact, ASCII JSON, with no LF
and excluding the relevant self-hash field. Snapshot hashes cover the full
per-entry recipe/PCM/profile/semantic binding plus file hash/sample count. The
stored files append one LF; the newline is not part of a response's self-hash.
Consumers must verify closed shapes, exact pins and self-hashes rather than
trust `accepted` alone. Hashes establish identity/integrity, not authorization.

## Durability and recovery

A store-wide OS writer lock serializes bridge processes across all books. The
upstream API itself has no cross-process lock: stop other direct store writers
while the bridge owns it. An unaccounted direct write changes the exact head
and is refused; it is not silently adopted.

Before changing the book, the bridge exclusively writes and fsyncs a private
intent containing the exact request and before-state pins. After the actual
store operation, it verifies all old entries and chosen PCM, appends/fsyncs a
hash-chained bridge journal receipt and then removes the intent. A crash after
the store commit but before the receipt admits only the identical request:
the final actual store record must have the expected source request ID, prior
head and single new entry. Recovery emits the receipt without another store
commit. A crash after journal append returns that same receipt without
duplication. Profile creation has its own empty-book recovery check.

Torn journals/intents, mismatched pins, unrelated writes or ambiguous evidence
remain refused for operator investigation. No automatic repair/truncation occurs.
Durable caller-held latest head/snapshot pins are needed to detect a coordinated
rollback of store and bridge journal. Concurrent manual store writes and storage
power-loss behavior have not been operationally qualified.

## CLI

Use the already installed Python runtime with the sound package dependencies:

```text
python tools/menu_store_bridge.py --config private/menu-store.local.json --config-sha256 <raw-config-sha256> --request private/request.json --output private/new-receipt.json
python tools/menu_store_bridge.py --config private/menu-store.local.json --config-sha256 <raw-config-sha256> --read-verified --expected-head <latest-head> --expected-snapshot-sha256 <latest-snapshot> --output private/new-selection-manifest.json
python tools/menu_store_bridge.py --config private/menu-store.local.json --config-sha256 <raw-config-sha256> --serve-mailbox private/menu-mailbox --seconds 300 --max-requests 128
```

Outputs are exclusive new files. A retry after an output failure may use a new
output path and the identical request, preserving store/journal idempotency.
Exit 0 means an accepted operation/verified read; exit 2 means a durable actual
candidate rejection; protocol/integrity exceptions fail the invocation. There
is no network listener, runtime installation or recipe exchange with Unity.
For the empty initial read, use explicit `--expected-head none` and the empty
snapshot pin. Provision that output and its manifest/config/package pins to the
client independently before enabling menus. A read cannot infer the latest pins
from whichever store happens to exist.

## Private file mailbox

Use an operator-provisioned, access-restricted local directory on a filesystem
supporting atomic hard-link creation. UNC paths, symlinks and Windows reparse
points are refused. Filesystem access is the authorization boundary; this is
not a remote authentication or participant enrollment service. Direct upstream
store writers must remain stopped. The service owns store-wide and mailbox OS
locks for its entire bounded lifetime (at most 3,600 seconds and 1,024 new
responses); the defaults are 300 seconds and 128 responses.

The client fsyncs `requests/.<32hex>.tmp` and atomically renames it to
`requests/<same32hex>.json`. Submit one outstanding request at a time. Multiple
unanswered final requests are ambiguous and stop the service without choosing
an order. Malformed JSON, unsafe names, orphaned or changed responses also stop
it. Temporary files are ignored, never interpreted as requests. Request files
are limited to 16 KiB. Retain completed requests and responses as private audit
evidence; there is no automatic deletion, replacement or repair.

Each response at `responses/<same32hex>.json` contains exactly:

```text
schema_version, request_id, request_sha256, config_sha256,
receipt, snapshot, error, response_sha256
```

`request_sha256` binds canonical decoded request JSON. `response_sha256` binds
all other response fields under the same canonical rule. Accepted selections
and actual store candidate rejections have a receipt plus the freshly verified
snapshot and null error. Successful `verify` has null receipt, a fresh snapshot
and null error. Protocol/integrity failures have null receipt and snapshot and
a bounded uppercase error code; paths and upstream error text are not exposed.
The service verifies the request again before publication and writes/fsyncs a
new temporary response before atomic no-replace publication. A commit followed
by a lost response is uncertain until identical-request recovery proves the
durable journal/store state. A previous response is verified and retained, not
re-executed or replaced.

An error response is terminal for that mailbox request, even if the underlying
fault is later corrected. Preserve it. After inspecting the actual intent,
journal and store evidence, an operator may recover with the identical request
through the CLI and a new output path. The service does not silently execute a
previously answered request again.

The receipt and snapshot are produced under one store lock. The snapshot head
and digest must equal the receipt's after-state pins, and all prior exported
entries must remain byte-for-byte identical. The client must verify these
relations, closed fields and hashes, persist the accepted receipt/snapshot pins
before updating readiness, and fail closed on timeout or uncertain result. No
automatic alternate request is authorized. Full package/store verification is
intentionally retained; transport timeout/performance needs measurement and is
not a participant timing qualification.

The focused Python suite runs explicitly in the existing locked sound CI job
on Linux, macOS and Windows, including the actual package, store and CLI mailbox
process. Dependency-light root checks skip that module when NumPy is absent;
they still validate the public closed schema. No dependency version was changed.
