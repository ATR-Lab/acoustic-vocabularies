# O5.1.5 public state schema

The schema is [`apparatus/schemas/bridge-state.schema.json`](../../../apparatus/schemas/bridge-state.schema.json); both candidates carry this exact inner JSON envelope. Top-level and nested object schemas reject unknown keys. No trial, target, answer, audio, selection, allocation or participant information belongs in this stream. The separate Phase 2 command API is out of scope.

| Field | Meaning |
|---|---|
| `version`, `kind` | `1`, `state` |
| `source_kind` | `live` for articulation reads, `synthetic` for smoke fixtures; never relabel |
| `session_id` | Random 32-hex token per publisher instance; not a station/device identifier |
| `seq` | Monotonically increasing per publisher instance; restart changes session |
| `host_monotonic_ns` | Decimal string of Python `time.monotonic_ns()` at envelope assembly; includes subsequent encoding/queue/send cost |
| `sim_time`, `sim_step` | Diagnostic simulator values; never used for response time or clock offset |
| `joint_names`, `joint_positions` | Canonical #46 name order, finite radians, equal lengths; name set must match loaded #44 articulation |
| `objects` | Public placeholder poses only: unique `id=placeholder_*`, `position_m[3]`, normalized `rotation_xyzw[4]` |

No joint count is asserted before actual USD inventory and #46 crosswalk are verified. Runtime rejects mismatched vectors, non-finite values and unknown names. Object positions use Isaac's world coordinates (metres; Z up); convert explicitly at the display boundary. Joint scalar radians are consumed by #46's tested axis/origin conversion. Do not apply raw object poses directly to Unity's different coordinate basis.

Custom B sends JSON as uncompressed WebSocket text through a Unix socket inside Isaac's isolated namespace. Candidate A publishes the same compact JSON string as ROS2 `std_msgs/msg/String` on `/spike/state`; rosbridge wraps it as `{"op":"publish","topic":"/spike/state","msg":{"data":"<inner JSON>"}}`. Unity subscribes with `queue_length=1`. The explicit strings preserve nanosecond integers across JSON clients.

Echo request is only `{"kind":"echo","c0_s":"<client monotonic seconds>"}`. Reply adds `s1_ns` (server callback receipt) and `s2_ns` (server reply assembly). A uses `/spike/echo/request` and `/spike/echo/reply`; B uses the same WebSocket. c3 is the Unity socket receive timestamp before parsing/main-thread queuing.

Measured network RTT is `(c3-c0)-(s2-s1)`. Server-minus-client offset estimate is `((s1-c0)+(s2-c3))/2`; estimated state latency is `receive_client + offset - publish_server`. Clock units are converted first. Asymmetry can bias offset by up to half network RTT in a no-drift model; nearby minimum-RTT samples reduce queueing bias without proving symmetry. Report estimates and uncertainty separately from ground truth. Hardware synchronization would be required to validate physical one-way latency independently.

Python validates strict keys before transmission. Unity's `JsonUtility` verifies version, canonical names/vector lengths and finite values after decoding; it ignores unknown JSON members, so full schema validation is enforced at the sender and in offline evidence review. A production parser with full schema enforcement is a Phase 2 decision, not claimed here.
