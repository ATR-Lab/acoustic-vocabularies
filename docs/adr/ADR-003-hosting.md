# ADR-003 — Isaac hosting and station hardware

Status: Proposed — capacity pending measured resource runs

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
These are inventory facts, not a throughput test. Its GPU differs from the
planned RTX 4080 class and its OS differs from Ubuntu 22.04. Do not qualify it
merely because 24 GB exceeds 16 GB. Ten-minute one-instance/viewport and concurrent
measurements remain required; link sanitized resource CSVs when they exist.

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
