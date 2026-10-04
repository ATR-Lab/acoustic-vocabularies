# O5.1.2 — fixed-base Isaac spike

Status: **600-second headless run passed on the approved nonbaseline host**.
Ubuntu 22.04 qualification, visible viewport testing, runtime DDS observation and
the remaining checks below are open. This is not G1 approval.

## Measured result, 2026-10-04

`G129_CFG_WITH_DEX3_BASE_FIX` loaded with a fixed base and **43 measured joints:
29 body, 7 left-hand, 7 right-hand**. It completed 83,308 physics steps over
600.0011 seconds of host-monotonic time and exited with code 0. The log contained
no `Error` or traceback entries; it reported upstream actuator deprecation
warnings. Physics dt was 1/60 second; the loop ran faster than real time.

| Headless, one articulation | Mean | p95 | Maximum |
| --- | ---: | ---: | ---: |
| Full wall-clock step interval | 7.2019 ms | 7.7469 ms | 12.7667 ms |
| Physics `sim.step` call duration | 5.1635 ms | 5.6203 ms | 7.7015 ms |
| GPU total memory used | 3,336 MiB | 3,336 MiB | 3,336 MiB |
| Simulator process RSS | 3,355.23 MiB | 3,358.43 MiB | 3,358.55 MiB |
| CPU, percent of one core | 208.60% | 213.15% | 218.45% |

There were 580 resource samples. GPU baseline was 1,019 MiB immediately before
this run; an existing compute process remained running. GPU totals include that
workload and are not per-process allocations. No frames were rendered; render
intervals are absent. Sampling adds unquantified overhead. The host has 64 logical
CPUs; 208.60% means approximately 2.09 cores, not 208.60% of the entire machine.

Evidence: [inventory](joint_inventory.csv), [hashes](asset_hashes.csv),
[resources](resources.csv), [step timestamps](headless-steps.csv),
[configuration](headless-run.json), [completion](headless-completion.json),
[summary](headless-summary.json). Runtime harness commit: `2ff8f2f`, with its
source hash recorded. Full intervals were subsequently derived from consecutive
timestamps by `evidence.py` at `f8157c2`. The manifest covers 562 files, including
loaded USDs, imported configuration and the bundled MDL tree used by the material.

## Apparatus and installation

The [initial read-only inventory](../../../apparatus/spikes/O5.1.2/host-inventory.json)
records Ubuntu 24.04.5, Quadro RTX 6000 with 24,576 MiB VRAM, 125 GiB RAM
(rounded), two Xeon Silver 4216 CPUs and NVIDIA driver 570.211.01.
The official image contains Ubuntu 24.04.2 and Isaac Sim build
`5.1.0-rc.19+release.26219.9c81211b.gl`. Both OS versions differ from the required
22.04 pin. The older Quadro has enough nominal VRAM but is not an RTX 4080-class
or Unitree-tested GPU. The user approved this nonbaseline feasibility run.
It does not qualify the prescribed apparatus or another GPU's hosting capacity.

A separate task image was prepared without changing the host installation or
stopping existing workloads. Isaac Lab is 0.54.3 at the pinned commit below.
The image has Python 3.11.13, PyTorch 2.7.0+cu128 and websockets 12.0. Its measured
derived image ID is
`sha256:6121207fd77c365293aa7d981f8cba88bd0a3b4cdcf380e2e97019b2ca8922c3`.
The base image digest and source pins are in `spikes/O5.1.2/pins.json`.
The [Dockerfile](../../../spikes/O5.1.2/Dockerfile) records the installation recipe;
the derived image is local, not a public registry artifact.

Early smoke attempts found missing Git and h5py, added only inside the task image.
USD's scanner cannot resolve bare MDL module names, so the harness resolves unique
bundled Kit modules and hashes the MDL source tree. Graceful Kit shutdown stalled
in a short smoke; the final harness flushes outputs before the supported immediate
framework release. The final 600-second process exited normally. Early attempts
are not counted as 600-second passes.

## Provenance and license

The [Unitree 5.1 instructions](https://github.com/unitreerobotics/unitree_sim_isaaclab/blob/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/doc/isaacsim5.1_install.md)
pin Isaac Lab to `80094be3245aa5c8376a7464d29cb4412ea518f5`.
Unitree code is `e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc`, under
[Apache-2.0](https://github.com/unitreerobotics/unitree_sim_isaaclab/blob/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/LICENSE).

The [pinned dataset card](https://huggingface.co/datasets/unitreerobotics/unitree_sim_isaaclab_usds/blob/394cf2448f8a9ed815c77c701a761f3d1ff1c8fb/README.md)
declares `apache-2.0`, checked 2026-10-04. The downloaded archive matched
1,305,090,539 bytes and SHA-256
`06fbf14549be3a81e3dbd4a8a019e7f060f48de1556b70f291a18ac8f9ead4b0`.
No extracted filename matched LICENSE, NOTICE, COPYING, COPYRIGHT or README.
This establishes the observed declaration and absence of bundled notice files,
not an independent chain-of-title audit.

Decision: use the declared Apache-2.0 dataset for this local engineering evaluation
and retain revision/hashes for study provenance. **Do not commit USDs**, as requested.
Public-repo redistribution of the asset bundle remains no-go by project policy.
No license conflict was observed. The BSD-3 URDF conversion remains a fallback if
later source information contradicts the declaration.

## Isolation and DDS

All simulations used Docker `--network none`, checked for only loopback before
importing Isaac/Unitree configuration. The namespace had an empty route table.
The direct configuration probe starts **no DDS** and does not change host networking.
Physical robot presence elsewhere on the LAN was not inferred; this process had
no interface or route to that LAN. A different DDS domain alone is insufficient.

Upstream DDS initialization uses domain 1 without an interface argument. Do not
use its `--network host` example. Source-inspected, not runtime-observed topics:

| Component | Published | Subscribed |
| --- | --- | --- |
| G1 | `rt/lowstate` | `rt/lowcmd` |
| Dex3 | `rt/dex3/left/state`, `rt/dex3/right/state` | `rt/dex3/left/cmd`, `rt/dex3/right/cmd` |
| Simulation | `rt/sim_state` | `rt/sim_state_cmd`, `rt/reset_pose/cmd`, `rt/run_command/cmd` |
| Rewards | `rt/rewards_state` | `rt/rewards_state_cmd` |

[Pinned DDS source](https://github.com/unitreerobotics/unitree_sim_isaaclab/tree/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/dds).
Some objects are conditional; this is not evidence that every topic was active.

## Open validation

Use the [runbook](../O5.1.2-runbook.md) for visible viewport, offscreen image and
capacity probes. Label a rendered offscreen image separately from a viewport run.
A short two-process/two-environment feasibility probe is not a 600-second
stability result or production capacity claim. Confirm both hands in the image;
compare exported measured link poses with Unity. Ubuntu 22.04/GPU qualification
needs an explicit architecture decision. #44 stays open for missing checks.
