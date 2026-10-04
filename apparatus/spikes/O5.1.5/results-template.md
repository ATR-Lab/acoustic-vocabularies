# O5.1.5 transport comparison

Status: **Needs live/operator runs. No transport winner.**

| Candidate | Hz | Topology | 30 min complete | RTT median/p95 | One-way estimate median/p95 + symmetry bound | Interval SD/p95/p99 | Max gap / >250 ms | Seq loss / queue overwrites | Host CPU vs baseline | Main apply p95 | Reconnect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rosbridge | 30 | Standalone Wi-Fi | Pending | | | | | | | | |
| rosbridge | 60 | Standalone Wi-Fi | Pending | | | | | | | | |
| custom | 30 | Standalone Wi-Fi | Pending | | | | | | | | |
| custom | 60 | Standalone Wi-Fi | Pending | | | | | | | | |
| rosbridge | 30 | Link PC, wired LAN | Pending | | | | | | | | |
| rosbridge | 60 | Link PC, wired LAN | Pending | | | | | | | | |
| custom | 30 | Link PC, wired LAN | Pending | | | | | | | | |
| custom | 60 | Link PC, wired LAN | Pending | | | | | | | | |

Record isolated namespace proof; exact ROS/rosbridge/client/Unity versions; actual joint mapping; Android IL2CPP device connection; live motion capture; AP alias/band (private station identifiers stay local); stream duration; initial/final gaps; sequence resets/reordering; source_kind; missing nearby echoes; transport CPU baseline; fault-run restart/drop/recovery results separately.

ADR-002: pending. Candidate A now uses official pinned ROS# 2.3.0 RosSocket/WebSocketSharp. Actual library, live desktop renderer and Android IL2CPP build checks passed; see `docs/spikes/bridge/diagnostic-results.md`. The prototype uses bundled Humble rclpy directly after full ROS/OmniGraph extension startup stalled. Resolve or accept that deviation explicitly. The auxiliary direct-protocol client is diagnostic-only and is not the ROS# candidate. Neither build nor desktop diagnostics establish Android runtime or headset acceptance; every matrix cell above remains pending.

Preserve raw recordings/settings privately. Publish reviewed `docs/spikes/bridge/<candidate>_<rate>_<topology>_<run-kind>.csv` and summaries after stripping local endpoints and identifiers. Separate synthetic smoke tests from live timings.
