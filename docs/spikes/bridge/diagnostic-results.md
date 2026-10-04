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

A final 60 Hz ROS diagnostic received **1800 live states across 30.0036 s**, RTT median/p95 **38.575/51.602 ms**, interval SD **3.904 ms**, maximum gap **39.558 ms**, and zero >250 ms gaps, missing sequences, reordered frames or invalid frames. Symmetry-dependent one-way median/p95 was 12.704/15.971 ms; source qualification, renderer application and acceptance remain false. Raw SHA-256: `07dd0e38df138a587c80182da5e7ead3506aedf3d39e29bc6571e7d53ce78fa7`. [ROS derivative CSV](ros-ssh-diagnostic-summary.csv) contains both rates. The corresponding source completed 90.0032 s / 12160 simulation steps, exit 0, without recorded error/traceback. The longer ROS30 source completed 300.0024 s / 41281 steps, exit 0.

## Build evidence

The integrated real-robot scene, actual ROS# dependency graph and both transport code paths built successfully for **Android ARM64 IL2CPP and Windows x64 Mono**, both with actual process exit 0. Final [build results](build-results.json), [compiled source hashes](build-source-manifest.json) and [Windows output manifest](windows-build-manifest.json) cover the durable-output source at `28bd61c`. The final APK is **71,604,827 bytes**, SHA-256 `031061641ddb5f53fd366b495a6ba6bb0d4167e03edfed2503fcaee853d64980`; ZIP CRC, sole ARM64 IL2CPP ABI and INTERNET permission were checked. Windows output is **224,450,064 bytes across 317 files**, original manifest SHA-256 `815f3185bb35c653f16a9f438079a0c7ce0ac3c19ee1a14f22ffd2eb6ed6e30f`. The original source manifest SHA-256 is `0fdaf6309e2d6931b98705756f371bc795b08b632af2dcab59ccacf9b55d2835`; hashes describe installed source bytes, including line endings. All nine hashes were independently verified against the current source worktree.

These final rebuilds supersede the earlier 67,593,654-byte APK and 224,449,425-byte Windows output, which predated capture/output refinements. No binary or unavailable LFS pointer is published. Compilation/AOT/linker/packaging success does not establish device runtime or headset operation.

## Actual Unity robot application

The pinned ROS# `RosSocket`/WebSocketSharp client independently received ten strict live 43-joint states and a correlated echo with zero invalid messages. The complete Unity benchmark then applied **601 live states** through the actual #46 named-joint adapter over a 19.9955 s first-to-last receipt span. Main-thread parse/validation/application cost was median **0.3082 ms**, p95 **0.5207 ms**, maximum **19.7705 ms**. The run has one start/end marker pair and Unity exit 0. All states are `applied=true`, `source_fresh=false`, `diagnostic=true`; the analyzer cannot accept this as calibrated performance. [Sanitized derivative and hashes](rendered-ros-diagnostic.json) identify the private CSV and screenshot.

The screenshot was inspected and shows the full imported G1. This is the fixed diagnostic camera over the SSH tunnel; OpenXR reported `XR_ERROR_FORM_FACTOR_UNAVAILABLE`, so no headset rendering/session claim is made. An earlier capture was obscured by placeholder labels, and a clean diagnostic-only camera rerun supplied the reported result. Capture rendering and file I/O are outside the reported `apply_ms` cost.

The first custom-renderer attempt produced an image but its summary later contained NUL bytes; it is excluded from pass claims. A fresh attempt correctly reported zero applied frames and warmup failure when the local SSH forwarding listener had disappeared. Those original artifacts remain private and preserved. Final output was hardened with disk flushes and atomic non-overwriting image/summary writes, and the tunnel was re-established and checked end-to-end before another repeat. The original custom source also logged an abrupt client-disconnect traceback; source process completion is not described as an error-free connection lifecycle.

The final custom repeat **passed the bounded integration check**: 600 live states were actually applied across a 19.9665 s first-to-last receipt span; parse/validation/application median/p95/maximum was **0.3020/0.4133/3.5677 ms**. The independently checked CSV, valid summary, complete robot image and actual Unity process exit 0 agree. All rows retain `source_fresh=false` and `diagnostic=true`, and `run_end` is present. [Sanitized custom derivative](rendered-custom-diagnostic.json) includes the original evidence hashes. The final run used the durable-output sources at `28bd61c`; the preceding clean ROS capture used the same transport/parser but predates those output-only changes.

These two desktop diagnostic application-cost samples are not a paired, controlled performance comparison. They verify that both real transports feed the same verified 43-joint renderer and that diagnostic logging cannot pass source-clock acceptance. They do not measure total render-frame or process cost.

## Completion and cleanup

After all receiver evidence was collected, task containers, middleware and byte relays were stopped. The temporary SSH forward was closed; both diagnostic listener ports were verified absent on their respective machines. No Unity build/test processes remained. Approved images, dependency caches and private evidence were retained. The final successful custom-renderer source was intentionally stopped after the 600-frame receiver capture; it is not claimed as a complete 300 s source benchmark. Original failed/incomplete attempts and their termination records remain preserved.

Remaining acceptance: actual ROS# and custom Android device connections and live headset captures; qualified clock bounds; all eight 1800 s cells; fault intervention timestamps/recovery runs; full process/frame cost comparisons; the full ROS extension path decision; and the approved network topology. No transport recommendation is final from these diagnostics.
