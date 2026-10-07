# Silent entry and explicit allocation handoff

Build `tools/build-unity.ps1 -Scene PreallocationEngineering` with the normal pinned editor, G1 description, protocol and fresh build ID. This scene contains the silent `OrientationHost`, the existing workcell/source/panel, and a **disabled** `JoinedEngineeringBootstrap`. The latter also requires a typed one-use allocation binding, so enabling it without a verified handoff fails closed. There is no automatic allocation, runtime change or package access before handoff. `ParticipantAdmission` remains false.

Provision the existing orientation/station/state/panel files as described in `docs/orientation/README.md`. The original orientation plan/demo validation still applies; the recorded motion library that failed qualification cannot supply eligibility. An engineering-draft pass produces an ineligible receipt. These instructions do not turn draft wording, failed demonstrations or synthetic test results into a participant's eligibility.

Create a private root and this exact UTF-8 JSON `preallocation.local.json`, using independently verified values:

```json
{"version":1,"scope":"DEMO_ENGINEERING","screening_id":"SYNTHETIC-01","station_id":"station-01","protocol_version":"engineering-pending-review","allocation_list_sha256":"<canonical producer list hash>","mailbox_directory":"operator-handoff","receipt_directory":"orientation-receipts"}
```

Launch with `-preallocationConfig <absolute path> -preallocationConfigSha256 <SHA256 of those exact bytes>`. Screening IDs are opaque 1–32 character `[A-Za-z0-9._-]` values, distinct from the allocated schedule slot. Paths are relative to the config root, without links, absolute paths, backslashes, `..` or streams. This file contains no study package/schedule path. The list hash identifies the operator's intended allocation list; it is not a study material approval.

After the actual durable orientation outcome, the entry closes that journal and creates `<orientation_id>.journal.jsonl` and `<orientation_id>.receipt.json` under the receipt directory with exclusive fsynced writes. The receipt binds screening/station/protocol identity, plan/demo hashes, exact journal bytes, outcome and draft flag. Canonical receipt SHA-256 uses sorted compact ASCII JSON excluding its `receipt_sha256` field. Raw file pins are separate hashes. Missing or failed outcome writes never publish eligibility.

The trusted operator uses `av_schedules.admission.DurableRevealLog` / `admission_cli` from `schedules/docs/admission.md`, explicitly retaining its independent allocation-journal head. That boundary verifies each actual orientation journal and required consent/compatibility/screening/scheduling checks. It returns independently pinned eligibility and reveal receipts. The native app does not invoke reveal or fill any operator check.

After the explicit operator decision, copy those receipts, the producer package-hashes mapping and the prepared joined configuration under the same private root. Atomically publish `operator-handoff/handoff.local.json` with this shape:

```json
{
  "version": 1,
  "request_id": "<fresh 32 lowercase hex>",
  "orientation_id": "<current native orientation ID>",
  "orientation_receipt_sha256": "<canonical current orientation receipt hash>",
  "eligibility": {"path":"operator/eligibility.json","sha256":"<raw file pin>"},
  "reveal": {"path":"operator/reveal.json","sha256":"<raw file pin>"},
  "package_hashes": {"path":"operator/package-hashes.json","sha256":"<raw file pin>"},
  "joined_config": {"path":"joined/join.local.json","sha256":"<raw file pin>"}
}
```

The native gate verifies closed receipt shapes, canonical hashes, list identity, ordered screening/receipt links, journal order and the exact allocated A slot or B member. It consumes the handoff once, including failed attempts. It then records a durable handoff before releasing the old orientation view and enabling the joined loader. Joined config unit/slot identity must match allocation. Before opening the package, the producer package-hashes mapping must match the run-sheet manifest and bind the allocated A book or B unit to the configured package hash. The normal DEMO, source, calibration, gain, material and explicit Resume gates still apply.

One rejected or uncertain handoff requires operator inspection and a fresh controlled entry; the app never changes screening IDs or retries allocation. A complete visit, grammar completion, physical input comfort, acoustic delivery, signed gates and participant eligibility are separate evidence. The synthetic receipt tests exercise software boundaries only.
