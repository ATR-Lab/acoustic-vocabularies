# ADR-001 — Headset topology

Status: Accepted at G1 (#51) — wired USB Link (`pcvr_link`); device measurements
are acceptance conditions before the engineering pilot

## Context

WBS O5.1.8 / #50. One application must present the same seated public workcell on
Quest Pro with live state from a Linux Isaac host. Timing uses a host monotonic
clock and physical audio onset. Participants are seated and stationary for the
whole visit, so a tethered headset does not restrict the task.

## Options

1. Standalone Android ARM64 IL2CPP: render and play audio on Quest; state over Wi-Fi.
2. Windows Link: render on this PC; audio either at PC headphones or headset.
3. Air Link: a separate wireless PC-VR condition, not interchangeable with wired Link.

The Linux simulator host and Windows Link host are separate computers unless a
different tested hosting design is approved. Each Link station therefore needs a
Windows rendering PC in addition to its allocated Isaac capacity.

## Measurements

Required: #45 frame distributions/refresh/startup/loss tests, #47 rate/transport
matrix and #48 physical onset. The [audio harness](../../spikes/O5.1.6/audio_onsets.py)
and [runbook](../spikes/O5.1.6-runbook.md), merged in
[PR98](https://github.com/ATR-Lab/acoustic-vocabularies/pull/98), are not onset evidence. No route/topology timing CSV is available yet. Windows
x64 Mono/DX11 and Android ARM64 IL2CPP/Vulkan builds passed in Unity 6000.6.0f1,
including the aligned robot Android build in #46. Compilation proves buildability
only. Headset removal, sleep, Link loss
and actual compositor timing remain unmeasured.

## Decision

Use option 2, **wired USB Link** (station `topology: pcvr_link`). The Windows
player renders on one Windows PC per station and the Quest Pro shows it over a
USB-C Link cable. Study audio plays from that PC (ADR-005). Standalone Android
and Air Link are not used for any session with people. The Android target stays
buildable for engineering checks only; using it for sessions needs a superseding
ADR and a new apparatus version.

No headset measurement exists yet, so the choice rests on the evidence below,
not on frame timing:

1. The application is built and exercised on Windows. The operator mailbox
   publishes state with same-directory `MoveFileExW` replacement and fails closed
   on other platforms (`mailbox_platform_unqualified`, see
   [operator console interface](../interfaces/operator-console.md)). Native
   simulated visits ran a Windows player against the Isaac backend
   ([native simulation tests](../unity/native-simulation-tests.md)). Standalone
   would need a new console transport and a second qualification.
2. Live state stays on the wired station network between the Isaac host and the
   render PC. Standalone would put the 250 ms stale-state rule (ADR-002) on Wi-Fi
   to every headset; the unqualified diagnostic path in the
   [G1 readiness packet](../gates/G1-readiness.md) already showed gaps over 250 ms.
3. The imported 43-joint G1/Dex3 scene has only been compiled for Android (#46);
   its mobile frame cost is unmeasured. A desktop GPU leaves headroom for the
   frame budget in #74.
4. A PC audio route lets the request, the Commit and electrical loopback share
   one host monotonic clock, the simplest path to the ADR-005 onset target.

Accepted costs: one Windows render PC with a discrete GPU per station (ADR-003);
Link compositor latency and dropped frames, measured below; Link software and
headset OS updates must be deferred and recorded per station; a Link disconnect
is a fault that pauses before the next exposure.

## Acceptance conditions

These are measured at station install, before the engineering pilot. They do
not reopen the choice unless a revisit trigger fires.

- #45 runbook on Link with the real joined scene: offered rates, the chosen rate,
  a 900-second frame capture, no presentation freeze over 250 ms in cue or
  response windows, and removal, sleep and Link-disconnect callbacks.
- #47 bridge at the chosen rate over the station's wired network.
- The same checks on every installed station, recorded in its station file.

## Consequences

Station files use `topology: pcvr_link`. ADR-003 adds one render PC per station.
ADR-005 uses a PC-side route. #59 and #60 provision the Quest as a Link display
(developer mode, Link, update deferral); APK deployment steps are engineering
only. #74 measures frame timing on Link. Do not infer Air Link results from wired
Link or extrapolate the uncalibrated placeholder robot's triangle load to the
imported robot. Recheck the exact robot asset scene.

## Manifest fields

`headset_topology`, `unity_editor`, `openxr_version`, `meta_openxr_version`,
`render_host_class`, `refresh_hz`, `build_sha256`, `observer_reference`,
`audio_route`, `network_condition`, `frame_evidence_sha256`.

## Revisit trigger

Any runtime, refresh, network, audio route, asset load or host change after G1;
presentation freeze over 250 ms; or missed frame budget on the frozen scene.
