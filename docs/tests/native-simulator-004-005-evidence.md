# Native simulator attempts: builds 004 and 005

Refs #81 and #82. These are five **actual, incomplete Windows XR simulator attempts**, using explicit `SIMULATION_TEST` capability and synthetic materials. All five closed inventories independently verify for integrity; none completes a mock visit. No participant, acoustic, physical headset, leakage, or timing acceptance is claimed. Build 006 is not included.

The [sanitized machine-readable report](native-simulator-004-005-evidence.json) records each exact source, build inventory, run manifest, export, process receipt, terminal receipt, journal and capture hash. Raw histories, identifiers, configuration paths, screenshots and materials remain private. The independent audit reran `tools.mock_visit.reconcile` at `880cf2a30fc6c6dbd94b6ecb6ca49582418e68e0` against the frozen manifests, read the actual CSV and typed records, and visually inspected the retained desktop images. It did not launch another native process or run a new test suite.

| Attempt | Terminal observation | Process exit | Durable data records | Retained grammar audio requests | Completed study opportunities |
|---|---|---|---:|---:|---:|
| 004 smoke | `JOIN_FOCUS_LOST` | 0 | 5 | 0 | 0 |
| 004 full-visit attempt | `GRAMMAR_FAILED` | 0 | 8 | 1 | 0 |
| 005 full-001 | `JOIN_FOCUS_LOST` | 0 | 5 | 0 | 0 |
| 005 full-002 | `JOIN_FOCUS_LOST` | `0xC0000005` | 5 | 0 | 0 |
| 005 full-003 | `AUDIO_EXPOSURE_BLOCKED` | 0 | 8 | 1 | 0 |

All five exports have **zero data rows in both study CSVs**, zero scheduled study cue requests, and zero attributed render intervals. The configured A D0 schedule contains 108 opportunities and 212 study plays; grammar familiarization is separate. The two grammar attempts each retain `started`, `request`, `audio:AUDIO_REQUESTED`, and `interrupted` in typed grammar records. No audio callback, delivered sample or completion was observed. The empty study exposure CSV does not erase that grammar request, and missing callback evidence does not establish physical inaudibility or authorize a free replay.

Each terminal receipt has `complete:false`, `cleanup_succeeded:true` and `export_succeeded:true`. The 005-002 nonzero OS exit remains a separate failure: successful export does not override it, and reconciliation includes `NATIVE_PROCESS_NOT_SUCCESSFUL`. Its shutdown cause is unassigned. Successful export/exit for the other attempts is evidence of retained failed-attempt records, not successful visits.

Build 004 uses source `c61b342e25b2596bd28b83b397c7668a32434d41`; build 005 uses `fbb3c7da9dd826645a1a50e24bc9293b7a4a5365`. The build inventories bind metadata to those revisions. This audit did not independently rehash the executable or embedded build identity.

The 004 smoke/full process intervals overlap by **41.131541 seconds**. No 005 process intervals overlap each other. Do not add the 004 durations into sequential coverage or claim isolated performance. Process lifetimes differ from native retained-event spans; neither calibrates acoustic onset or source-to-display delay. Zero attributed frame intervals is an empty dataset, not a frame-budget pass.

Both 004 runtime desktop captures show a black viewport with the yellow simulation-only banner and no visible G1/workcell. The 005-003 capture shows G1 and portions of the workcell on a light background, with a simulator/help window overlapping the desktop view. This is a useful observed difference, but not a complete participant camera, physical HMD or leakage-matrix check. The staged stimulus image is not a runtime capture. The report does not assign a rendering cause from these images.

The 005-003 diagnostic narrows the grammar failure: `AUDIO_REQUESTED` was observed at 19603.1972ms, followed at 19610.6628ms by `GRAMMAR_RESET_ACK_NOT_CURRENT`, then `GRAMMAR_PRIVATE_CONTROL_NOT_READY` and `AUDIO_EXPOSURE_BLOCKED`. The second audio gate refused about 7.4656ms after the request observation, before `PlayScheduled` in that source revision. The exact accepted reset identity was retained. `ResetAcknowledged` also requires current health, whose conservative age bound includes local receipt age, request round trip, and remote neutral/publisher ages within 250ms. Periodic HTTP health samples were not durably retained, so expired cached health, a queued newer receipt, and transport-worker failure cannot be distinguished retrospectively. Crossing the bound during synchronous evidence persistence is a hypothesis, not an established root cause. Neither a larger limit nor rewriting reset history is justified. Later fixes and reruns must retain their own source/evidence pins.

These attempts supersede any earlier preparation-only statement that build 004 never launched or that OS policy still prevented every launch. The earlier OS refusal and separate missing-configuration startup refusal remain historical evidence; they are not additional completed mock visits.

None of these observations counts toward the seven prescribed fault injections. No matching predeclared injection provenance is credited, and the earlier generic `GRAMMAR_FAILED` must not be retroactively assigned the 005 diagnosis. #81 and #82 remain open. Closure still needs linked fixes/regressions, reconciled affected native segments, full A/B visit coverage, required fault scenarios, reviewed materials and methodology, and the separate physical-device/acoustic qualifications. See the [mock evidence contract](../unity/mock-visit-reconciliation.md) and [partial fault evidence scope](../../tools/mock_visit/FAULT_EVIDENCE.md).
