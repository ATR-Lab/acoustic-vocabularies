# O6.1.2 hidden-tuple code-path review checklist (draft)

Refs #79. **Status: draft, unsigned.** This checklist lists every component in
this repository that can read, carry, log or render a protected trial's hidden
answer tuple (action, target, or any field derived from them, such as content
IDs, audio IDs, answer keys or correctness). It is input for the independent
review required by #79 ("signed by a reviewer other than the author"). Nothing
here is a finding, an approval or G3 evidence. Pixel, state and routing
equality from `tools/view_leakage.py` does not substitute for this review.

## How to use

1. Review at one pinned commit. Record it below before starting; a later commit
   needs a new review of every changed path.
2. For each row, read the listed files (and anything they call that is not
   listed), answer each "verify" item, and record evidence: file:line,
   test name or retained private log reference. Do not paste private study
   content, answer keys or participant data into this document.
3. A row is closed only when every item is answered *yes* with evidence, or
   a finding is opened and later closed by a separately reviewed change.
4. "Hidden tuple" below means the protected trial's action/target pair and any
   value from which it can be recovered (content ID, trial-to-answer mapping,
   audio file identity, correctness, score, response key expectation).

Commit reviewed: `__________` Author of the reviewed changes: `__________`

## 1. Session engine and protected trial boundary

Files: `unity/Assets/ExperimentApp/Runtime/SessionEngine/FixedSlotEngine.cs`,
`ScheduleLoader.cs`, `SessionContracts.cs`, `SessionJournal.cs`,
`ContentCueGate.cs`; `unity/Assets/ExperimentApp/Runtime/Assessment/ProtectedSlots.cs`,
`AssessmentSessionHost.cs`, `AssessmentStages.cs`, `UnityAssessmentPorts.cs`,
`AssessmentScripts.cs`.

- [ ] Before cue onset, no protected slot passes the hidden tuple, content ID or
      trial type to the view, panel, renderer, state source or private control.
- [ ] `ProtectedSlot.Prepare` only begins the trial and prepares audio; the view
      shows `Neutral()` and the same instruction text for every protected item.
- [ ] Readiness (`SlotReadiness`) depends only on neutral/mode/focus/panel/audio
      readiness, never on which target is pending.
- [ ] Faults, interrupts and the neutral `Response recorded` acknowledgment are
      identical for right, wrong and timeout outcomes (timing included).
- [ ] Schedule loading does not expose a per-trial answer to any presentation
      component or to the operator-visible status codes.

Reviewer notes / findings:

## 2. Scene and robot state mirror

Files: `unity/Assets/ExperimentApp/Runtime/StateSources/LiveSocketClient.cs`,
`StateParser.cs`, `SceneFrame.cs`, `SnapshotSource.cs`, `StateSources.cs`,
`StateSourceJournal.cs`; `unity/Assets/ExperimentApp/Runtime/StateIntegration/StateSourceHost.cs`,
`WorkcellStateRenderer.cs`; `unity/Assets/ExperimentApp/Runtime/Workcell/WorkcellRegistry.cs`;
`isaac/publisher/protocol.py`, `isaac/publisher/runtime.py`, `isaac/workcell/state.py`.

- [ ] The public frame schema has no target, action, trial, content or answer
      field; unknown fields are refused by the Unity parser.
- [ ] Interpolation/hold logic and visibility state cannot vary with a pending
      trial (they read only frame contents and local receipt times).
- [ ] Neutral comparison and reset confirmation use only the pinned neutral
      snapshot, not trial-specific expectations.
- [ ] Robot appearance outside the registered workcell material/light accessor
      (G1 meshes, hand pose, highlights) is not trial-conditioned.

Reviewer notes / findings:

## 3. Command API test lock (#55)

Files: `isaac/commands/dispatcher.py`, `protocol.py`, `queue.py`, `transport.py`,
`event_log.py`, `health_probe.py`, `hold.py`;
`unity/Assets/ExperimentApp/Runtime/StateIntegration/PrivateModeResetClient.cs`,
`PrivateControlExchange.cs`, `PrivateHealthProbe.cs`, `ControlHealthGate.cs`.

- [ ] In test mode every target-bearing command (`demo`, including replays of
      earlier accepted teaching demos) is rejected with
      `PROTECTED_TARGET_COMMAND` before any simulator mutation, and is logged.
- [ ] Switching to test mode interrupts any active demo before reset.
- [ ] The Unity private client can only send `set_mode` and `reset`; no code
      path builds a `demo` request from a protected trial.
- [ ] The durable command log is private; rejected requests carrying a probe
      pair cannot reach the public stream, renderer or participant view.
- [ ] `TryGetAcceptedResetReply` (added for capture evidence) only returns an
      already validated accepted reset and grants nothing.

Reviewer notes / findings:

## 4. Kinematic demos (#56)

Files: `isaac/stations/audit.py` and the demo trajectory sources on the
reviewed commit (the #56 harness may live on an unmerged branch; record which).

- [ ] Demo execution is reachable only in teaching mode through the lock above.
- [ ] No demo trajectory, gaze, hand pose or object highlight remains active or
      partially applied when test mode and the neutral reset complete.
- [ ] Teaching-mode demo history cannot be replayed into a protected block.

Reviewer notes / findings:

## 5. Audio routing and cue preparation

Files: `unity/Assets/ExperimentApp/Runtime/StudyAudio/AudioPlayer.cs`,
`PackageLoader.cs`, `SpeechBank.cs`, `MessageComposer.cs`, `AudioTiming.cs`,
`AudioDelivery.cs`, `ComfortableGain.cs`; `UnityAssessmentPorts.cs` (audio port).

- [ ] The single `AudioSource` stays nonspatial (spatial blend, pan, doppler,
      spread zero) with fixed volume/pitch, no mixer group and no effects for
      every protected trial (`CheckSource`).
- [ ] Preloading a protected cue does not change any visible object, text,
      panel state or routing before onset.
- [ ] Cue scheduling, gating and fault codes do not differ by target except for
      the waveform itself.
- [ ] Saved settings are not treated as an acoustic measurement; acoustic route
      confirmation is recorded separately.

Reviewer notes / findings:

## 6. Logging and data export

Files: `unity/Assets/ExperimentApp/Runtime/DataLogging/*`,
`unity/Assets/ExperimentApp/Runtime/SessionEngine/SessionJournal.cs`,
`unity/Assets/ExperimentApp/Runtime/ResponsePanel/PanelJournal.cs`,
`unity/Assets/ExperimentApp/Runtime/FoundationLog.cs`,
`unity/Assets/ExperimentApp/Runtime/SessionIntegration/JoinedAudit.cs`,
`unity/Assets/ExperimentApp/Runtime/FrameBudget/FrameCaptureHost.cs`.

- [ ] Logs that may contain the hidden tuple are private and never drawn in the
      participant view or published on the public stream.
- [ ] No synchronous log write, failure or flush timing differs by target in a
      way that changes what is rendered before cue onset.
- [ ] View evidence records hashes/text that are identical across protected
      trials (`assessment_view_command`).

Reviewer notes / findings:

## 7. Operator console and mailbox

Files: `ops/console/core.py`, `server.py`, `simulation.py`, `transport.py`;
`unity/Assets/ExperimentApp/Runtime/OperatorConsole/OperatorMailbox.cs`,
`OperatorContracts.cs`, `OperatorCommandJournal.cs`.

- [ ] The console never renders into the participant view; no console or
      diagnostic overlay can appear in captured frames.
- [ ] Operator status and health shown during protected blocks contain no
      target, answer or correctness field.
- [ ] Operator commands cannot inject a target-bearing request in test mode.

Reviewer notes / findings:

## 8. Presentation, panel and simulation-only inputs

Files: `unity/Assets/ExperimentApp/Runtime/ResponsePanel/ResponsePanelController.cs`,
`ResponseState.cs`; `unity/Assets/ExperimentApp/Runtime/Assessment/AssessmentScreen.cs`,
`AssessmentAcknowledgmentView.cs`; `unity/Assets/ExperimentApp/Runtime/SessionIntegration/JoinedEngineeringBootstrap.cs`,
`SimulationInputDriver.cs`; `unity/Assets/ExperimentApp/Runtime/FoundationBootstrap.cs`.

- [ ] The response panel layout, labels, highlights and focus are identical for
      every protected trial until the participant acts.
- [ ] `SimulationInputDriver` (SIMULATION_TEST only) uses fixed dummy inputs and
      never reads the trial answer.
- [ ] The `SIMULATION TEST` watermark exists only in the explicitly compiled
      simulation player and is constant across trials.

Reviewer notes / findings:

## 9. View-capture driver and offline tools (this check's own code)

Files: `unity/Assets/ExperimentApp/Runtime/ViewCapture/ViewCaptureDriver.cs`,
`ViewCapturePlan.cs`, `ViewCaptureOutput.cs`, `ViewCaptureContracts.cs`,
`UnityCapturePorts.cs`, `ViewCaptureHost.cs`;
`unity/Assets/ExperimentApp/Editor/ViewCapture/ViewCaptureBuild.cs`;
`tools/view_capture_assemble.py`, `tools/view_leakage.py`, `tools/leakage_png.py`;
`isaac/view_capture/observations.py`, `lock_probe.py`.

- [ ] The driver passes the pair only to `IProtectedTrialBoundary`; rig, grabber,
      state readback, audio and view inventory APIs take no pair argument.
- [ ] Capture file names contain only the capture index; the pair appears only
      in the private manifest rows.
- [ ] The native host's `DriverOnlyTrialBoundary` loads no session-engine trial
      and schedules no audio; a run with it does **not** satisfy "load the trial
      through the real protected boundary". Record whether a reviewed boundary
      replaced it for the run being assessed.
- [ ] The capture assembly is excluded from ordinary players (define constraint
      `UNITY_EDITOR || AV_SIMULATION_TEST`) and refuses without a loaded
      SIMULATION_TEST capability and an acknowledged test-mode control session.
- [ ] The offscreen render uses the participant camera's scene and culling mask;
      confirm whether it can differ from the compositor/headset image (it does
      not capture headset eye buffers or OS overlays).
- [ ] The renderer inventory covers every visible renderer and text type used by
      the scene (it inventories `Renderer` and `TextMesh`; UI `Canvas` overlay
      presence is flagged, not inventoried).
- [ ] The live-state join uses the left-bracket frame identity and reset lineage;
      review its alignment with the visibly applied interpolated frame.
- [ ] No step retries, skips, resizes, normalizes or replaces evidence.

Reviewer notes / findings:

## Sign-off

| Field | Value |
|---|---|
| Reviewer (not the author) | |
| Reviewed commit | |
| Date | |
| Open findings | |
| Closed findings with fixing commits | |
| Signature / approval reference | |

Leave blank until an independent reviewer completes every row. An unsigned or
partially completed checklist keeps O6.1.2 at `HOLD`.
