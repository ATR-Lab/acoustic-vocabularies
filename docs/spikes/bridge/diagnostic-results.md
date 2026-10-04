# Bridge diagnostic evidence — 2026-10-04

These short captures establish working paths and expose limitations. They do not complete the candidate × rate × device/network matrix or G1 acceptance.

## Live custom transport, Python receiver

Actual Isaac articulation state flowed from the network-isolated simulator through its Unix WebSocket, Linux host loopback byte relay, an SSH local forward, and a standard-library Python receiver on Windows. No demo joint command was used. This diagnostic path differs from both required Quest Wi-Fi and Link PC wired-LAN topologies, and no Unity renderer participated.

| Requested publish rate | Live states | Captured duration, s | RTT median / p95, ms | Receive interval SD, ms | Maximum receive gap, ms | Gaps >250 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 Hz | 900 | 29.981 | 67.408 / 190.346 | 41.588 | 593.727 | 3 |
| 60 Hz | 1798 | 30.026 | 86.686 / 3343.055 | 70.724 | 1856.458 | 10 |

Both captures had zero missing sequence numbers within the observed session, zero reordered/duplicate frames and zero schema-invalid frames. That does not imply smooth delivery: queued bursts can preserve sequences while violating the gap bound. Both **failed** the timing/freshness/application screen. Source-clock bounds, render application and 1800 s duration are absent. The 30 Hz capture's short duration is retained; the collector was subsequently corrected to start its duration after handshake.

The corresponding full publisher runs recorded 4501 frames across 149.976 s at 30.0048 Hz, and 7199 across 119.968 s at 59.9991 Hz, with zero server queue overwrites. Publish-call median/p95 costs were 0.245/0.280 ms and 0.244/0.286 ms respectively. Those are directly measured call costs, excluding transport-worker, renderer, relay and total simulation CPU; they are not total bridge overhead. Publisher windows are longer than the receiver windows and must not be treated as aligned per-frame latency captures.

RTT is measured from four timestamp echo exchanges after subtracting server echo work. Source-to-receive point estimates depend on path symmetry and are not ground truth; the published derivative contains them only with that qualification. No delay is attributed specifically to Isaac, ROS, Wi-Fi or the SSH relay without a controlled comparison. Initial/terminal gaps are included.

Sanitized [summary CSV](custom-ssh-diagnostic-summary.csv) contains derivative metrics only; endpoints, station details, raw clock epochs and session tokens are withheld. Private raw CSV SHA-256: 30 Hz `7747c60d00e249c029df9b640a843489dbcb452be1ad498cea4935e36f0847d0`; 60 Hz `1573fc1f7e05020d554600f38f22ba8f27024eeecdab445fbf4597e1347e7c64`. Publisher hook revision `2275ede`; strict harness/collector base `377b1f6`. These hashes identify the original captures before later diagnostic-mode and ROS# additions.

## Actual ROS# library smoke

Official pinned ROS# sources compiled in Unity 6000.6.0f1 with approved Newtonsoft 3.2.2. `RosSharpSelfChecks.Run` passed actual RosSocket ROS2 subscription/type/queue, publish serialization, typed callback dispatch and close lifecycle checks. An actual `WebSocketSharpProtocol` + `RosSocket` connection to the isolated Humble/rosbridge synthetic publisher then received **10 typed synthetic states and one correlated echo with zero invalid messages**, Unity process exit 0. This separately verifies the requested library, not only equivalent protocol JSON.

The synthetic source is explicitly marked `source_kind=synthetic`; renderer application, source-clock qualification and device acceptance remain false. Initial server attempts failed due to rosbridge allowlist typing, were corrected as documented in [dependencies](dependencies.md), and were not counted as passes.

The first actual-Isaac ROS attempt stalled during full `isaacsim.ros2.bridge`/OmniGraph extension activation after simulation reset and was stopped. The revised prototype uses the distribution's pinned Humble `rclpy` directly inside the Isaac process. That substitution exercises ROS2 String-topic state transport, but **does not validate the originally requested full extension activation path**. Preserve this deviation until that path is independently resolved or accepted in the architecture decision.

## Live ROS2/rosbridge, Python receiver

With the corrected shared private IPC namespace, actual 43-joint Isaac state reached the same SSH diagnostic route through bundled Humble `rclpy` and the pinned rosbridge sidecar. The 30 Hz capture contained 900 live states across 30.007 s; measured RTT median/p95 was 36.659/63.477 ms, interval SD 4.596 ms, maximum receive gap 56.548 ms, and no gaps exceeded 250 ms. Missing sequences, reordered/duplicate frames and invalid frames were all zero. Symmetry-dependent one-way estimate median/p95 was 13.323/17.580 ms, with no ground-truth calibration. Raw CSV SHA-256 is `1154eb82adafa8d3e97c2fbf5d51530e26a0890f5c858240c1143c4bafbb59e5`.

This receiver does not exercise the ROS# library; the independent library tests above do. Source-clock qualification and renderer application remain false for this Python capture, so its overall screen is **false**. These short captures occurred at different times/load and do not support a controlled transport performance ranking. Full paired matrix measurements remain necessary.

## Build evidence

The integrated real-robot scene, actual ROS# dependency graph and both transport code paths built successfully for Android ARM64 IL2CPP. The 67,593,654-byte private APK SHA-256 is `2d6612c81b69a4ae2fc18b180b3def7c4e74e573a6a74f4c79dc896924eba26f`; no binary or unavailable LFS pointer is published. This is an AOT/linker/packaging result, not a device-runtime pass. See [dependency pins](dependencies.md).

Remaining acceptance: actual ROS# and custom Android device connections; live robot rendering capture; qualified clock bounds; all eight 1800 s cells; fault intervention timestamps/recovery runs; full process/frame cost comparisons; and the approved network topology. No transport recommendation is final from these diagnostics.
