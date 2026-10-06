# Offline screening-chain evidence

This read-only verifier joins a sealed orientation journal, the existing durable
allocation policy, and the exact joined configurations named by a mock suite.
It neither allocates a participant nor starts a native process. A successful
software result does **not** establish receipt custody, consent, reviewed
materials, physical screening, displayed content, or participant admission.
Issue #81 remains incomplete until its other evidence and review requirements
are met.

Run against independently retained raw SHA-256 pins:

```text
python -m tools.mock_visit.screening --packet <private-packet.json> --sha256 <raw-pin> --out <fresh-private-report.json>
```

The output is create-new and flushed to disk. Exit `0` means this supplied
software chain verified; `3` means required evidence is missing; `2` means an
input is malformed, altered, contradictory, or unreadable. The verifier does not
rewrite input files. Reports omit screening IDs, material text and local paths.
All physical, material-review, custody, build-execution and participant flags
remain false, including on exit `0`.

## Closed packet, version 1

The exact top-level keys are `version:1`, `scope:"SIMULATION_TEST"`,
`orientations`, `allocation`, and `joins`. A pin is exactly
`{ "path": "relative/path", "sha256": "<64 lowercase hex>" }`. Pins are relative
to the packet, except joined configuration file pins, which remain relative to
that configuration. Paths must be local, contain no traversal, and have no
symbolic links or Windows reparse points. Individual inputs have bounded sizes;
the packet can read at most 64 MiB in total.

`orientations` has at most two records, one per screening identity. Each record
has exactly these fields, each containing a pin or null:

| Field | Retained artifact |
|---|---|
| `receipt` | `OrientationJournal.SealOutcome` canonical receipt |
| `journal` | Complete sealed orientation journal bytes |
| `plan` | Exact `OrientationPlan` input |
| `demo_index` | Exact demo-index bytes named by the receipt |
| `handoff` | Native preallocation handoff record, after an eligible outcome |

The demo index is checked **only for raw byte binding and JSON object shape**.
Its referenced trajectory files and collision, methodology, timing and visual
qualifications are not validated here. The report explicitly names this limit;
it must not be presented as a passed demonstration library.

`allocation` is null or a closed record with pins `list`, `journal`,
`checkpoint`, `eligibility`, `reveal`, and the independently retained value
`retained_head_sha256`. Its list must be a DEMO allocation list from the real
producer. The complete canonical journal is replayed through the existing
policy **in memory**. No writer or `DurableRevealLog` instance is opened by the
verifier. Eligibility evidence, reveal order, selected person/member/role,
spares, canonical entry hash, raw line chain, byte count and final checkpoint
must agree. The retained head must equal the complete supplied journal end;
this offline check does not accept an earlier ancestor head as current.

`joins` contains up to twelve closed records:
`{ "screening_id": "<opaque ID>", "config": <pin or null>, "package_hashes": <pin or null> }`.
Each exact configuration is bound to its revealed A book/slot or B unit/member
and active/yoked role, actual producer schedule identity, and the package-hash
mapping pinned by its run-sheet manifest. The native handoff must name the
orientation receipt, reveal receipt and one of that person's configurations.
Later visits can use their own configurations. The verifier checks this identity
chain; it does not replace the native configuration parser, package loader or
per-visit evidence reconciliation.

Null or absent files remain explicit incomplete reasons. Supplied files with
wrong hashes, unknown keys or contradictory identity/order fail. A sealed draft
or failed orientation can have a valid software sequence, but cannot qualify an
allocation chain. Incomplete prefixes cannot prove an absent exposure or a
completed screening.

## Sequence and suite semantics

The journal check matches the actual `OrientationFlow`, `OrientationJournal`
and response-panel contracts: header and asset-validation record; eight action
screens with bounded synthetic/recorded duration observations; eight target
screens; eight ordered practice responses; and an exact outcome. A first
imperfect check requires one standard reexplanation and the complete second
check in its stored order. Scores are recomputed from the pinned plan. Commit
and Don't Know timestamps must be strictly before the practice deadline;
timeout is permitted at or after it. No third check, trailing event, re-used
orientation or handoff ID, or contradictory eligibility is accepted.

Mock suite plan version `3` adds `screening_chains`, an array of up to three
packet pins, to version `2`. Versions `1` and `2` continue to parse and report
screening evidence incomplete. Each packet configuration hash must identify an
actually reconciled normal run in the suite with the same study, visit and
role. Reusing a packet or configuration across chains fails. A valid chain for
one supplied visit is reported separately from all twelve required visits.
This addition never sets `suite_complete` or `issue81_accepted`: independently
reviewed fault provenance, supplementary evidence and the full native runs
remain separate requirements.

## Reproducible contract checks

```text
python -m unittest discover -s tests -p test_mock_visit_screening.py -v
```

Python fixtures use the real DEMO allocation, durable-reveal and visit-schedule
producers. Their orientation rows and review-shaped test pins are synthetic;
they do not claim human review or a native visit. Adversarial cases alter pinned
and rehashed records, identities, ordering, deadlines, allocation heads, package
mapping and suite bindings.

The separate Unity filter
`AcousticVocab.Orientation.Tests.OrientationScreeningExportTests` writes actual
C# Flow/Panel/Journal output under a fresh `AV_ORIENTATION_EXPORT` directory.
Its three cases cover first pass, second pass after an exact-deadline timeout,
and failure after Don't Know. They retain draft status, use an injected clock
and synthetic demo observations, and never load a study package. Setting the
same environment variable for the Python test checks those sealed C# bytes
independently. Without an export, that one interoperability test explicitly
skips; ordinary CI does not invent a retained native artifact.
