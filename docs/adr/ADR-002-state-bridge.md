# ADR-002 — Public scene-state bridge

Status: Proposed — transport and rate pending measurements

## Context

WBS O5.1.8 / #50, informed by #44, #46 and #47. Unity renders public simulation
state. Trial, target, intended command and correctness information never belong
in this stream. Unitree DDS must remain isolated from physical robot networks.

## Options

A. Isaac ROS 2 bridge + rosbridge_suite WebSocket + ROS# 2.3.0 C# integration.
Unity 6 and Android IL2CPP compatibility require actual builds/device runs.

B. Small WebSocket publisher inside the Isaac Python app. Direct configuration
loading avoids Unitree DDS in the state path. Isolated Unix socket plus a host
relay can keep the simulator namespace network-free during the spike.

ROS-TCP-Connector/Endpoint and ROS2ForUnity are excluded from the implementation.
Even if Link wins, ROS2ForUnity would constrain portability to a Windows-only
path; it is a documented alternative, not a dependency. URDF-Importer is not a
runtime transport dependency.

## Measurements

Required #47: both candidates × 30/60 Hz × standalone Wi-Fi/wired-LAN Link,
30 minutes each, plus server restart and operator-controlled Wi-Fi-loss tests.
Report median/p95 latency estimate, clock-offset uncertainty, jitter SD/p95/p99,
loss, maximum gap, >250 ms fault count, CPU and Unity application cost.
RTT/2 is a symmetry-dependent estimate, not measured one-way latency. No passing
hardware matrix exists yet. Joint order must be reconciled between loaded #44
articulation and #46 import, not inferred from expected counts.

[Bridge PR100](https://github.com/ATR-Lab/acoustic-vocabularies/pull/100) includes
strict C# parsing and source/queue freshness checks using the approved
`com.unity.nuget.newtonsoft-json` 3.2.2 package. The direct rosbridge adapter is
an interim prototype; it does not establish ROS# 2.3.0 compatibility. Unknown
clock bounds, unapplied frames and synthetic sources cannot pass the live screen.

## Decision

Evaluate B as the simpler candidate, with A retained for comparison. Do not freeze
a rate or transport without evidence. The proposed public envelope is in
[`schemas/bridge-state.schema.json`](schemas/bridge-state.schema.json), matching
#47's tested contract. CI validates a clearly synthetic example and rejects
top-level and nested answer metadata. When the spike is integrated, CI checks
that the two schema copies agree.

| Field | Meaning |
| --- | --- |
| `version`, `kind` | Contract version 1 and `state` discriminator |
| `source_kind` | `live` or explicitly `synthetic` for harness tests |
| `session_id`, `seq` | New random publisher epoch after restart; increasing integer sequence |
| `host_monotonic_ns` | Decimal string from publisher host monotonic clock; never subtract it from another host's clock without a measured mapping |
| `sim_time`, `sim_step` | Simulation diagnostics, never response-time origin |
| `joint_names`, `joint_positions` | Canonical ordered names and matching finite positions in radians |
| `objects` | Public object ID, position metres and XYZW rotation only |

Reject unknown fields, duplicate joints, length mismatch, nonfinite values and
unexpected session/sequence transitions. Use local receive time for liveness,
and check source progress and mapped publish age conservatively to reject queued
old frames. A valid packet alone must not clear a fault after a source stall.
Array-length equality, finite IEEE values, normalized quaternions and measured
canonical order also require runtime validation; JSON Schema alone is insufficient.
Do not expose trial information in object names or encode hidden answer metadata.

Proposed per-station ports: `8765 + station_index` for custom state and
`9090 + station_index` for rosbridge, where station_index is 0..5. Bind addresses
and actual station mappings stay in local configuration; network changes need
operator approval. Restrict streams to the isolated lab network; no public exposure.

Reconnect with bounded backoff (0.25, 0.5, 1, 2, then 5 s maximum); a new session
clears buffered states and validates the canonical inventory before resume.
State older than 250 ms triggers a fault and pauses the next exposure. A reconnect
must not silently resume a protected test. Exact recovery behavior is validated
in #47 and later implemented only after G1.

A separate proposed command schema is in `schemas/control-command.schema.json`.
Only engineering reset/pause/resume/health are drafted here. The real command API
and acknowledgement/idempotency behavior belong to #55; no API is implemented
or study actions assigned by this ADR. An operator command is never a state frame.

## Consequences

#54/#62 use a public state contract; #55 owns an isolated command channel after
G1. Monotonic domains, origin epochs and canonical-name revisions must be logged.
Zero steady-state >250 ms gaps is the proposed screen; failures remain evidence
against the candidate. Recorded trajectories are a possible G2 fallback for
review, never an unannounced substitute for the requested live stream.

## Manifest fields

`bridge_transport`, `schema_version`, `publish_hz`, `ports`, `clock_mapping`,
`stale_ms`, `canonical_joint_order_sha256`, `reconnect_policy`, `source_kind`.

## Revisit trigger

Steady-state stale event, source/schema change, Android incompatibility, network
change or spike overrun of three days. Re-measure before changing the frozen rate.
