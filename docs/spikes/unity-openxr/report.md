# Unity / OpenXR spike report — O5.1.3

Status: **incomplete; needs operator runs**. No headset timing, refresh-rate,
connection-loss or wearer observations have been collected. No Quest was attached
when device availability was checked. The methodology copy remains pending review.

## Software and reproducibility

Unity 6000.6.0f1 (f7f8ed4d1e24), OpenXR 1.18.0, Unity OpenXR: Meta 2.6.1,
XR Management 4.7.0, Core Utils 2.6.0, Input System 1.20.0, XR Hands 1.6.3,
UGUI 2.6.0. No Meta XR Core SDK or Oculus XR Plugin. The exact dependency graph
and settings are committed with the project. SDK build/platform tools 36.0.0,
NDK r27c (27.2.12479018), OpenJDK 17.0.18+8 were available on the build station.

The editor is a Supported release, rather than the preferred LTS. Decision:
use the already installed exact version for this spike; approval is needed before
installing a different editor. Revisit the production pin at G1 and rerun tests if
changed. [Official Unity release description](https://discussions.unity.com/t/unity-6-6-is-now-available/1735357).

## Build evidence

Headless package resolution and scene configuration succeeded. The Windows x64
Mono/Direct3D 11 development player built with zero build errors. Android build
status is recorded in the final validation update below. Three synthetic analysis
tests verify percentiles, unknown rates, monotonicity rejection and incomplete
runs; these fixtures are not device measurements.

Both builds select the same scene. Its synthetic geometry totals 10,040 triangles
and has not been matched to G1/Dex3; real robot load validation remains pending #46.
The panel has 8 target labels, 4 generic action labels and Commit, with interaction
reserved for the independent #49 spike. No meaningful study labels are included.

## Measured comparison

| Variant | Offered / actual Hz | Duration | Mean / p95 / p99 / max ms | Over budget | App gaps >250 ms | Presentation freezes >250 ms | Start-to-ready |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Standalone | pending | pending | pending | pending | pending | pending | pending |
| USB Link | pending | pending | pending | pending | pending | pending | pending |
| Air Link | pending | pending | pending | pending | pending | pending | pending |

App-frame gaps are not presentation freezes. Runtime compositor evidence and
captures are required for the latter. The app readiness timestamp is anchored to
the managed `BeforeSplashScreen` callback; OS process start is a separate metric.

## Station impact and remaining work

Meta's [official requirements](https://www.meta.com/help/quest/articles/headsets-and-accessories/oculus-link/requirements-quest-link/)
specify Windows 10/11 for Link. With Isaac pinned to Ubuntu, simultaneous Link
operation needs a separate Windows rendering host per station, with a supported
GPU and the chosen USB or wireless transport. Multi-station capacity and costs
remain decisions for ADR-001/003 after actual measurements.

Need wearer: scene alignment, comfort and labels. Need device runs: each offered
refresh rate for 15 minutes, headset removal/sleep/Link disconnect callbacks,
short captures and compositor metrics. Follow [the runbook](../O5.1.3-runbook.md).
The available callback names are implemented; their mapping to physical events is
unobserved. Standalone versus Link remains undecided.
