# Experiment application foundation

Development continuation under #59. G1 gate qualification, the final protocol, accepted ADRs, Quest timing, and human comfort checks remain pending. This project contains the fixed observer rig and provisioning/build infrastructure; the workcell (#61), robot stream (#62), audio (#64), and response panel (#65) are separate work.

Open this `unity/` directory in **Unity 6000.6.0f1**, revision `f7f8ed4d1e24`. The separate `spikes/openxr/` project remains Phase 1 evidence. Production assets live under `Assets/ExperimentApp`. Exact package versions are in `Packages/manifest.json` and `packages-lock.json`: OpenXR 1.18.0, Meta OpenXR 2.6.1, XR Management 4.7.0, Core Utils 2.6.0, Input System 1.20.0, XR Hands 1.6.3, Newtonsoft JSON 3.2.2. Unity resolves its **already installed builtin** Test Framework 1.8.0 and NUnit 2.1.0; the older registry request (1.4.6/2.0.3) is superseded by this editor's builtins. No additional editor or OS component was installed.

Meta OpenXR is the approved existing integration. Meta XR Core v207 is **not installed**; the original brief permits omitting it when unnecessary. The literal issue checklist item naming all proposed Meta SDK versions is therefore not claimed fully satisfied. Both Android standalone (ARM64 IL2CPP, Vulkan) and Windows Link (x64 Mono, D3D11) are configured. The final topology is provisional pending measurements and ADR acceptance.

## Station provisioning

The canonical engineering schema is `../apparatus/schemas/station.schema.json`; the embedded resource must be byte-identical (build and test guard). `../apparatus/examples/station.example.json` contains synthetic placeholders and `provisioning_status: example_only`, which **cannot start the application**. Copy it into an ignored `*.local.json` file outside public source and fill reviewed station values. Required `protocol_version` must equal the stamped build version. Do not substitute this engineering example for the missing methodology or final station templates.

The runtime loads `station.local.json` only from `Application.persistentDataPath`. On Windows this is the application-specific Unity persistent directory; on Quest it is the app's external files directory. No private station file is embedded in the APK. Unknown fields (including nested fields), duplicate keys, non-JSON extensions, wrong types/enums, missing fields, non-finite values, mismatched protocol/topology, credentials in endpoint URLs, and non-unit reference quaternions are rejected. Schema vocabulary is deliberately bounded: adding an unsupported validation keyword fails closed until implemented and tested.

Audio route/buffer, input method, refresh rate, and robot source are recorded engineering choices, not qualified device settings. This foundation does not switch headset OS/audio/network settings or implement the robot client. `audio.route_offset_ms: null` explicitly means unmeasured (#80); downstream audio/trial code must gate on its own qualification requirements.

`observer_reference` is the calibrated **observer-head world pose**, in Unity left-handed Y-up metres (quaternion XYZW), recorded with a calibration ID. It is not a trial-dependent camera placement. Isaac uses RH Z-up and converts polar coordinates as `(-y,z,x)`; the workcell handoff will supply reviewed numeric placement. No illustrative numbers in the public example are calibration evidence.

## Fixed observer and fail-closed startup

Scene hierarchy: `FoundationRoot`, `SeatedOrigin/CameraOffset/Main Camera`, `PresentationRoot`. Add later fixed workcell content below `PresentationRoot`. It is inactive in the saved scene. The participant sees a black neutral background until configuration, operator logging, device tracking, and the reference restore succeed. `FoundationBootstrap.Ready` is the future controller's readiness signal; a copied `Configuration` snapshot prevents external mutation of the loaded station reference.

Tracking origin mode is explicitly **Device**. Once at startup, the rig compensates its origin so the current tracked head maps to the stored calibrated reference. Subsequent natural head motion remains untouched; there is no head lock, locomotion, teleport, or snap turn. The observer must occupy the calibration posture during startup/restoration. Repeatability is a hardware/operator validation item; this code does not establish the proposed 1 cm/1 degree tolerance.

`XRInputSubsystem.trackingOriginUpdated` latches a fault, logs it, and hides presentation. Duplicate notifications are idempotent. Tracking loss or app pause also requires recovery. The future trial controller invokes `RestoreAtSafeBoundary("between_trials")` or `"operator_recovery"` only when appropriate and the observer is in the calibrated posture. It never invokes the system's recenter API. With no trial controller yet, a post-startup fault remains neutral until that explicit recovery call. Operator log failure also fails closed.

Operator JSONL logs begin with `foundation-engineering-v1`, station ID, build ID, full commit SHA, required protocol version, schema hash, editor/target, dirty-source marker, and development-only status. An invalid/unprovisioned launch uses station `unprovisioned` and a bounded fault code; private configuration values are not echoed. This is an explicit engineering log format pending the final ADR-007/methodology contract. Logs remain private in the application persistent directory.

## Build and test

PowerShell 7 commands, from repository root (set `$editor` to the installed Unity executable):

```powershell
./tools/build-unity.ps1 -Unity $editor -Target Configure -ProtocolVersion engineering-pending-review -BuildId configure-001
./tools/build-unity.ps1 -Unity $editor -Target Test -ProtocolVersion engineering-pending-review -BuildId tests-001
./tools/build-unity.ps1 -Unity $editor -Target Android -ProtocolVersion engineering-pending-review -BuildId quest-001
./tools/build-unity.ps1 -Unity $editor -Target Windows -ProtocolVersion engineering-pending-review -BuildId link-001
```

The explicit protocol value above is an engineering placeholder, not an approved study protocol. Use a fresh build ID each time. Dirty source requires explicit `-AllowDirty` and is recorded in the build identity; reviewable release candidates should be committed first. Optional `-TemporaryDirectory` and `-GradleCache` set process-local build paths when a station's redirected AppData causes Gradle failures; they never change global OS settings.

Outputs are ignored under `unity/Builds/<build-id>/<target>/`. Each player has a `.build.json` record with identity, result, size, and SHA-256 hashes for every output file. Build identity is generated only under ignored `Assets/Generated.local.data/Resources/BuildIdentity.json`. Raw build/test logs and NUnit XML are ignored under `.local/foundation/<build-id>/`. Preserve them locally and publish only reviewed sanitized summaries/hashes.

Both player builds use `BuildOptions.None`: no development build, script debugging, or profiler connection. The foundation scene has a strict component allowlist and an automated check for unexpected components, missing scripts, extra cameras, or `OnGUI` developer overlays. Later issues must deliberately extend that check when adding reviewed production components. Tests cover strict station validation, schema/example drift, reference transform math with natural movement, recenter latching, scene stripping, and log identity. Test execution fails on zero discovered tests, failed XML results, or a Unity process failure.

## Deploy a named Quest

Use the Android SDK's existing `adb`, Python with the repository's approved `jsonschema` dependency, a provisioned private config, and the device's explicit locally observed identifier:

```powershell
./tools/deploy-quest.ps1 -Adb $adb -Serial $device -Python $python -Apk $apk -StationConfig $privateConfig -Record .local/deploy/quest-001.local.json
```

The script verifies the APK hash and non-development build record, validates station protocol/topology, selects exactly the named authorized device, installs, provisions app storage, launches, and writes an ignored deployment record. It does not enable developer mode, change Wi-Fi, configure kiosk mode, or alter headset settings. A deployment record proves installation/launch only; the operator still checks the view, comfort, and ten-relaunch repeatability. No connected Quest was available during foundation implementation, so no deployment or headset acceptance is claimed.

## CI and remaining validation

`.github/workflows/unity-foundation.yml` runs on pushes and pull requests. A hosted guard fails clearly until an isolated licensed Windows runner exists with labels `self-hosted, Windows, X64, unity-6000.6.0f1`, repository variables `UNITY_FOUNDATION_RUNNER_READY=true` and `UNITY_EDITOR_PATH`, and secret `UNITY_LICENSE`. Only then does it dispatch configure → edit-mode tests → Android build using the same scripts. This avoids permanently queued jobs or a green skipped Unity gate. Fork source is not sent to the licensed runner. Use an isolated disposable runner; do not register the shared lab workstation as a public PR runner. No runner, license upload, or new CI editor installation was performed for this issue.

Current repo runner/secrets configuration was absent when this workflow was added. Local build/test results are separate evidence; **CI build acceptance remains pending**, along with a deliberate CI compile-failure proof, named-headset deployment, ten reference relaunch measurements, human comfort/legibility, final protocol/ADR review, and PR sign-off.

Primary API references: [Unity Test Framework command line](https://docs.unity3d.com/Packages/com.unity.test-framework@1.4/manual/reference-command-line.html), [XRInputSubsystem](https://docs.unity3d.com/ScriptReference/XR.XRInputSubsystem.html), [Unity package registry](https://packages.unity.com/com.unity.test-framework). Bundled package manifests and actual editor results determine the version pins above.
