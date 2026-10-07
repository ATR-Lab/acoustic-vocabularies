# Apparatus manifest collector (provisional #83)

This implements the field contract in issue #83 and a private hash collector. It
does not supply the missing read-only methodology template, a tagged #82 pilot
candidate, G4 approval, station settings, or recording/calibration evidence.
`manifest_version` is always `0.9-provisional`, `participant_qualified` is always
false, and `template_reconciliation` must remain pending with a reason. These are
deliberate schema constraints, not an accepted v0.9 apparatus freeze.

The schema contains all 26 named template fields and the headset extensions,
plus explicit schedule, clip, timing-record, app-source and console-build
bindings. All 47 fields are required. Each is a closed record with either
`status: recorded`, a typed `value` and its `source`, or `status: pending` /
`not_applicable` and a nonempty `reason`. Nulls and unknown keys are refused at
every depth. Recorded sample rate must be 48000; pending sample rate is not a
measurement. Joint inventory is an ordered, unique list of 43 names. Poses use
metres and XYZW unit quaternions; camera FOV is vertical degrees.

## Collect and independently verify

Use the existing development environment; requirements are the repository's
pinned `jsonschema` and Python 3.12. No device discovery, SDK installation,
network request or simulator launch occurs.

1. Provision a local private directory with access limited to the operator.
   Copy `apparatus/examples/apparatus-manifest-input.example.json` there under
   `.local/`, `private/` or `local-data/`. Those directories are ignored by Git.
   The example is synthetic and entirely pending. Do not populate or commit it
   with station, allocation, review or protocol information.
2. Supply measured versions/settings with their evidence source. Pending and
   not-applicable records must explain why. `recorded` means supplied by the
   operator, not independently detected or approved by this tool. Render/physics
   rates are configured rates; achieved performance belongs in the timing record.
3. For actual files, replace the corresponding artifact record with e.g.
   `{"status":"recorded","files":[{"path":"build.apk"}]}`. Relative paths
   resolve against the private input file. An optional `expected_sha256` checks
   an independently retained pin; otherwise the script computes the first hash.
   `schedules` accepts 1–64 explicitly listed files; other artifact entries accept
   one file each. Supply the actual build, USD, snapshot, asset, protocol, console
   build, schedule files, onset record, rendered-view clip and timing record, or
   retain explicit pending reasons. A listed but missing file is a hard error.
4. Independently calculate/retain the input JSON hash and run (absolute private
   paths required at the command boundary):

   ```text
   python tools/apparatus_manifest.py collect --config <absolute-private-input.json> --config-sha256 <raw-input-sha256> --output <absolute-private-manifest.json> --public-summary <absolute-redacted-summary.json>
   python tools/apparatus_manifest.py verify --manifest <absolute-private-manifest.json> --manifest-sha256 <raw-manifest-sha256>
   ```

The collector writes canonical sorted compact ASCII JSON, so its printed
`manifest_sha256` is also the raw output-file SHA-256. Retain that pin separately.
Verification rereads the pinned input, rehashes every recorded file, reloads the
pinned G4 handoff, re-resolves any supplied release tag and compares the entire
reconstructed manifest. A forged derived hash, changed config, changed artifact,
changed G4 file or moved tag fails. It also rejects an explicitly recorded app
source commit that disagrees with the candidate commit. A matching source-commit
claim alone does not prove that a binary was built from that commit; archive the
actual build provenance separately before release acceptance.

The manifest and input contain sensitive paths and may contain the allocation
seed, station network details and review IDs. Manifest output must be under an
ignored private directory; it is never appropriate to commit the real manifest
in this public repository. On Windows the containing directory's inherited ACL
must already be private. POSIX output mode is 0600. Public summaries include
only a manifest checksum, status counts and fixed nonqualification flags; they
contain no station ID, paths, setting values, seed or artifact hashes. Review
even that explicitly requested summary before publishing it.

## G4 and candidate handoffs

`g4_freeze` is either unavailable with a reason or
`{"status":"recorded","path":"g4.json","expected_sha256":"<independent-pin>"}`.
The strict provisional handoff schema is
`apparatus/schemas/g4-manifest-handoff.schema.json`. It contains a freeze reference,
the freeze status (`draft` or `frozen`) and the exact six fields
`model_id_provisional`, `model_revision`, `runtime_precision`, `prompt_hash`,
`fallback_bank_hash` and `renderer_recipe_schema_hash`. Each field is its value or
`{"status":"pending","reason":...}`; a `frozen` handoff may hold no pending field.
Values are copied verbatim with the handoff file hash as their source: `g4:<sha256>`
from a frozen handoff, `g4-draft:<sha256>` from a draft. Pending fields stay pending
with their reason. Callers cannot override any of them in ordinary fields. The
collector never recomputes generation hashes or creates a freeze record. A hash
pin proves byte identity, not G4 sign-off: the public summary always reports
`g4_approval_verified: false`, and approval is recorded on #25 only.

### Converting the #25 freeze manifest

The handoff is produced from the generation freeze manifest
(`generation/FREEZE-v1.0.json` after G4, the committed `FREEZE-v1.0.draft.json`
before it; format `av-generation/freeze-manifest`, `generation/docs/freeze.md`), never
typed by hand:

```text
python tools/apparatus_manifest.py g4-handoff --freeze <absolute-freeze-manifest.json> --freeze-sha256 <raw-freeze-file-sha256> --output <absolute-new-handoff.json>
```

It prints the handoff SHA-256 (the raw output-file hash) to pin as
`g4_freeze.expected_sha256`. The output holds only public generation values and is
created exclusively, never overwritten. Before writing anything it checks the freeze
manifest against its pin and itself:

- the file hash equals `--freeze-sha256` (`HASH_MISMATCH`); the JSON is strict apart
  from the `null` that marks pending freeze values, and has the freeze format
  (`FREEZE_FORMAT_INVALID`);
- it validates against `generation/schema/freeze-manifest.schema.json`
  (`FREEZE_SCHEMA_INVALID`), which also enforces the frozen-status rules (commit, tag,
  two sign-offs, no pending value);
- every item's `sha256` follows the freeze hash rule: the value for hash items, the
  canonical SHA-256 for objects and arrays, otherwise null (`FREEZE_ITEM_HASH_MISMATCH`);
  item keys are unique (`FREEZE_ITEM_DUPLICATE`) and the items the handoff uses exist
  (`FREEZE_ITEM_MISSING`) with the expected types (`FREEZE_VALUE_INVALID`);
- the freeze's own `apparatus` block equals the values recomputed from its items,
  including `prompt_hash` = canonical SHA-256 of `{a3_sha256, b_sha256}`
  (`FREEZE_APPARATUS_MISMATCH`);
- a frozen manifest is signed off by both the owner and the advisor
  (`FREEZE_SIGNOFF_MISSING`).

The freeze manifest has five apparatus fields (`av_generation.freeze.APPARATUS_FIELDS`);
the handoff has six. `model_id_provisional` is reconciled from the freeze item
`model.id` (the code-pinned `av_generation.constants.MODEL_ID`, guarded by the CI
freeze guard), and once the frozen generation config (`config.document`) is recorded
its `model` pin must match `model.id` and `model.revision` (`FREEZE_MODEL_CONFLICT`).
A freeze value still pending in #25 becomes a pending handoff field whose reason
names the freeze item and its fill-in source; nothing is invented. The handoff status
mirrors the freeze manifest: a draft is never presented as frozen. These checks are an
independent self-consistency check of the file; the full CI freeze guard against the
running generation code remains `python -m av_generation.freeze check`.

Schema change (#83): the handoff schema previously required six concrete values and
had no status, so the current draft (three values pending until G4) could not be
represented without inventing values. It now carries `freeze_status` and allows
pending fields, but only in a draft.

`release_candidate` may contain `status: recorded`, a local `repository`, exact
`tag` and 40-digit `commit`; the tool resolves only `refs/tags/<tag>^{commit}`,
without fetching or changing Git state. A pending candidate remains valid draft
evidence, never an accepted #82 candidate. Review and preregistration fields
contain reference IDs only, not copied private documents.

## Bounds and failure behavior

No recursive file search occurs. JSON is limited to 1 MiB / 20 levels; files to
8 GiB each and artifacts to 32 GiB in aggregate. Schemas bound list and string
sizes. UNC/mapped network paths, traversal, symlinks, Windows reparse points,
hard links and nonregular files are rejected. Identity, size and modification
time are checked before/open/after streaming. Use an operator-owned directory
without concurrent writers; this is not a sandbox against an adversary who can
replace the entire filesystem namespace. Referenced USD/asset dependencies are
not recursively resolved: a single USD hash does not attest its external asset
closure, which needs an independently archived dependency manifest.

Outputs are created exclusively and fsynced; existing evidence is never
overwritten. Parent directories must already exist. POSIX also fsyncs the parent
directory. On write failure an incomplete file may remain for inspection; the
command returns failure and it must not be accepted. If optional summary writing
fails after the manifest succeeds, the command still returns failure and does
not delete the manifest. Error output is a bounded code, not private paths.

## Verification and remaining acceptance

`tests/test_apparatus_manifest.py` exercises real temporary bytes and a temporary
Git tag, tampering, source changes, strict schemas, G4 copying, private-output
guard, output collision, size bounds and redaction. CI runs it on Linux and
Windows, alongside registered synthetic schema fixtures. Linux covers symlink
creation when the Windows account lacks that permission. These tests establish
collector behavior, not station acceptance.

The G4 chain is tested across modules: the committed freeze draft (real
`av_generation.freeze` output) is converted by the CLI, pinned and collected, and
tampered, forged-frozen, under-signed or incomplete freeze manifests are refused
(`tests/test_apparatus_manifest.py`). A clearly labelled synthetic signed manifest
exercises the `frozen` path; it is not a G4 record.
`tests/generation/test_freeze_apparatus_handoff.py` builds a fresh draft from the
running generation code, converts and collects it, and checks that the converter
refuses the tampering the freeze checker refuses; it runs in the generation CI job.

Still required: exact methodology-template reconciliation; #82 tagged candidate
and binary provenance; per-station settings and complete dependency inventory;
G4 freeze (#25) and the handoff converted from it (today only the draft converts:
`runtime_precision`, `prompt_hash` and `fallback_bank_hash` stay pending); actual acoustic onset and frame-rate records; representative
rendered recording; review/reference IDs and independent manifest review. Issue
#83 remains open after merging this tooling. No new apparatus v0.9 is declared.
