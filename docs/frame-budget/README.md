# Frame timing contract

`AcousticVocab.FrameBudget` instruments explicitly registered attempt windows. A
trusted session owner wraps its content factory with `FrameContentFactory` and
composes `FrameAudioAdapter.Record` into its durable audio event sink, resolving
PCM sample counts from the already verified loaded wave. The wrapper uses the
engine's existing response offsets. Every actual audio request separately binds
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
rate confirmation, missing baseline/cue bindings, interrupted capture and logger
failure block readiness.

## Clocks and rates

`Application.onBeforeRender` intervals are application render callbacks on the
absolute host Stopwatch clock. They are **not measured photon-presentation
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
