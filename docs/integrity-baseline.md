# DEMO integrity baseline (#78)

This executable baseline joins three independently sealed producer packages to
the real Unity package loader, fixed-slot engine, response state, data adapters,
journal verifier and CSV exporter, then checks the retained exports offline.
It is not a participant-ready or audible-delivery certificate.

Use an existing environment with the locked `sound` dependencies and the pinned,
licensed Unity editor. No dependency is installed by the command:

```powershell
python tools/run_integrity.py --unity '<pinned Unity executable>' --out '<fresh private output directory>'
```

The command refuses dirty source unless `--allow-dirty` explicitly marks the
report as a local development run. It creates private DEMO fixtures, runs three
native test cases, verifies all 480 exported responses, runs the Python negative
and producer tests, and retains hashes in `report.local.json`. Native tests must
pass 3/3 and Python must have zero failures or skips. Each run needs a fresh
output directory. No audio, learner package, native log or private path belongs
in the public repository.

For retained native evidence, pass `--fixtures`, an independently retained
`--fixtures-sha256`, and `--existing-exports` instead of `--unity`. This is labelled
`retained_exports: true`; it never claims to rerun Unity. Each export's mapping
receipt must have its companion SHA-256. Preserve those receipts separately
from any mutable CSV copy.

## Evidence covered

- All 32 command tuples under three actual generator permutations, each with
  correct, wrong-action, wrong-target, don't-know and timeout responses. Scoring
  is offline only and never enters the participant view.
- The engine issues one-use novel permits. The ordinary trained-message path
  rejects held-out requests. Actual producer PCM and stored waveform hashes are
  checked against raw request observations, CSVs and export manifests.
- No audio callback is fabricated. The inactive audio adapter records requests;
  their delivery remains uncertain and exposure consumed. This proves the data
  and scoring path, not what was physically played.
- The existing real store grows 8 → 12 → 16 atoms for all three profiles; old
  bytes, recipes, semantic bindings and profiles remain unchanged, and all four
  overwrite types are rejected and logged for each profile.
- Generated histories for one complete Study A allocation unit (12 learners)
  and one Study B dyad satisfy the existing history/oracle checks: 48/108 A
  teaching plays and 128/8 B menu/profile plays per person. These are generated
  schedule counts, not counts measured in complete joined audio histories.
- Negative tests reject duplicate CSV headings, ragged rows, extra/traversing
  export files, stale hashes, torn tails, and rehashed semantic mutations of
  real native exports (wrong score, PCM, removed novel permission and false
  confirmed delivery).

## Observed verification and limits

On 2026-10-05 the real Unity run passed 3/3 tests, exported 160 cases per package,
and all three offline verifications passed. The combined Python suite passed
59 tests with no skips, including the four real-export mutations. Initial test
harness mistakes were corrected before this final pass; retained local logs
include those failed runs.

Hosted Linux/Windows Python contracts are separate from the licensed Unity
workflow. The latter now prepares real fixtures before its full edit-mode suite
and verifies their exports afterward. It still requires the existing licensed
runner configuration; a local pass does not turn that CI gate green.

## Synthetic engine-produced full visit histories

This step goes beyond the generated schedule counts above. It runs complete producer
visit schedules through the real Unity session engine on a virtual clock, exports
the resulting histories and checks them offline. Every history is **synthetic and
engine-produced**. It counts as evidence toward AC3/AC4, not as closure. The owner
still requires joined native histories.

```powershell
python tools/prepare_engine_histories.py --out <fresh private fixtures directory>
$env:AV_ENGINE_HISTORY_FIXTURES = '<fixtures>/fixtures.local.json'
$env:AV_ENGINE_HISTORY_FIXTURES_SHA256 = '<contents of fixtures.local.json.sha256>'
$env:AV_ENGINE_HISTORY_RESULTS = '<fresh private results directory>'
<pinned Unity> -batchmode -nographics -projectPath unity -runTests -testPlatform EditMode -testFilter AcousticVocab.SelectionMenus.Tests.EngineHistoryExportTests -testResults <xml> -logFile <log>
python -m tools.mock_visit.engine_histories --index <results>/histories-B.local.json --sha256 <pin> --fixtures <fixtures.local.json> --fixtures-sha256 <pin> --out <fresh report>
```

Fixtures come from the public curriculum example seed (`DEMO-o4.4.1-example`), the
same seed as the committed DEMO schedules and allocation lists:

- A-C01: the synthetic book, sealed with every learner's D0 and D7 schedule.
- B-C01: the provisional DEMO dyad bank, sealed with both members' V1–W4 schedules.
- The fixed #14 calibration examples.

The driver runs:

- **A-C01-L01**: D0 then D7.
- **B-C01**: for each of V1, V2, V3, W1 and W4, the active member (M2) and then the
  read-only yoked member (M1). The yoked member replays the active member's sealed
  menu ledger through `MenuReplaySequence`, and `SealYoked` compares the two.

The driver uses these real components: `FixedSlotEngine`, `ScheduleLoader`
block validation, `TeachingCatalog`, `LessonTimeline` + `LessonDataJournal`,
`MenuCatalog`, `MenuTimeline`, `MenuLedger`, `MenuReplaySequence`,
`AudioDataAdapter`/`PanelDataAdapter`, `DataJournal`, `DataDeriver`,
`LessonExport` and `ExportBundle`.

Thin adapters replace the MonoBehaviour hosts:

- Display, XR input, the `AudioPlayer`, the private backend and the #11 store are
  not used.
- Each play writes only `AUDIO_REQUESTED`, with a simulation software-output
  estimate at its nominal time. No callback or observation is recorded, so every
  exposure stays uncertain and consumed.
- Lesson and menu timelines receive virtual-clock software onsets so they can run.
  These are not acoustic or device measurements.
- Menu receipts are synthetic: there is no #11 store.
- Lesson retrieval times out.
- Assessment items answer "don't know".
- D7 and W4 validity blocks are omitted, because they need a reviewed or
  simulation speech bank.

The checker re-verifies each export and runs the existing `reconcile_records`. The
only incomplete reasons it accepts are missing callbacks and, at D7/W4, the omitted
validity block. It then checks:

- **Play counts**: per person, A is 48 atomic-lesson + 108 whole-message lesson
  plays; B is 8 profile + 128 atom-menu plays over V1–V3.
- **Menu ledgers**: each menu ledger's play hashes equal the journal's requests,
  and the active and yoked plays match one to one, with source IDs.
- **Held-out phrases**: every held-out composite (all B profile/rank
  combinations) occurs only as its scheduled novel request, once per history,
  and never in a lesson or menu ledger.

When the retained-history scanner from #173 (`tools.mock_visit.holdouts`) is
present, it also scans the same records. It is not on `main` yet, so this step
is optional and reported as unavailable.

Study A loads only with the Unity loader's current book hash fields (#201).

#78 remains open for complete joined Study A and active/yoked Study B histories,
actual playback/count/contamination reconciliation, a qualified pilot-station
run and a green licensed-Unity CI run. Existing retry and response-family tests
remain separate component evidence. Exact methodology reconciliation is also
pending the authoritative external folder.
