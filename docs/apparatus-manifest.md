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
`apparatus/schemas/g4-manifest-handoff.schema.json`. It contains a freeze reference
and the exact six values `model_id_provisional`, `model_revision`,
`runtime_precision`, `prompt_hash`, `fallback_bank_hash` and
`renderer_recipe_schema_hash`. All six are copied verbatim, with the source file
hash attached; callers cannot override any of them in ordinary fields. The
collector never recomputes generation hashes or creates a freeze record. A hash
pin proves byte identity, not G4 sign-off. Reconcile this handoff with #25's
actual reviewed record format before treating it as approved generation evidence.

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

Still required: exact methodology-template reconciliation; #82 tagged candidate
and binary provenance; per-station settings and complete dependency inventory;
approved G4 handoff; actual acoustic onset and frame-rate records; representative
rendered recording; review/reference IDs and independent manifest review. Issue
#83 remains open after merging this tooling. No new apparatus v0.9 is declared.
