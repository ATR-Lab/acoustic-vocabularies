# OpenXR architecture spike

Issue #45 / O5.1.3. This is an engineering measurement scene, with no lessons,
study sounds, response correctness or production application flow.

Pinned editor: **6000.6.0f1 (f7f8ed4d1e24)**. It is the installed Unity 6.6 Supported
release, not an LTS. The installed editor meets the issue minimum and avoids a new
editor installation; prefer an approved LTS for the final foundation decision and
rerun measurements if the pin changes. See the [official release announcement](https://discussions.unity.com/t/unity-6-6-is-now-available/1735357).

Direct packages: OpenXR 1.18.0, Unity OpenXR: Meta 2.6.1, XR Management 4.7.0,
XR Core Utils 2.6.0, Input System 1.20.0, XR Hands 1.6.3, UGUI 2.6.0. Exact
resolved dependencies are in `Packages/packages-lock.json`. Unity's built-in UGUI
version for this editor is 2.6.0. Meta XR Core SDK and Oculus XR Plugin are absent.

Both build variants load `Assets/Spikes/OpenXR/Scenes/SeatedWorkcell.unity`.
`SeatedOrigin/CameraOffset/Main Camera` uses device tracking, a 1.2 m seated eye
offset and no locomotion. The origin stays fixed; ordinary tracked head motion is
preserved. An operator must recenter seated and verify alignment before each run.
The scene contains eight labeled stations, a static synthetic robot, and a visual
response panel. The panel has no response behavior in this spike. #49 adds its own
isolated input comparison. The 10,040-triangle scene is **not yet matched to the
G1/Dex3 mesh cost**; repeat timing after #46 supplies representative geometry.

Android: ARM64 / IL2CPP / Vulkan. Windows Link: x64 / Mono / Direct3D 11. Both use
OpenXR, the Meta feature group, single-pass rendering, display utilities, hand
tracking, Touch/Touch Pro profiles and the session observer. Optional AR features
are disabled. Windows Mono uses the installed player module; IL2CPP installation
is not required for this architecture spike.

Use the [runbook](../../../docs/spikes/O5.1.3-runbook.md) to configure, build,
collect and summarize measurements. `Configure` only creates the scene if missing;
build methods preserve scene additions from the audio and input spikes. The
explicit `Recreate placeholder scene` menu replaces the scene and should only be
used when regeneration is intended.

Frame timing is application `Update` interval timing with host `Stopwatch` ticks,
Unity realtime and optional `FrameTimingManager` values. A 250 ms application gap
does not prove a 250 ms presentation freeze. Pair it with compositor metrics and
headset capture. The start anchor is Unity's `BeforeSplashScreen` callback, not OS
process creation. Unknown refresh rates or unavailable timing counters stay unknown.
