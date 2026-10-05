# Native mock-visit evidence reconciliation (#81, partial)

The tools in `tools/mock_visit` inspect **actual retained native outputs** in a
private `SIMULATION_TEST` run. They do not generate native playback, fault
injections, screenshots, acoustic observations, material approval, or participant
permission. A positive unit test is a synthetic protocol case. The full #81
normal-visit and seven-fault execution requirement remains open.

The native launcher was blocked by OS Application Control before process
creation during this tooling checkpoint. No new native visit or successful
launch is claimed. Preserve the denied attempt separately; do not invent a
process receipt or export to make it an input to these tools.

## Close and inventory a real run

Wait for the native process to exit, retaining its real exit code and the normal
closed DataJournal export. Use one unique private run root per process. Copy the
exact `experiment.exe.build.json` into that root. The process observer must write
and independently pin a private JSON receipt with these exact fields:

| Field | Requirement |
|---|---|
| `version` | integer 1 |
| `process_id` | actual positive OS process ID |
| `process_exit` | actual integer exit code |
| `source_commit` | exact 40-character build commit |
| `build_manifest_sha256` | raw SHA-256 of the copied build manifest |
| `started_utc`, `ended_utc` | actual timezone-qualified UTC timestamps, end after start |

The following commands use operator-supplied values; the angle-bracket values
are placeholders, not evidence. All manifest-relative files must be inside the
run root. The fixture provenance source can be outside it and has its own raw
pin. The emitter verifies that this pin equals the simulation capability's
`fixture_set_sha256`, then copies the unchanged bytes to
`fixture-provenance.local.json` after finding a closed export. Conflicting
existing destination bytes are refused.

```text
python tools/write_mock_run_manifest.py --run-root <private-run-root> --run-id <run-id> --role <reference|active|yoked> --config staged/join.local.json --config-sha256 <raw-pin> --capability simulation-test.local.json --capability-sha256 <raw-pin> --build-manifest build.json --build-manifest-sha256 <raw-pin> --process-result process-result.json --process-result-sha256 <raw-pin> --fixture-provenance <original-fixture-provenance.local.json> --fixture-provenance-sha256 <raw-pin>
python -m tools.mock_visit.reconcile --manifest <private-run-root>/mock-run.manifest.json --sha256 <emitter-result> --out <fresh-private-report.json>
```

The emitter always sets `complete:false`. The verifier derives software
completion from records. An optional `--selection-snapshot` names an additional
final snapshot inside the root; if present it must exactly match the final
durable mailbox response. It cannot replace missing store history.

Files, links, paths, JSON duplicates, finite numbers, sizes and closed record
shapes are checked. JSON/journals are capped at 64 MiB, manifests at 1 MiB where
specified. Captures are hashed in chunks, at most 1 GiB each and 2 GiB total run
inventory. Captures larger than those bounds must be separately chunked before
inventory, preserving their original provenance. File hashes do not establish
the contents or meaning of video.

## What is verified

- Native journal hashes use their original JSON bytes, preserving Newtonsoft
  number spelling. DataJournal includes its LF in the hash; joined/menu/operator
  journals do not. Torn tails require the exact next-segment recovery record.
- Raw events, exported trial/exposure rows, exact producer opportunity counts,
  audio request IDs, consumed uncertain exposure, fixed slots and visual tails
  reconcile. Pre-cue context replacement is allowed only while unconsumed.
  Simulation provides no confirmed-no-onset permission or uncertain replay.
- Callback observations and software completion must match every request, PCM
  identity, complete sample count and sample provenance. Acoustic onset columns
  stay null/blank. Software estimates remain separate and bounded by their
  reported uncertainty, at most 20 ms.
- Actual sealed A/B package files, 48 kHz mono canonical PCM16 WAVs, trained and
  held-out composition hashes, selected B ranks, and stored speech selections
  are checked. Held-out PCM is never generated to disk by this verifier.
- Profile examples bind the actual reserved registry and allocation's stored
  P1/P2/P3 order. All eight profile plays and each full 96,000-sample completion
  must match. Atom menus bind the ordered ranks 1/1/2/2/3/3 and selected repeats.
- Joined mailbox intents, canonical response/receipt/snapshot hashes, exact
  request IDs, old entries, profile and selected ranks are linked. Menu selection
  receipts must match that history. Fresh verification must precede post-menu
  handoff. The upstream full snapshot digest is an opaque retained commitment:
  this smaller projection omits recipe/semantic fields and cannot independently
  reproduce the entire store or rerender its recipes.
- Lesson/grammar display-call records, protected acknowledgment text equality,
  response/forms/validity ordering, operator request/result pairs and raw frame
  intervals are checked. These are native software records, not photon evidence.
- Build ID, clean commit, protocol, target and successful build result bind the
  copied build inventory. Observer elapsed UTC duration must cover the retained
  monotonic interval. This does not rehash executable bytes, the embedded build
  identity bytes, establish binary custody, or calibrate UTC to Stopwatch.
- Actual fixture provenance, package/permutation/registry pins and simulation
  material attestations are checked. Ordinary `approved:true` records cannot
  substitute for the separate `SIMULATION_TEST` attestation shape.

## Suite plan and executable order

`python -m tools.mock_visit.suite --plan <private-plan.json> --sha256 <raw-pin>
--out <fresh-private-suite-report.json>` re-runs each pinned manifest; it does
not trust cached success reports. The exact plan fields are `version:1`,
`scope:"SIMULATION_TEST"`, `runs`, `screening_artifacts`, `screen_recordings`,
and `external_script_reports`. Each run is `{ "scenario": ..., "manifest":
{ "path": ..., "sha256": ... } }`; supplementary entries are the same path/hash
pair. Paths are relative to the plan. A scenario is `normal` or one of
`headset_disconnect`, `input_loss`, `presentation_stall`, `audio_underrun`,
`corrupt_file_hash`, `failed_reset`, `missing_response_log`.

1. First run A D0 through all lessons, protected tests and forms. Run A D7 from
   its separately stored schedule through forms and 8 speech/8 no-cue validity
   items. Neither schedule's fixed durations may be compressed.
2. Run B active V1/V2/V3, retaining the same immutable store's 8/12/16 growth and
   sealed visit menu ledgers. Run each yoked counterpart using the pinned active
   ledger and explicit fresh process anchor. Then W1/W4 for both roles, including
   W4 forms and validity. The suite compares old entries, selected receipts,
   active/yoked content, within-menu timing and inter-menu pauses.
3. Preserve separate real fault runs with requested/observed injection evidence,
   exact native prefix, resulting pause/fault, and operator recovery history.
   A label in the plan does not prove an injection occurred.

The 12 normal visits contain 1,276 scheduled plays plus 14 reserved grammar
plays. Their fixed slots alone total 12,112 seconds, excluding setup, forms,
breaks and recovery. Existing isolated mapping tests do not meet this coverage.

**Suite completion is deliberately unavailable in this version:** external
fault-injection provenance, screening/eligibility, screen-recording semantics
and O4.5.1 script-result review have not yet been implemented/independently
reviewed. They are inventoried only. The suite therefore reports
`suite_complete:false` and exits 3 even if all normal software visits reconcile.
Single-run exit 0 means only its software reconciliation is complete; exit 3
means valid but incomplete evidence; exit 2 means malformed/inconsistent input.
All outputs keep `issue81_accepted:false` and participant/acoustic qualification
false. Killed processes lacking a closed export and multi-process recovery
histories require further provenance support; this emitter does not invent one.

The XR simulator can exercise these software paths once native launch is
permitted. It cannot qualify headset visibility/interaction, acoustic timing,
actual headset disconnection, methodology review, or participant readiness.

## Validation checkpoint

`mock-visit-reconciliation.validation.json` records synthetic tests and retained
artifact compatibility checks. No actual full visit is included. The minimal
repository Python environment is sufficient; there are no new dependencies.
