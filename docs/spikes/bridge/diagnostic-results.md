# Bridge diagnostic evidence — 2026-10-04

These short captures establish working paths and expose limitations. They do not complete the candidate × rate × device/network matrix or G1 acceptance.

## Live custom transport, Python receiver

Actual Isaac articulation state flowed from the network-isolated simulator through its Unix WebSocket, Linux host loopback byte relay, an SSH local forward, and a standard-library Python receiver on Windows. No demo joint command was used. This diagnostic path differs from both required Quest Wi-Fi and Link PC wired-LAN topologies, and no Unity renderer participated.

| Requested publish rate | Live states | Captured duration, s | RTT median / p95, ms | Receive interval SD, ms | Maximum receive gap, ms | Gaps >250 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 Hz | 900 | 29.981 | 67.408 / 190.346 | 41.588 | 593.727 | 3 |
| 60 Hz | 1798 | 30.026 | 86.686 / 3343.055 | 70.724 | 1856.458 | 10 |

Both captures had zero missing sequence numbers within the observed session, zero reordered/duplicate frames and zero schema-invalid frames. That does not imply smooth delivery: queued bursts can preserve sequences while violating the gap bound. Both **failed** the timing/freshness/application screen. Source-clock bounds, render application and 1800 s duration are absent. The 30 Hz capture's short duration is retained; the collector was subsequently corrected to start its duration after handshake.

RTT is measured from four timestamp echo exchanges after subtracting server echo work. Source-to-receive point estimates depend on path symmetry and are not ground truth; the published derivative contains them only with that qualification. No delay is attributed specifically to Isaac, ROS, Wi-Fi or the SSH relay without a controlled comparison. Initial/terminal gaps are included.

Sanitized [summary CSV](custom-ssh-diagnostic-summary.csv) contains derivative metrics only; endpoints, station details, raw clock epochs and session tokens are withheld. Private raw CSV SHA-256: 30 Hz `7747c60d00e249c029df9b640a843489dbcb452be1ad498cea4935e36f0847d0`; 60 Hz `1573fc1f7e05020d554600f38f22ba8f27024eeecdab445fbf4597e1347e7c64`. Publisher hook revision `2275ede`; strict harness/collector base `377b1f6`. These hashes identify the original captures before later diagnostic-mode and ROS# additions.

## Actual ROS# library smoke

Official pinned ROS# sources compiled in Unity 6000.6.0f1 with approved Newtonsoft 3.2.2. `RosSharpSelfChecks.Run` passed actual RosSocket ROS2 subscription/type/queue, publish serialization, typed callback dispatch and close lifecycle checks. An actual `WebSocketSharpProtocol` + `RosSocket` connection to the isolated Humble/rosbridge synthetic publisher then received **10 typed synthetic states and one correlated echo with zero invalid messages**, Unity process exit 0. This separately verifies the requested library, not only equivalent protocol JSON.

The synthetic source is explicitly marked `source_kind=synthetic`; renderer application, source-clock qualification and device acceptance remain false. Initial server attempts failed due to rosbridge allowlist typing, were corrected as documented in [dependencies](dependencies.md), and were not counted as passes. Live Isaac→ROS# validation is a separate pending diagnostic.

Remaining acceptance: actual ROS# and custom Android IL2CPP/device connections; live robot rendering capture; qualified clock bounds; all eight 1800 s cells; fault intervention timestamps/recovery runs; full process/frame cost comparisons; and the approved network topology. No transport recommendation is final from these diagnostics.
