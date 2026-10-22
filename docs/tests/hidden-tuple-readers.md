# Hidden-tuple readers: first-pass code review

Part of #79 (O6.1.2, engineering-pilot check 3). This covers the checklist item
"Review every code path that reads the hidden tuple; record reviewer and
findings".

**Status: first pass only.** An agent did this review by reading source at
`main` `0aa8eeb`. Nothing was run, Unity included. It is an inventory for the
second reviewer named in `docs/spikes/O6.1.2-runbook.md` to audit. It is not
that review, and it is not G3 evidence. The station capture (32 commands × 5
poses), natural head movement, the acoustic route check and sign-off are still
pending.

| Field | Value |
|---|---|
| Source | `main` `0aa8eeb` |
| First-pass reviewer | coding agent, static read only |
| Second reviewer | **pending (human)** |
| Second-review date and source commit | pending |

"Hidden tuple" here means the intended target and action of a protected trial,
plus anything that maps to them: the schedule's `intended` record, the
package's `answers.json`, the permutation's meaning fields, and scoring keys.

Findings use three labels:

- **OK**: the reader is guarded, and the evidence is cited.
- **CONCERN**: there is a plausible leak path, or a flow with no guard.
- **UNVERIFIED**: the reader needs a runtime or native check.

Line numbers refer to `0aa8eeb`.

## Summary

The review found no runtime path by which the hidden target or action reaches
anything the participant sees or hears before Commit or timeout. The main
reason is structural:

- The loaders check the hidden fields and then drop them.
- The runtime trial item (`SlotItem`) carries only an opaque content ID: a
  message, atom or speech ID.
- Isaac never receives a schedule, a package or an answer key.
- In test mode, Isaac rejects every command that carries a target.

Three items are exemptions by design (E1–E3) and should be recorded as such.
Three are low-severity hardening concerns with no leak path found (C1–C3). Five
need runtime or native confirmation (U1–U5).

## Readers by area

### Loaders and the private order

- **`unity/Assets/ExperimentApp/Runtime/SessionEngine/ScheduleLoader.cs`**
  - Lines 146–172 read `item["intended"]` and check it against the permutation.
    Lines 184–191 read the validity `intended` fields and `no_cue_targets`.
  - Line 172 builds `SlotItem(id, type, message ?? atom ?? speech, ...)`. The
    `intended` object is a local that goes out of scope.
  - `SlotItem` has no meaning fields (`SessionContracts.cs` lines 18–43). A
    `no_cue` item gets a null content ID.
  - **OK.**
- **`unity/Assets/ExperimentApp/Runtime/StudyAudio/PackageLoader.cs`**
  - Lines 166–177 and 375–476 read `answers.json`, the permutation, the
    allocation and the package schedules' `intended`. They are used for
    checking only, and the parsed documents are locals.
  - `LoadedAudioPackage` (lines 19–42) keeps only paths, hashes and trained
    flags.
  - **OK.**
- **`unity/Assets/ExperimentApp/Runtime/SessionIntegration/JoinedVisitArtifacts.cs`**
  - Lines 41 and 56 keep the raw permutation bytes for the visit.
  - Line 91 loads the yoked partner's schedule through `ScheduleLoader`, which
    drops `intended`.
  - **OK. See C1.**
- **`unity/Assets/ExperimentApp/Runtime/SessionIntegration/JoinedSelections.cs`**
  (lines 33–81)
  - Keeps atom and message structure IDs only, with no semantic labels.
  - **OK.**
- **Offline producers** (not runtime)
  - `schedules/src/av_schedules/orders.py` writes `intended` and
    `no_cue_targets`.
  - `sound/src/av_sound/package.py` writes `answers.json`.
  - Unity only reads the order these fix.

### Session engine and protected blocks

- **`SessionEngine/FixedSlotEngine.cs`** line 168
  - For heldout items it passes the content ID to a single-use `NovelPermit`,
    which is used for composition only.
  - Session records and operator snapshots carry no content.
  - **OK.**
- **`Assessment/UnityAssessmentPorts.cs`** lines 89–129
  - Chooses and composes the cue PCM from the content ID and role. Novel and
    speech trials preload placeholder atoms.
  - The output is the audio buffer, which is the stimulus itself.
  - **OK.**
- **`Assessment/ProtectedSlots.cs`**
  - The view interface (lines 37–42) has only `Neutral()` and
    `Acknowledgment()`.
  - The panel receives trial ID, mode, role and anchor (lines 189–191). The
    acknowledgment deadline is fixed by trial type (line 193).
  - `AssessmentAcknowledgmentView.cs` line 27 shows the same text for every
    outcome.
  - **OK. See E2.**
- **`Assessment/AssessmentStages.cs`**
  - Lines 170–195: the speech permit requires the delayed test, the protected
    blocks and the forms to be complete.
  - Lines 213–229: the dictionary permit requires the optional stage, which
    itself requires forms and validity to be complete.
  - **OK. See E1.**
- **`Assessment/AssessmentScripts.cs`** and `AssessmentScreen.ShowInstruction`
  - Show block-level reviewed text. Braces are rejected, so no template
    substitution is possible.
  - **OK.**
- **`Assessment/PostStudyActivities.cs`**
  - Nothing in runtime constructs it, and no `IPostStudyExecution`
    implementation exists.
  - **OK.**

### Audio routing

- **`StudyAudio/AudioPlayer.cs`**
  - Lines 67–75 fix spatial blend 0, pan 0, pitch 1, no mixer, and bypass
    listener and reverb. Lines 141–151 re-check this before every schedule and
    every frame.
  - Gain is the per-person comfort gain (lines 84–89; `ComfortableGain`,
    `AudioCalibrationHost`). It is not set per trial.
  - **OK. See U1.**

### Logger, data journal and exports

- **`SessionIntegration/SessionIntegrationOwner.cs`** lines 31–38 record each
  cue's file and PCM hash.
- **`DataLogging/DataContracts.cs`** lines 134–139 carry PCM, action and
  referent hashes.
- **`DataLogging/DataDeriver.cs`** lines 21–22: the trial and exposure headers
  carry `waveform_sha256`, but no target or correctness columns.
- **Lesson journals** (`LessonDataJournal.cs`, `LessonExport.cs`,
  `JoinedAudit.cs` lines 28–32) carry `meaning_display_id` for lessons only.
- All of these sinks are write-only files, and none of them is rendered.
- **OK for the participant. See C3.**

### Teaching, selection menus and orientation

These show meaning on purpose. They are not protected.

- **`Teaching/TeachingCatalog.cs`**
  - Keeps the parsed permutation (lines 51, 88, 94). `Prepare` (lines 139–164)
    reads the expected action and target for feedback.
  - `Validate` (line 135) requires `Phase == "teaching"` and `!Heldout`.
    `TeachingContent.cs` line 55 calls it before any content is built.
  - The dictionary and selection readers (lines 167–179) are permit-gated.
  - **OK. See E3 and C1.**
- **`SelectionMenus/MenuCatalog.cs`** lines 51–88
  - Reads atom meanings through a single-use `MeaningPermit`, for non-heldout
    selection items only (line 80). DEMO only (line 48).
  - **OK.**
- **Orientation** (`Orientation/OrientationPlan.cs`, `OrientationFlow.cs`,
  `OrientationHost.cs`)
  - Uses public practice pairs from a separate preallocation plan. There is no
    package or schedule input.
  - **No hidden-tuple reader.**

### Isaac command API, test lock, demos and state mirror

- **Unity sender**
  - `StateIntegration/PrivateModeResetClient.cs` lines 97–113 send only
    `set_mode`, `reset` and health probes. Unity runtime sends no `demo`
    command and no action or target argument.
  - **OK.**
- **`isaac/commands/protocol.py`** lines 34–39: `target_bearing` flags any
  `demo` command and any nested `action` or `target` key.
- **`isaac/commands/dispatcher.py`**
  - Lines 141–144 reject `PROTECTED_TARGET_COMMAND` in test mode, before schema
    dispatch and before replay lookup.
  - Lines 244–250 interrupt an active demo on entry to test mode.
  - Lines 186–189 call the demo factory only outside test mode.
- **`isaac/commands/queue.py`** lines 100–112 reject pending demo and unlock
  requests after a test-mode acknowledgement.
- **`isaac/e2e/service.py`** line 201 has no demo factory, so the robot stays
  neutral during teaching.
- **Lock probes** (`isaac/view_capture/lock_probe.py` lines 108–114,
  `isaac/e2e/fault_check.py` line 278) iterate over the public legal-pair
  constants. They do not use trial data.
- **`isaac/publisher/protected_stream.py`** holds the robot neutral.
- **Isaac overall**
  - Isaac never loads a schedule, package or answer file.
  - Its event log keeps the raw arguments of rejected probes as private
    engineering evidence.
  - **OK. See U3.**
- **State sources and bridge** (`StateSources/*`, `StateIntegration/*`,
  `Workcell/*`)
  - No schedule or package references. They render the neutral Isaac state.
  - **No hidden-tuple reader.**

### Operator console

- **Unity side**: `OperatorConsole/OperatorMailbox.cs` lines 130–142 build
  snapshots from hashes, engine state, block counts and health.
- **`ops/console/core.py`**
  - `load_bundle` (lines 123–177) parses the private schedule for identity and
    block and run-sheet counts only, and does not keep it.
  - Snapshots and the audit pass through `masked()` (lines 276–314).
- **`ops/console/static/app.js`** renders only that snapshot.
- **OK.**

### Simulation and fault-injection hooks

- **`SessionIntegration/SimulationInputDriver.cs`** (lines 12–14, 33–36)
  injects fixed dummy inputs. There is no answer lookup.
- **The compile gate**
  - `Foundation/SimulationTestAuthority.cs` (lines 19–28) gates this hook and
    the other `Simulation*` hooks: `CompiledCapability` is true only under
    `UNITY_EDITOR || AV_SIMULATION_TEST`.
  - The define is added only for `JoinedEngineeringBuild.BuildSimulationWindows`
    (via `FoundationBuild.cs` line 264).
  - `ProjectSettings` `scriptingDefineSymbols` is empty.
- **Other hooks**: `FrameCaptureHost.InjectStall` and
  `DataJournal.BeforeDurableFlush` do not read hidden data.
- **OK. See C2.**

### Offline analysis

- **Scoring and integrity**: `analysis/src/av_analysis/derive.py`, `scoring.py`,
  `analysis/integrity.py` and `analysis/command_scoring.py` read scoring keys
  after the fact. Nothing they produce is shown to the participant.
- **Masking**: `analysis/src/av_analysis/masking.py` masks hidden-answer
  columns by name. See C3.

### Checked, no reader found

- **Unity**: FrameBudget, Soak, Workcell, ResponsePanel (selection only;
  `ResponseState.cs` line 83), GrammarFamiliarization,
  ExclusiveContentMultiplexer and StagedModuleCoordinator (block level only),
  CompletedFormsRecovery, OperatorCommandJournal.
- **Isaac**: `isaac/reset`, `isaac/stations`, `isaac/soak`, `isaac/view_capture`.
- **Ops**: the transport, server and simulation modules of `ops/console`.
- **Tools**: `tools/quest_station.py`, `tools/deploy-quest.ps1`,
  `tools/view_leakage.py`, `tools/leakage_png.py`.

## Exemptions by design: the reviewer should record them

- **E1. Validity speech trials.**
  - The speech ID is `<family>-<action>-<referent>` (`ScheduleLoader.cs`
    line 168), and the cue speaks the command (`SpeechBank.cs`). The tuple is
    audible by design.
  - The permit gates it to the validity block, after the protected blocks and
    the forms are complete.
- **E2. Atomic probe role.**
  - The panel's labels show whether the probe is an action or a target sound.
    The role comes from the atom ID (`ProtectedSlots.cs` line 189).
  - The semantic label is not shown.
- **E3. Lessons and selection menus.**
  - These show meaning on purpose.
  - They are guarded by phase and `!Heldout`, and the menus also by a
    single-use permit.

## Concerns: low severity, no leak path found

- **C1. The codebook stays in the same process.**
  - When a visit has teaching or menus, the Unity process holds the full
    message-to-meaning map during protected blocks, including heldout
    messages: `TeachingCatalog` line 51 and `JoinedVisitArtifacts` line 41.
  - The live content ID sits in the same process. Isolation rests on API
    discipline only (internal `Prepare`, `Validate` and permit gates), with no
    process or compile separation.
  - Hardening option: drop the semantic fields after teaching content is
    built, or load them per phase.
- **C2. Build records do not show whether `AV_SIMULATION_TEST` was defined.**
  - Neither `BuildIdentity.json` nor `*.build.json` records it
    (`FoundationBuild.cs` lines 255–266). A simulation player cannot be told
    apart from a participant player by its artifacts alone.
  - The hooks do not read the tuple, but they can inject input.
  - Hardening option: record the define set in both files.
- **C3. Analyst blinding.**
  - This is not a participant-view issue.
  - `waveform_sha256` and the PCM hashes in the trial and exposure exports map
    to a message ID through the package `audio.json`, and from there to meaning
    through `answers.json`. Name-based masking does not catch this.

## Needs a runtime or native check

- **U1. Audio sources in the player.**
  - Confirm the built player has exactly one isolated `AudioSource`, with no
    listener effects and no mixer.
  - The build-time scene allowlist (`FoundationBuild.cs` lines 165–236) and the
    runtime `CheckSource` enforce this today. `CheckSource` inspects only its
    own GameObject.
- **U2. No test assemblies in the player.** Confirm that no test assembly
  appears in the player build's output file list.
- **U3. Neutral publisher frames.** In a native run, confirm that the Isaac
  publisher frames stay neutral in test mode.
- **U4. Main-thread timing side channel.**
  - Novel and speech trials build the `AudioClip` on the main thread at cue
    request. Speech trials also hash the WAV files and scan the peak.
  - A hitch that tracks heldout or speech status (not target or action) could
    follow. Check the frame evidence for these trial types.
- **U5. No protected participant entry point yet.**
  - `JoinedEngineeringBootstrap.ParticipantAdmission` is false, and the
    assessment scene host is not installed for participants.
  - Repeat this review when a participant-admitting host is added.
