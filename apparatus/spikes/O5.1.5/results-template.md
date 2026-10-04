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

ADR-002: pending. Candidate A uses a small C# rosbridge protocol adapter for the same logger; proposed ROS# 2.3.0 package itself has not been imported or validated. If strict ROS# comparison is required, run a separately pinned ROS# client against the same topic/clock logger before accepting A. Do not imply this protocol adapter proves ROS# or IL2CPP compatibility.

Preserve raw recordings/settings privately. Publish reviewed `docs/spikes/bridge/<candidate>_<rate>_<topology>_<run-kind>.csv` and summaries after stripping local endpoints and identifiers. Separate synthetic smoke tests from live timings.
