# Frame timing contract

Software and native build evidence is recorded in [validation.json](validation.json),
with [Windows file hashes](windows-build.json) and [Android file hashes](android-build.json).
These records distinguish the Unity build report size from the actual packaged file size.

`AcousticVocab.FrameBudget` instruments explicitly registered attempt windows. A
trusted session owner wraps its content factory with `FrameContentFactory` and
composes `FrameAudioAdapter.Record` into its durable audio event sink, resolving
PCM sample counts from the already verified loaded wave. The wrapper uses the
participant choice offsets (`ChoiceOpensSeconds`/`ChoiceClosesSeconds`). Menu
choice windows remain atom 22–32 s and profile 30–45 s, independently of the later
engine close/reset boundary. Every actual audio request separately binds
its cue duration, including the final lesson presentation. No study timing,
selected rate, wave, allocation or answer is invented by the monitor.

Raw rows identify a host render callback, its full interval, a cue/response
window and the clipped overlap. A positive overlap charges the full interval to
that attempt conservatively. An exact boundary touch contributes nothing. Old
attempts remain registered through their end, even when the engine prepares the
next item. Overlapping cue/response rows share the same frame index; the summary
counts a frame once per attempt. `frame_freeze_ms` is the maximum, never a sum.
Missing capture remains blank and incomplete, not zero.

The strict engineering threshold is **greater than 250 ms**. Exactly 250 ms and
200 ms are recorded without a freeze fault; 300 ms faults. Input unavailability
within a response window is a separate `FRAME_INTERFACE_UNAVAILABLE` fault. The
monitor never reads selected answers or correctness. A watchdog observes missing
render callbacks on a background timer and latches readiness; Unity/engine and
study-journal callbacks are dispatched on the main thread when it next runs.
This cannot promise an instantaneous Unity API call while that thread is stalled.
Observed fault timestamps and dispatch behavior remain distinguishable, and timer
poll spacing is a request to the OS, not a hard real-time guarantee.

Raw CSV writes are buffered to avoid an fsync per render frame. Faults and attempt
summaries flush the raw prefix before they are exported to the study journal.
Directories are create-new; partial/crashed evidence is retained and never
repaired into a complete run. The unified data adapter appends typed device
observations and projects only bounded health into the operator console. Missing
rate confirmation, missing baseline/cue bindings, interrupted admitted capture and logger
failure block readiness.

## Clocks and rates

`Application.onBeforeRender` intervals are application render callbacks on the
per-process Stopwatch clock, with a recorded capture clock epoch. They are **not measured photon-presentation
intervals**. Unity CPU/GPU statistics are delayed and nullable. Runtime refresh
rate, present/dropped counters, render texture dimensions, Unity physics step size
and observed step counts are separate fields. Isaac physics rate and physical
panel resolution remain unknown unless supplied by independently measured
apparatus evidence; neither is derived from a requested frame rate.

The selected refresh rate comes from the validated station configuration.
`request` checks the runtime-offered list, makes one request before a visit, then
requires three matching reports. `require_current` makes no request and verifies
the operator-configured runtime rate; an unavailable offered list is preserved as
unknown, not inferred. Once confirmed, any rate change or loss faults. No rate
change is attempted during a visit. The setup's protocol/station IDs must match
Foundation exactly. A runtime acknowledgment does not qualify physical timing.

Unity's [Meta Display Utilities](https://docs.unity.cn/Packages/com.unity.xr.meta-openxr%402.6/manual/features/display-utilities.html)
provides offered-rate queries and requests. Its
[API contract](https://docs.unity.cn/Packages/com.unity.xr.meta-openxr%402.1/api/UnityEngine.XR.OpenXR.Features.Meta.MetaOpenXRDisplaySubsystemExtensions.html)
notes that an accepted request takes effect on a subsequent frame. The installed
2.6.1 source is the build authority. Unity's
[frame timing documentation](https://docs.unity.com/en-us/engine/6000.7/script-reference/unityengine/frametimingmanager)
explains delayed CPU/GPU results and release-build statistics configuration.

Physical Quest timing, full-scene optimization, a chosen/frozen operating rate,
three-volunteer legibility and comfort checks, and apparatus sign-off remain
pending. Simulator or virtual-clock results must carry their source labels.

## Integration and ownership

`SessionIntegrationOwner` constructs the sole `FixedSlotEngine` around the final
frame wrapper and `ExclusiveContentMultiplexer`. It reuses a caller-owned
`DataJournal`, creates one persistent `AssessmentDataJournal` view, and installs
one panel adapter. It does not load a package or certify admission. The real
module remains the sole `AudioPlayer.Event` observer; its durable sink composes
`AudioDataAdapter` in non-subscribing mode and `FrameAudioAdapter` exactly once.

The trusted joining owner supplies a reviewed block-to-creator map. A creator
receives `Resources` and a `ModuleConstructionScope`; it registers cleanup before
any side effect that may throw, then returns its `IDisposable` factory. The
returned factory is automatically owned. Existing assessment and teaching host
installation methods accept `beforeSchedule: resources.BindAudio`; their actual
ports call this with the already verified, permitted waveform immediately before
`AudioPlayer.Schedule`. Pass `resources.DurableAudioSink` to that module's audio
sink. No additional PCM read or novel/speech permission is introduced.

Call the mailbox constructor with `prepareResume: owner.PrepareResume`. The hook
runs after the durable command request and before `ConfirmResume`; it never
auto-resumes. A block switch is allowed only at an explicit engine boundary after
the preceding **requested** visual tail ends. The old lease is disposed before
creation begins; failed cleanup/creation latches blocked. An interrupted lease
must be replaced even on same-block Resume: its control client is permanently
interrupted and its prepared request IDs cannot be reused. Creators construct a
fresh backend, factory and host/screen, registering `host.Uninstall` in their
construction scope before `Install`. Uninstall clears engine/factory ownership
before cleanup; deferred Unity disable/destroy/focus callbacks cannot abort the
next owner's shared player. Retain the owner-level stage journal and reconstruct
stage history in each fresh assessment host. The retained factory is
still pumped through acknowledgment/lesson tails. The selected factory's
`ISlotStartPlan` is forwarded, preserving verified yoked timing anchors. The
mailbox remains the only `engine.Tick` driver.

Fresh control readiness is a separate staged admission operation at a safe
boundary. In particular, assessment installation requires an already acknowledged
`test` mode. The synchronous mailbox prepare hook does not wait for or manufacture
that asynchronous handshake. A trusted bootstrap must stage and verify a fresh
backend before explicit Resume, then transfer its ownership to the new scope;
unready installation fails closed. That concrete bootstrap is still pending.

Preparing an unadmitted future slot does not reserve a visual tail. A pre-cue
pause records a raw `cancelled_before_window` summary, keeps its absent interval
blank, and allows explicit replanning of the still-unplayed opportunity. A pause
after a cue request remains an interruption fault. Earlier active tails are not
removed when a later unadmitted plan is cancelled.

The scene builder includes the assessment, G1 state, panel and capture components.
The explicit owner API is ready for trusted module creators; complete participant
admission, the selection-menu host, qualified orientation recordings, final
materials and physical audio calibration are still integration prerequisites.
No scene auto-installs those authorities. This is not a qualified complete visit.

The raw frame bundle and unified journal are retained together. A typed dispatch
failure attempts all already raw-backed fault records/notifications before
latching; raw evidence remains the recovery authority and an incomplete typed
prefix must not be represented as complete. Existing data exports do not silently
embed or upload raw frame bundles.

## Measured simulator probe

[Public callback CSVs, hashes and limits](simulator-callbacks/validation.json) and
[the interval plot](simulator-callbacks/intervals.svg) describe actual native
Unity callbacks with the recorded G1 snapshot and engineering panel. The short
baseline, 200 ms injection and 300 ms injection are separate runs. They are not
the proposed live 36-trial headset block. The requested sleeps yielded observed
maxima of 242.7 ms and 333.3 ms respectively; only the latter raised a freeze.

The separately named `FrameProbe` build has no session or audio authority. Supply
`-frameProbeOutput <fresh-directory> -frameProbeStall 0|200|300` only in an
engineering simulator run. It waits for real Foundation/source/panel readiness,
captures the actual rendered scene, uses synthetic windows and exits. Build it
with the existing wrapper `-Target Windows -Scene FrameProbe`; participant
capture scenes use `-Scene FrameBudget`. Raw logs/configuration/screenshots remain
private. The probe's camera capture is supplementary; inspect the actual native
XR mirror and log camera pose/FOV before making layout judgments.

The initial native mirror confirmed a real preview defect: a level tracked head
left the workcell low in view, and the panel overlapped the G1. A bounded private
configuration trial moved the panel's first row from −0.18 m to eye level, enlarged
common glyph height from 0.6° to 1.0°, and widened buttons from 0.135 m to 0.18 m.
Distance (0.7 m), button height (0.065 m), gap (0.012 m), observer calibration and
actual tracked pitch were preserved. All values remain inside the authoritative
response-panel schema. Both all-target and all-action views rendered with this
common setting; the earlier 1.2°/0.135 m action trial correctly hit the fit guard.
These are provisional preview values, not changed protocol defaults. The G1 head
is visible but the Commit row still overlaps its shoulders/upper torso. This is
an unresolved layout acceptance defect, and neither screenshot is a physical
legibility or comfort pass. Native mirror cropping and the supplementary 16:9
camera render differ; the recorded XR eye texture is 1440×1584 with a 96° vertical
FOV. See [pose, configuration deltas and image hashes](simulator-callbacks/layout-preview.json).
