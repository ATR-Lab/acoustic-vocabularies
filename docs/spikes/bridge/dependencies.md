# Pinned bridge dependencies

The user approved these dependencies for isolated Phase 1 work. No package or binary is vendored in this repository. Installed copies stay in ignored external assets/integration projects.

## Unity ROS# candidate

- Official [ROS# 2.3.0](https://github.com/siemens/ros-sharp/releases/tag/2.3.0), exact commit `99387714e3d498b87a93add711c7e9b5d38392f9`.
- Selected 518 C# files from `com.siemens.ros-sharp/Runtime/Libraries/RosBridgeClient`, excluding `UrdfTransfer` and inactive ROS1 messages. No URDF importer, editor extensions, sample scenes or meshes imported.
- Seven bundled support DLLs: `Microsoft.Bcl.AsyncInterfaces`, `System.IO.Pipelines`, `System.Runtime.CompilerServices.Unsafe`, `System.Text.Encodings.Web`, `System.Text.Json`, `System.Threading.Channels`, `websocket-sharp`.
- Observed file product versions: the five Microsoft libraries other than Unsafe are 9.0.5; Unsafe is 6.1.2; the bundled websocket-sharp binary reports 1.0.2.59611. Exact file hashes take precedence over a NuGet label inferred from source project metadata.
- Omit bundled `Newtonsoft.Json.dll`; use the approved official Unity package `com.unity.nuget.newtonsoft-json` **3.2.2**. The ROS# constructor initializes both serializer implementations, so the System.Text.Json support graph remains required even with the Newtonsoft serializer selected.
- Define `ROS2` globally in this isolated Unity integration project; the selected message type is `std_msgs/msg/String`.
- ROS# source [license](https://github.com/siemens/ros-sharp/blob/99387714e3d498b87a93add711c7e9b5d38392f9/LICENSE.md): Apache-2.0. Preserve upstream source notices. Microsoft runtime support libraries use [MIT](https://github.com/dotnet/runtime/blob/main/LICENSE.TXT); websocket-sharp uses [MIT](https://github.com/sta/websocket-sharp/blob/master/LICENSE.txt). Preserve their notices with redistributed builds; the source manifest records the exact bundled binaries rather than substituting newer downloads.

[Manifest](ros-sharp-2.3.0-manifest.json) contains every selected relative path, Git blob, byte count and SHA-256. The original selected manifest SHA-256 is `939db4f46e2bbe58596e67d3821c2233a96816f120c60b2698017efa849ffd65`; selection totals 526 files and 1,977,666 bytes including the root license. Verify hashes before copying source/plugins into the isolated project. An exact list avoids silently importing unrelated robot-control or asset code.

Actual Unity 6000.6.0f1 compilation, shared strict bridge selfchecks, ROS# in-memory library serialization/dispatch tests, and ROS# WebSocketSharp/RosSocket state/echo connection to isolated synthetic ROS passed. This verifies Editor/Mono integration. Android IL2CPP build/runtime and device operation remain unverified for this dependency graph.

## Linux custom and ROS candidates

Custom transport uses existing `websockets==12.0` in the inspected Isaac image, Python 3.11.13. Its Unix-socket transport test passed in a network-isolated, non-GPU container.

The Isaac image contains Humble `rclpy` 3.3.17 for its Python 3.11 runtime, but no rosbridge. The approved sidecar avoids mixing host Jazzy/Python 3.12 with Isaac:

| Component | Observed pin |
| --- | --- |
| Base | `ros:humble-ros-base-jammy`, Ubuntu 22.04.5 |
| Base digest | `sha256:1813d3c85d7f96ff7d3012d865204583255740182db5d0065f8f8cd029a83138` |
| Derived image ID | `sha256:8ac407e2fadf88b0fcbed24ec03102ba5eef7c3e21954f33e1f82a350f785564` |
| ros-humble-rosbridge-suite | `2.0.8-1jammy.20260907.222404` |
| ros-humble-rosbridge-server | `2.0.8-1jammy.20260907.222017` |
| ros-humble-rosbridge-library | `2.0.8-1jammy.20260907.220006` |
| ros-humble-rclpy | `3.3.21-1jammy.20260724.022150` |
| ros-humble-rmw-fastrtps-cpp | `6.2.10-1jammy.20260724.002510` |

Only `ros-humble-rosbridge-suite` and apt-resolved dependencies were installed into the dedicated sidecar. Runtime uses `--network container:<isolated-Isaac-container>` so both processes share the same loopback-only namespace; the separate synthetic smoke used `--network none`. Set matching `ROS_DOMAIN_ID`, `ROS_LOCALHOST_ONLY=1`, `ROS_DISTRO=humble`, and `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`. The Unix/TCP relay forwards WebSocket bytes only. No host network mode, Wi-Fi/router/firewall change, DDS egress, Unitree SDK2 or physical robot-control process is involved.

Observed rosbridge 2.0.8 launch pitfall: the XML launcher coerces the topic allowlist into a string array although the node expects a string. Use `ros2 run rosbridge_server rosbridge_websocket --ros-args --params-file <private-yaml>` with `address: "127.0.0.1"`, `port: 9090`, and **string** `topics_glob: "['/spike/*']"`. Source the Humble setup before enabling shell `nounset`. Verify an actual subscription through the relay after launch; successful process startup alone is insufficient.
