# Fault evidence contract

`python -m tools.mock_visit.faults` verifies retained evidence. It never injects
a fault, resumes a session, creates a native observation, or grants participant
admission. All fixtures in its tests are synthetic. A pinned observation is an
operator/harness claim whose bytes can be checked; it is not an authenticated
physical fact or proof that a plan existed before the run.

## Closed native episode

Use independently retained raw SHA-256 pins:

```text
python -m tools.mock_visit.faults --plan private/fault-plan.json --sha256 PLAN_SHA --observation private/fault-observation.json --observation-sha256 OBSERVATION_SHA --manifest private/mock-run.manifest.json --manifest-sha256 MANIFEST_SHA --out private/fresh-fault-report.json
```

The run manifest and every existing reconciliation contract still apply. Fault
runs can be incomplete visits; integrity errors remain errors. A corrupted asset
or broken log is not silently exempted from the underlying byte checks. Preserve
the corrupt input separately from valid exported evidence. Recovery across
multiple native processes and runs without a closed export require further
provenance support; they cannot produce a negative absence claim here.

The exact plan fields are:

| Field | Contract |
|---|---|
| `version`, `scope` | Integer `1`, `SIMULATION_TEST` |
| `case_id`, `run_id` | Opaque IDs; run ID matches the run manifest |
| `scenario` | One of the seven names below |
| `build_manifest_sha256`, `config_sha256`, `schedule_sha256` | Exact raw pins from that manifest |
| `expected_native_codes` | One to eight distinct bounded codes, declared before injection |
| `planned_opportunity_id` | Exact scheduled opportunity ID, or null for a boundary fault |
| `minimum_observation_ms` | Explicit engineering observation floor, between 1,000 and 60,000 ms; not a study timing threshold |

Scenario names are `headset_disconnect`, `input_loss`, `presentation_stall`,
`audio_underrun`, `corrupt_file_hash`, `failed_reset`, `missing_response_log`.
Save the expected behavior and independent plan pin before operating the
injection. This verifier checks byte bindings, not the custody of that step.

The exact observation fields are `version:1`, `scope:"SIMULATION_TEST"`,
`case_id`, `plan_sha256`, `run_manifest_sha256`, `injection`, and `refs`.

`injection` has exactly `method` (`operator_observation` or `software_harness`),
`requested_utc`, `observed_utc`, `outcome` (`applied`, `not_applied`, `unknown`),
and `supporting_artifacts` (one to sixteen `{path,sha256}` pins relative to the
observation). Observer timestamps must be ordered and inside the independently
retained process interval. No UTC timestamp is subtracted from native monotonic
time. The original support files are read and checked; their semantic/physical
truth is not inferred from their hashes.

`refs` has exactly these keys. Missing observations use null rather than invented
rows, except the committed-prefix reference, which is required:

| Key | Exact native evidence |
|---|---|
| `last_committed_data_sha256` | DataJournal row hash retained before the attempted injection |
| `cause`, `detected` | Null or `{journal:"data"|"joined",sha256}` |
| `paused_data_sha256` | DataJournal session `session_paused` |
| `recovery_operator_sha256` | Durable accepted operator `resume` result; matching earlier request must exist |
| `resumed_data_sha256` | DataJournal session `operator_resume`, between that operator request and result |
| `reset_reply_joined_sha256` | Accepted native `control_reply`, matched to an earlier `reset` request after detection, before resume |
| `deviation` | Null or `{data_sha256,source:{path,sha256}}`; native `deviation_reference` must pin the original source bytes |

Detection must carry a predeclared code. Supported software cause facts are
typed `input=false`, `FRAME_FREEZE` with measured duration strictly above 250 ms,
and `AUDIO_UNDERRUN`. These facts do not prove physical injection. Focus loss is
not proof of headset disconnection. Actual changed-file, rejected-backend-reset
and failed-write causes require additional joins; generic `JOIN_RUNTIME_FAILED`
does not establish them. A joined fault lacks an opportunity ID and cannot be
silently assigned to a planned item.

Reset verification checks exact request/reply IDs, non-cached accepted `RESET_COMPLETE`,
`reset_ok:true`, the independently pinned join configuration's
`control.session_id`, nonnegative health ages, healthy source flags, and the existing conservative 250 ms
receipt bound. Host/Isaac timestamps are not used as the local time basis. A
fresh reset receipt and an explicit operator resume are separate requirements.
The reply mode must match the planned opportunity's actual teaching/test block;
boundary cases without an independently bound block leave mode coverage open.

No-new-request and consumed-novel checks run only with a bound post-cleanup
receipt, successful export/cleanup, a single native process clock history, and
the full planned observation interval. They inspect actual request, grammar
request and durable `CueRequested` records from the observed cause (or detection
when the cause is unverified) until accepted
recovery (or terminal shutdown). Consumed novel opportunities cannot receive a
new cue request, lose consumption, or become an unheard retry. At least one novel
opportunity must have been consumed before the fault for that replay check to
report coverage. A shortened/torn history yields unknown, never absence.

These are **native software sequence checks**. They do not establish that a
previously scheduled physical cue was inaudible, that there was no answer in the
headset view, or that free-text deviation notes describe the correct incident.
Those fields remain explicitly unverified. Missing source edges are reported as
incomplete. Exit 0 means this narrow native sequence is complete; exit 3 means
incomplete evidence; exit 2 means malformed/inconsistent input. All reports keep
`issue81_accepted:false`, `participant_qualified:false`, and
`acoustic_qualified:false`.

## Producing a case with the simulation player

`python -m tools.mock_visit.fault_harness plan` writes this exact plan before
launch, and `observe` composes this exact observation from the SIMULATION_TEST
player's `simulation-fault-injection.local.json` receipt and the closed run
manifest. The composer only selects candidate rows; this verifier still checks
them. See the [runbook section](../../docs/unity/native-simulation-tests.md#software-fault-injection-simulation_test-only).
The shared fixtures in `tests/fixtures/simulation_faults` are parsed by the
Unity EditMode suite and validated here; they are synthetic, not native evidence.

## Suite integration

Existing v1 suite plans remain supported. A v2 suite plan has the same fields
plus `fault_cases`, an array of at most seven entries with exactly `scenario`,
`plan:{path,sha256}`, `observation:{path,sha256}`. Paths are relative to the suite
plan. Each case must match the fault scenario's pinned run manifest; reusing a
case or matching it to another run is rejected. The suite reruns the source
checks and reports `fault_evidence_bindings_verified` and
`native_fault_sequences_complete` separately. Scenario names alone cannot set
either field. Physical injection/custody, screening, recording semantics and
O4.5.1 report review remain outside this slice, so full-suite completion remains
unavailable and its exit status remains 3.

## Startup refusal before owner/data/export

```text
python -m tools.mock_visit.faults --startup --plan private/startup-plan.json --sha256 PLAN_SHA --observation private/startup-observation.json --observation-sha256 OBSERVATION_SHA --out private/fresh-startup-report.json
```

This separate plan has exactly `version:1`, `scope:"SIMULATION_TEST"`, `case_id`,
`scenario:"startup_preflight"`, `run_id`, `build_manifest_sha256`, and
`expected_native_codes`. The observation has exactly `version:1`,
`scope:"SIMULATION_TEST"`, `case_id`, `plan_sha256`, `run_id`, `build_manifest`,
`process_result`, `native_log` (each `{path,sha256}`), and
`log_selection:{offset,bytes}`. The selection names one entire bounded ASCII log
line, excluding its newline:

```text
JOINED_ENGINEERING_STATUS JOIN_CONFIG_REQUIRED participant_admission=false
```

The actual positive PID, UTC interval, exit result and source revision must bind
to the pinned successful build inventory. The source log bytes and selected
failure line are checked. No configured session, DataJournal, export or
post-cleanup receipt is fabricated. This output always remains incomplete and
**never counts as one of the seven fault scenarios**, even when the process exits
zero. It does not reverify executable bytes or prove configuration loading.
