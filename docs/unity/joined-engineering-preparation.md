# Prepare a private joined engineering configuration

`tools/prepare_joined_engineering.py` stages **existing DEMO inputs** for the
joined Unity bootstrap. It does not create packages, schedules, reviews, gains,
calibration, clock evidence, allocations or admission. `DEMO_ENGINEERING` is
required; participant admission remains false. Ordinary producer IDs such as
`A-C01` and `A-C01-L01` are valid: the actual package and schedule must both be
DEMO material, and their identities must agree.

Keep the input map, all staged content and reports in an ignored private location
such as `.local/`. They contain coded visit and answer material. Never commit them.
The staging utility uses the existing locked sound environment; it installs no
dependencies and starts no runtime, service, simulator or audio playback.

## Supply existing inputs

Create a private JSON input map with these exact root keys:

| Key | Value |
| --- | --- |
| `version` | Integer `1` |
| `scope` | `DEMO_ENGINEERING` |
| `protocol_version` | Exact build protocol identifier |
| `identity` | Exact `station_id`, `unit_id`, `coded_id`, `session_id`, `visit_id`, `build_id` |
| `files` | Role descriptors `{ "path": "absolute existing source path", "sha256": "independently recorded raw SHA-256" }` |
| `directories` | Existing absolute content directory paths, as listed below |
| `pins` | Exact hash keys listed below; unavailable optional pins are `null` |
| `control` | Exact `endpoint` and `session_id` of the separately provisioned private controller |

Session IDs are 32 lowercase hexadecimal characters. Other identifiers are bounded
opaque strings, beginning with an ASCII letter or digit and containing only
letters, digits, `.`, `_` and `-`. `build_id` must be the actual native build's
identity. The control endpoint must be literal IP loopback with the `ws` scheme
and `/commands` path, without credentials, query or fragment. This tool never
starts that endpoint or assumes it is healthy.

Required file roles:

```
schedule permutation package_manifest run_sheet_manifest schedule_manifest
run_sheet_csv schedule_schema permutation_schema run_sheet_schema
station frame state_source neutral response_panel
```

Optional file roles may be omitted or set to `null`. Every omitted role is emitted
as explicit `null` in the output configuration:

```
teaching_manifest teaching_review teaching_allocation
menu_script menu_review menu_allocation reserved_registry
assessment_script assessment_review rating_review
speech_manifest speech_review audio_calibration
menu_snapshot menu_bridge_config comfort_gain menu_replay_ledger
```

`comfort_gain`, if supplied, must retain its actual history basename:
`SHA256(coded_id ASCII).local.jsonl`. Copying its bytes does not approve a gain.
The rating review must be existing reviewed wording evidence; an assessment
script review does not replace it. The private menu bridge configuration is copied
byte for byte, including its original independently bound store/package paths;
the stager neither rewrites that backend nor copies or starts its store.

`directories` requires `package`. It may also supply `teaching`, `grammar`,
`speech`, `menu_examples` and `menu_scripts`, or leave those absent/null.
Do not supply mailbox or evidence directories: active request histories are not
copied. The output configuration designates fresh, initially absent paths under
`runtime/` for `menu_mailbox`, `operator_mailbox` and `evidence`.

`pins` must contain exactly:

```
package_sha256 bank_sha256 menu_manifest_sha256 menu_head_sha256 menu_snapshot_sha256
```

The canonical package hash is required. The other pins may be null until the
corresponding real authority exists. A raw manifest file hash and the canonical
package hash are different bindings and must not be substituted for one another.

## Assemble and inspect

Record the raw input-map hash independently, alongside the source pins. From the
repository root, use the already provisioned sound environment:

```powershell
uv run --project sound python tools/prepare_joined_engineering.py `
  --map 'C:\private\existing-inputs.local.json' `
  --map-sha256 '<recorded 64-character raw map SHA-256>' `
  --out 'C:\private\fresh-joined-staging'
```

The output must not exist and cannot overlap any source tree. Absolute local
source paths are required; UNC, symbolic links and reparse points are refused.
Copied relative paths are confined and portable. Files and directory inventories
are bounded and rechecked during copying. The actual producer `load_package`
validates the source and staged package, including PCM and composite checks.
The sealed schedule/permutation bytes, coded visit identity, station/protocol,
neutral raw hash and run-sheet hash chain are checked before publication.

The output contains `join.local.json`, its raw hash file and
`preparation-report.local.json`. Conventional Teaching, Menu and Speech filenames
are retained and compared to their named file pins. Other content trees are copied
without changing their bytes. Directory inventory hashes describe the copy; they
do not establish review or calibration authority.

`join.local.json` is written last. Interrupted preparation may leave a partial
directory; preserve it for inspection and choose a fresh output for another run.
Do not resume in place. The bootstrap independently checks the complete raw config
hash, including after an interrupted final write.

The report lists unavailable optional inputs and explicitly records that full
runtime domain validation remains required. A successful staging operation is not
a successful visit or participant qualification. Missing authorities are expected
to block the corresponding joined host path.

## Explicit runtime provisioning and launch

Version 2 maps retain the version 1 fields and require an additional nullable
`yoked_start` field. A non-null value has exactly
`{"policy":"operator_start_plus_lead","lead_ms":2000}`; `lead_ms` must be an
explicit integer from 2000 through 60000, with no default. Version 2 adds four
optional independently pinned file roles: `yoked_active_schedule`,
`yoked_active_run_sheet_manifest`, `yoked_active_schedule_manifest`, and
`yoked_active_run_sheet_csv`. Supply the paired active visit's actual artifacts;
the runtime verifies their role, visit, unit and run-sheet chain together with
the separately pinned replay ledger. Successful copying does not approve them.
The stager never generates a monotonic anchor. Only explicit operator start at
runtime can consume the configured policy and durably bind an anchor for the
current process. Old anchors, automatic starts and inferred source pins are not
accepted. Version 1 retains its original closed shape and cannot carry version 2
authority fields.

The tool prints the exact launch arguments and a manual source-to-filename list:

| Role | Preprovisioned file under the application's actual `persistentDataPath` |
| --- | --- |
| `station` | `station.local.json` |
| `state_source` | `state-source.local.json` |
| `response_panel` | `response-panel.local.json` |
| `neutral` | The exact safe basename declared by the pinned state-source config |

Use the application's existing provisioning procedure to place those exact bytes.
This utility does **not** find or modify AppData, install station configuration,
overwrite running files, or assume that a different build uses the same data path.
The host compares both actual loaded configuration hashes and preprovisioned file
bytes before creating its journal or control client. The frame config is passed
directly from its pinned staged file.

Launch the matching native build with the printed arguments:

```
-joinedConfig "C:\private\fresh-joined-staging\join.local.json" -joinedConfigSha256 <raw configuration SHA-256>
```

There is no automatic operator Resume. A launch without config or with missing
authority is a bounded engineering diagnostic, not a reason to create synthetic
review/calibration evidence. The existing package, schedule, teaching, menu,
assessment, speech, audio, source, store and frame loaders retain their checks.
Run-sheet schema and full domain semantics are validated again by those loaders;
the stager's chain checks are not a replacement.

## Focused checks

Version 3 preserves the version 2 policy and roles and adds the nullable,
independently pinned `files.grammar_review` role (maximum 1 MiB). Missing input
becomes an explicit null in the staged configuration; the stager never creates
a review. Versions 1 and 2 reject this new role to keep their closed shapes.
The runtime must validate the actual review against the reserved grammar
registry, labels and teaching methodology before familiarization. File identity
alone is not review approval. Familiarization start and later teaching resume
remain separate explicit operator commands; staging starts neither.

Version 4 retains the version 3 roles and adds nullable `files.bank_manifest`
(maximum 4 MiB). Supply the exact independently pinned #26 `manifest.json` for a
qualified Study B package. Older versions cannot carry that new authority. The
joined menu loader verifies its canonical bank hash, permutation, allocation and
every package option. The store binding also requires the bridge's
`bank_file_sha256` to identify these exact raw bytes. A missing or changed manifest
blocks the qualified path, including visits which read a completed store without
showing a menu. Provisional DEMO packages keep their existing path.

The Python service uses the locked banks environment for qualified inputs. Its
existing `bank_path` points to the original verified bank directory's
`manifest.json`; retain that complete directory for backend verification. Staging
copies the pinned app-side manifest but does not relocate or rewrite the service
configuration. The service and app retain their separate raw-file pins. Neither
version 4 nor a qualified DEMO bank grants participant admission.

```
uv run --project sound pytest --import-mode=importlib -p no:cacheprovider tests/test_prepare_joined_engineering.py
```

The sound workflow collects these tests on Linux, macOS and Windows with its
existing locked dependencies. Root checks without NumPy skip the module with an
explicit reason. Tests use a real sealed synthetic producer package and test-only
transport data; they do not generate reviewed or participant-ready inputs.

For a repeatable private loader/bridge fixture, first build the existing seeded
DEMO bank with `tools/qualified_bank_fixture.py`, then run:

```sh
uv run --project banks python tools/prepare_qualified_join_fixture.py \
  --qualified-root "$PWD/.local/qualified-bank-demo" \
  --out "$PWD/.local/qualified-join-demo"
```

This registers the DEMO bank in a freshly sealed allocation list before reveal,
checks the selected unit's ten real schedules and run sheets, seals its package,
and stages a version 4 active V1 join with explicit SIMULATION_TEST materials and
capability. It never changes the source bank. Its apparatus files are transport
fixtures for domain-loader tests; they do not provision an approved station or
assert full-set balance. The result lists the joined-config, capability and
independent store-service paths. Use those with `SimulationAuthorityTests` and
`MenuStoreInterop`; start and qualify the Python service before enabling a client.

Joined engineering data journals use `ExportCodedId`, a conspicuous `DEMO-` alias
of the unchanged schedule slot (`slot_context.person_slot`). Already marked DEMO
or SYNTHETIC IDs are retained; long IDs use `DEMO-` plus their SHA-256 to fit the
data identity contract. Supply this explicit slot-to-coded mapping in offline
references. This keeps synthetic imports within their existing identity gate.
