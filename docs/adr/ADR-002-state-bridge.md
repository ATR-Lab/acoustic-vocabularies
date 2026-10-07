# ADR-002 — Public scene-state bridge

Status: Proposed — transport and rate pending measurements

## Context

WBS O5.1.8 / #50, informed by #44, #46 and #47. Code merged for #54 and #55
supplies the development state and command contracts. Unity renders public simulation
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
`com.unity.nuget.newtonsoft-json` 3.2.2 package. Actual ROS# 2.3.0 source and
its pinned support libraries compile in the isolated Unity project. RosSocket
serialization/dispatch, a synthetic state/echo connection and a real 43-joint
Isaac-to-ROS# rendering diagnostic are verified. The integrated robot and both
transport paths also build for Android ARM64 IL2CPP; device runtime remains
unverified. The direct rosbridge adapter is a separate diagnostic option.
Unknown clock bounds, unapplied frames and synthetic sources cannot pass the
live screen. Explicit diagnostic animation records `diagnostic=true` and
`source_fresh=false`, so it cannot be mistaken for qualified application.

The live ROS prototype uses Isaac's bundled Humble `rclpy` directly. Full
`isaacsim.ros2.bridge`/OmniGraph extension activation stalled after reset; the
requested extension path therefore remains unvalidated. The Ubuntu 22.04
Humble/rosbridge sidecar and simulator share only a private IPC and loopback-only
network namespace. A failed separate-IPC attempt exposed Fast DDS shared-memory
delivery failure; task-scoped IPC sharing resolved it without host IPC/network
changes. These are documented spike deviations, not baseline qualification.

Short custom-transport SSH/Python diagnostics received 900 live frames at 30 Hz
and 1,798 at 60 Hz, without sequence loss/reordering. They nevertheless recorded
three and ten receive gaps above 250 ms (maxima 593.727 ms and 1,856.458 ms).
The [diagnostic report](../spikes/bridge/diagnostic-results.md) retains this failing evidence. It cannot establish either required topology,
rendered-state freshness or an attribution to a particular network component.

A separate 30 Hz live ROS/Python diagnostic received 900 states in 30.007 s,
with RTT median/p95 36.659/63.477 ms, maximum gap 56.548 ms and no missing or
reordered states. It still fails the qualification screen because source clocks,
renderer application and the full duration are unqualified. These captures ran
at different times and loads; their values cannot rank the transports.
The real ROS# Unity rendering diagnostic applied 601 frames over 19.9955 s;
callback median/p95 was 0.3082/0.5207 ms. Callback cost excludes the full render
frame and does not establish headset timing or end-to-end latency.

The merged #54 custom publisher ([notes](../isaac/publisher.md)) completed two
paced 30 Hz one-hour runs with 60 Hz physics on the documented Ubuntu 24.04
deviation. Both **FAIL** the strict timing/rate screen: zero missed deadlines and
p99 absolute period error <=3.333333 ms. The
[in-process receiver run](../isaac/publisher/actual-hour-results.json) received
107,917 frames in 3,600.013 s with 84 missed deadlines and 3.575965 ms p99 error.
The [separate receiver run](../isaac/publisher/receiver-process-hour-results.json)
received 108,001 frames with zero missed deadlines but 6.069881 ms p99 error.
Neither had a sequence gap, queue overwrite or gap above 250 ms (maxima 45.270
and 43.907 ms). The paired unpaced disconnect diagnostics failed, then passed,
the 5% throughput screen; that pass does not qualify paced or protected
throughput. These unprotected local-socket workloads do not measure Wi-Fi,
source clocks or headset rendering. No hour run has passed.

## Decision

Evaluate B as the simpler candidate, with A retained for comparison. B is the
#54 development publisher at a provisional 30 Hz; that is not a frozen transport
or rate. Do not freeze either without passing evidence. The public envelope
[`schemas/bridge-state.schema.json`](schemas/bridge-state.schema.json) mirrors
the shipped version 2 contract
[`isaac/publisher/state-v2.schema.json`](../../isaac/publisher/state-v2.schema.json).
`tests/test_adr_contracts.py` fails if the copies differ beyond `$comment`,
validates a clearly synthetic example and rejects top-level, object and
visual-state answer metadata. The #47 version 1 spike envelope remains at
[`apparatus/schemas/bridge-state.schema.json`](../../apparatus/schemas/bridge-state.schema.json)
for its retained evidence; version 1 clients must reject version 2.

| Field | Meaning |
| --- | --- |
| `version`, `kind` | Contract version 2 and `state` discriminator |
| `source_kind` | `live` or explicitly `synthetic` for harness tests |
| `station_id` | Anonymous logical station ID such as `station-01`; not a host or device name |
| `scene_sha256`, `reset_snapshot_sha256` | Loaded scene hash and hashed neutral reset snapshot the frame is bound to |
| `session_id`, `seq` | New random publisher epoch after restart; contiguous integer sequence |
| `host_monotonic_ns` | Decimal string from publisher host monotonic clock; never subtract it from another host's clock without a measured mapping |
| `sim_time`, `sim_step` | Simulation diagnostics, never response-time origin |
| `joint_names`, `joint_positions` | Exactly 43 canonical ordered names and matching finite positions in radians |
| `objects` | Full sorted public inventory: ID, position metres, XYZW rotation, `visible`, `enabled` and closed visual `state` |

Visual `state` permits only `card_face`, `arrow_angle_rad`, `lid_open_fraction`,
`tag_attached` and a registered `location`. Reject unknown fields, duplicate
joints, length mismatch, nonfinite values and unexpected session/sequence
transitions. Use local receive time for liveness, and check source progress and
mapped publish age conservatively to reject queued old frames. A valid packet
alone must not clear a fault after a source stall. Finite IEEE values,
normalized quaternions, measured canonical order and the immutable scene
registry also require runtime validation; JSON Schema alone is insufficient.
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

Commands use a separate private channel. Its shipped #55 envelope is
[`isaac/commands/command.schema.json`](../../isaac/commands/command.schema.json),
mirrored and equality-checked as
[`schemas/control-command.schema.json`](schemas/control-command.schema.json).
Replies and terminal log events use
[`reply.schema.json`](../../isaac/commands/reply.schema.json) and
[`command-event.schema.json`](../../isaac/commands/command-event.schema.json).
Each request carries version 1, kind `private_command`, the fresh
`control_session_id` from private `/health`, a unique 32-hex `request_id`,
`command` and closed `args`. Commands are `reset`, `hold_neutral`, `demo` (legal
tray/container action-target pairs only), `set_mode` (`teaching`, `test`,
`post_endpoint`), `pause`, `resume`, `stop` and `health`. Acknowledgement,
idempotency and the protected-test lock are in [commands](../isaac/commands.md).
Public `/state` closes on command-shaped messages; an operator command is never
a state frame. This ADR assigns no study actions.

## Consequences

The merged #54 publisher emits version 2, #55 provides the isolated command
channel and #62 owns version 2 clients; none of these freezes transport or rate
before G1. Monotonic domains, origin epochs and canonical-name revisions must be
logged.
Zero steady-state >250 ms gaps is the proposed screen; failures remain evidence
against the candidate. Recorded trajectories are a possible G2 fallback for
review, never an unannounced substitute for the requested live stream.

## Manifest fields

`bridge_transport`, `schema_version`, `publish_hz`, `ports`, `clock_mapping`,
`stale_ms`, `canonical_joint_order_sha256`, `reconnect_policy`, `source_kind`,
`station_id`, `scene_sha256`, `reset_snapshot_sha256`.

## Revisit trigger

Steady-state stale event, source/schema change, Android incompatibility, network
change or spike overrun of three days. Re-measure before changing the frozen rate.
