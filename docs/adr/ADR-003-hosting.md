# ADR-003 — Isaac hosting and station hardware

Status: Proposed — measured feasibility; production density remains unqualified

## Context

WBS O5.1.8 / #50, #44. Plan four stations and separately assess six. Treat the
issue's RTX 4080 16 GB and 32 GB RAM per instance as a planning requirement;
capacity is not established by adding up VRAM alone.

## Options

1. One Isaac process per station: clearer restart/failure boundaries, higher overhead.
2. One Isaac Lab process with multiple environments: shared overhead but shared failure domain.
3. Several processes per GPU: only after measured concurrent performance and memory headroom.

## Measurements

[Isaac spike PR](https://github.com/ATR-Lab/acoustic-vocabularies/pull/93) records
the available machine: Quadro RTX 6000 24,576 MiB, 125 GiB RAM, 64 logical CPUs,
driver 570.211.01, Ubuntu 24.04.5; cached Isaac 5.1 container uses Ubuntu 24.04.2.
Its GPU differs from the planned RTX 4080 class and its OS differs from Ubuntu
22.04. Do not qualify it merely because 24 GB exceeds 16 GB.

The 600.0011-second headless run completed 83,308 physics steps and exited with
code 0, with no logged errors. The loaded fixed-base articulation has 43 joints
(29 body plus 7 per hand). Full wall-clock step intervals were mean 7.2019 ms,
p95 7.7469 ms and maximum 12.7667 ms; the narrower physics call durations were
mean 5.1635 ms, p95 5.6203 ms and maximum 7.7015 ms. The loop ran faster than
real time with physics dt 1/60 s; this does not measure paced network delivery.

Total GPU memory was 3,336 MiB throughout sampling, including an existing
workload (pre-run baseline 1,019 MiB). Simulator RSS was mean 3,355.23 MiB,
p95 3,358.43 MiB and maximum 3,358.55 MiB. Mean CPU was 208.60% of one core,
approximately 2.09 cores. These are measured totals on the nonbaseline machine,
not an allocation or density guarantee. No frames were rendered in this run.
The [spike evidence](https://github.com/ATR-Lab/acoustic-vocabularies/blob/o5.1.2-isaac-fixed-base-spike/docs/spikes/isaac/report.md)
links raw timestamps, resource CSVs, hashes and exact run configuration.
The additional offscreen run completed 600.0121 s and 25,446 steps, exit code 0.
Its full step/render intervals were mean 23.5787 ms, p95 25.4004 ms and maximum
41.6179 ms. Mean GPU total was 5,189.12 MiB (maximum 5,258 MiB); RSS was mean
6,339.63 MiB (maximum 6,340.49 MiB), with mean CPU about 4.14 cores. The renderer
logged discarded synthetic-data frames, so this is not an error-free render pass.
Offscreen rendering does not establish interactive viewport or headset cadence.

Short headless capacity trials all exited with code 0 and no logged errors:

| Configuration | Duration | Full step mean / p95 / max | GPU total |
| --- | --- | --- | --- |
| One process, two environments | 60 s | 9.1649 / 9.7605 / 16.3594 ms | 3,336 MiB |
| Concurrent process A, one environment | 60 s | 15.5889 / 16.3406 / 24.1291 ms | 5,645 MiB shared total |
| Concurrent process B, one environment | 60 s | 15.5736 / 16.3115 / 24.4470 ms | Same shared total |

The two processes overlapped for approximately 59.44 s. Their GPU totals must
not be added together. These trials omit rendering and bridge load and are too
short to qualify sustained density. The raw and derived evidence is linked from
the spike report above. A multi-environment process also retains a shared failure
boundary. Preserve one-instance-per-station planning until the qualified apparatus
passes simultaneous workload and isolation checks.

## Decision

Propose one isolated instance per station as the conservative planning baseline.
No consolidation is approved. Logical hosts below are roles, not actual hostnames:

| Scenario | Isaac roles | Per-role minimum | Aggregate dedicated capacity | Additional LLM role |
| --- | --- | --- | --- | --- |
| 4 stations | isaac-01..04 | 1 RTX 4080-class 16 GB, 32 GB RAM | 4 GPUs, 64 GB VRAM, 128 GB RAM | 1 separate >=24 GB GPU |
| 6 stations | isaac-01..06 | 1 RTX 4080-class 16 GB, 32 GB RAM | 6 GPUs, 96 GB VRAM, 192 GB RAM | 1 separate >=24 GB GPU |

The sums describe separate allocation, not interchangeable pooled VRAM or a
validated purchase list. With measured per-instance peak P and reserve R, a GPU
with capacity V must satisfy N*P+R<=V; it must also pass simultaneous physics,
render and bridge timing. Do not derive N until P/R and contention are measured.
One multi-environment process uses its own measured P(N), not N times P(1).

Per station also needs Quest Pro, selected input hardware, selected wired audio
equipment, and an isolated network connection. Link adds one qualified Windows
render PC per station. Shared apparatus includes measurement interface/mic/coupler,
sync instrument, switch and Wi-Fi 6E AP; no procurement is authorized by this draft.
LLM role and model memory follow ADR-006, never shared by default with Isaac.

## Consequences

Four/six physical host assignments, render-PC specifications and measured capacity
remain pending O1.3.3 inventory and G1. Each station has separate process/config/
state ports. Keep DDS on loopback/network-none; state relay deployment must preserve
that isolation. A change to host density requires a new apparatus version.

## Manifest fields

`station_count`, logical host role, `gpu_model`, `gpu_vram_mib`, `driver`,
`ram_gib`, `os`, `isaac_instances_per_gpu`, `envs_per_process`, resource evidence hash.

## Revisit trigger

Any over-budget step/render interval, OOM, cross-station failure, driver/asset
change, or proposed cohosting. The nonbaseline workstation cannot settle procurement.
