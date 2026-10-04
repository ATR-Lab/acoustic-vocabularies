# ADR-001 — Headset topology

Status: Proposed — decision pending measurements

## Context

WBS O5.1.8 / #50. One application must present the same seated public workcell on
Quest Pro with live state from a Linux Isaac host. Timing uses a host monotonic
clock and physical audio onset. The private protocol sources have not yet been
located; issue constants are provisional until reconciled.

## Options

1. Standalone Android ARM64 IL2CPP: render and play audio on Quest; state over Wi-Fi.
2. Windows Link: render on this PC; audio either at PC headphones or headset.
3. Air Link: a separate wireless PC-VR condition, not interchangeable with wired Link.

The Linux simulator host and Windows Link host are separate computers unless a
different tested hosting design is approved. Each Link station therefore needs a
Windows rendering PC in addition to its allocated Isaac capacity.

## Measurements

Required: #45 frame distributions/refresh/startup/loss tests, #47 rate/transport
matrix and #48 physical onset. [Audio harness PR](https://github.com/ATR-Lab/acoustic-vocabularies/pull/94)
is not onset evidence. No route/topology timing CSV is available yet. Windows
x64 Mono/DX11 and Android ARM64 IL2CPP/Vulkan builds passed in Unity 6000.6.0f1,
including the aligned robot Android build in #46. Compilation proves buildability
only. Headset removal, sleep, Link loss
and actual compositor timing remain unmeasured.

## Decision

Keep both standalone and wired Link candidates through the measurements. No
winning topology is selected. Use the same geometry/assets and fixed observer
reference, comparing offered versus selected refresh and all frames over budget.
Treat presentation freezes over 250 ms as faults and measure their occurrence.

## Consequences

#59, #62, #64 and procurement remain constrained by pending topology. Do not
infer Air Link results from wired Link or extrapolate the uncalibrated placeholder
robot's triangle load to the imported robot. Recheck the exact robot asset scene.

## Manifest fields

`headset_topology`, `unity_editor`, `openxr_version`, `meta_openxr_version`,
`render_host_class`, `refresh_hz`, `build_sha256`, `observer_reference`,
`audio_route`, `network_condition`, `frame_evidence_sha256`.

## Revisit trigger

Any runtime, refresh, network, audio route, asset load or host change after G1;
presentation freeze over 250 ms; or missed frame budget on the frozen scene.
