# O5.1.2 — fixed-base Isaac spike

Status: **prepared; runtime acceptance is open**. No robot was loaded or stepped.
The read-only host inspection on 2026-10-04 is recorded in
`apparatus/spikes/O5.1.2/host-inventory.json`; it is not a resource benchmark.

## Observed installation

The workstation has Ubuntu 24.04.5, a Quadro RTX 6000 with 24,576 MiB VRAM,
125 GiB RAM (rounded), 64 logical CPUs, and driver 570.211.01. One existing Python
compute process used 926 MiB; it was left running. Total GPU memory use at that
instant was 1,019 MiB and utilization was 0%. These single snapshots do not
establish available performance or cohosting capacity.

The cached official Isaac Sim 5.1.0 image contains build
`5.1.0-rc.19+release.26219.9c81211b.gl`, Python 3.11.13, PyTorch 2.7.0+cu128 and
websockets 12.0. Its OS is Ubuntu 24.04.2. Its immutable digest is in `pins.json`.
The image contains Humble and Jazzy bridge extensions. The host has ROS Jazzy
and rosbridge_suite 2.7.1. Matching Isaac Lab, Unitree sim and G1/Dex3 assets were
not found in the inspected locations.

Both host and image differ from the protocol's Ubuntu 22.04 pin. The older
Quadro has enough VRAM for the stated capacity minimum, but is not the required
RTX 4080 class or a Unitree-tested GPU. Do not describe it as a qualifying host.
Record a nonbaseline feasibility run separately if approved; G1 needs a decision
on an Ubuntu 22.04 installation and suitable GPU or an explicit protocol exception.

## Source and license pins

The [Unitree 5.1 installation instructions](https://github.com/unitreerobotics/unitree_sim_isaaclab/blob/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/doc/isaacsim5.1_install.md)
identify Isaac Lab commit `80094be3245aa5c8376a7464d29cb4412ea518f5`.
The Unitree repository is pinned to `e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc`.
Its code is [Apache-2.0](https://github.com/unitreerobotics/unitree_sim_isaaclab/blob/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/LICENSE).

The [dataset card at the pinned revision](https://huggingface.co/datasets/unitreerobotics/unitree_sim_isaaclab_usds/blob/394cf2448f8a9ed815c77c701a761f3d1ff1c8fb/README.md)
declares `apache-2.0`, checked 2026-10-04. This resolves the previously unknown
card declaration; it does not verify notices inside the 1,305,090,539-byte archive.
The HF LFS API reports SHA-256
`06fbf14549be3a81e3dbd4a8a019e7f060f48de1556b70f291a18ac8f9ead4b0`.
The fetcher verifies this value and safely extracts to a local cache.

Decision: **do not commit USDs** as requested. Study-use approval remains pending
inspection of the downloaded asset bundle's notices and provenance. The fallback
is the BSD-3 URDF conversion path if conflicting asset notices are found.

## Isolation and DDS

A metadata-only Docker container with `--network none --read-only` had only
loopback and an empty IPv4 routing table. The simulator and DDS were never started.
No conclusion about physical robots elsewhere on the host's LAN is possible from
this check. Network-none containment prevents a spike process reaching that LAN.

The probe requires only `lo` before loading Isaac or Unitree configuration. It
loads `G129_CFG_WITH_DEX3_BASE_FIX` directly without Unitree's sim entrypoint, and
therefore starts no DDS publisher. A separate DDS observation run, if needed, must
stay in the same isolated namespace. Never use the upstream `--network host`
example here. Upstream `DDSManager` initializes DDS domain **1**, with no interface
argument. A different domain alone is insufficient isolation.

Source-inspected topics (not runtime discovery), at the pinned Unitree commit:

| Component | Published topics | Subscribed topics |
| --- | --- | --- |
| G1 | `rt/lowstate` | `rt/lowcmd` |
| Dex3 | `rt/dex3/left/state`, `rt/dex3/right/state` | `rt/dex3/left/cmd`, `rt/dex3/right/cmd` |
| Simulation | `rt/sim_state` | `rt/sim_state_cmd`, `rt/reset_pose/cmd`, `rt/run_command/cmd` |
| Rewards | `rt/rewards_state` | `rt/rewards_state_cmd` |

Sources: [DDS files](https://github.com/unitreerobotics/unitree_sim_isaaclab/tree/e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc/dds).
Some objects are conditional; the table is not evidence that every topic was active.

## Runtime evidence still required

Run the [runbook](../O5.1.2-runbook.md). The harness exports ordered names, USD
joint type/axis, runtime limits/defaults, fixed-base status and actual body/hand
counts. It refuses a non-43-joint result rather than silently accepting it.
It hashes loaded USD dependency closure, loaded layers and imported Lab/Unitree
source, with logical paths only. Missing assets or unmapped paths fail the run.

One headless and one viewport run must each complete 600 seconds. Attempt two
instances and one two-articulation process separately. The latter is a minimal
two-environment feasibility probe, not RL training. Report GPU totals alongside
baseline, process RSS, one-core CPU%, and step/render timing distributions.
GPU totals include any existing workloads; they are not attributed process VRAM.
The resource sampler itself adds small unquantified overhead. Empty timing data
are missing values, never zeros. Confirm the viewport image shows both hands.

The expected `joint_inventory.csv`, `asset_hashes.csv` and `resources.csv` in this
directory are intentionally absent until generated by a real accepted run.
Installation approval, runtime tests, DDS observations and screenshot review are
outstanding. ADR-003 cannot make a measured multi-station capacity claim yet.
