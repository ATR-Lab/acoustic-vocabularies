# G1 architecture review — readiness packet

Status: **PHASE 2 DEVELOPMENT AUTHORIZED; G1 QUALIFICATION INCOMPLETE**.
The owner explicitly directed development to proceed using the G1 in Isaac Sim
after reviewing the HOLD recommendation. This supersedes the earlier conditional
Phase 2 permission and releases implementation work against the measured
fixed-base G1/Dex3 setup. It does not turn missing measurements into passes,
accept every proposed ADR, authorize procurement, or permit PR merges.

This packet remains the evidence index for outstanding apparatus qualification.
Protocol-dependent choices remain provisional until the external sources are
available; participant use and a claim of a fully frozen architecture are not
established by this development authorization.

## What is available

The initial monorepo skeleton is on main at `f2f71d2`. Separate issue branches
contain CI/policy, Unity, Isaac, robot import, bridge and audio/input spikes.
ADRs remain [Proposed](../adr/README.md). [Version pins](../../apparatus/version-pins.json)
distinguish verified observations, proposed pins and unresolved fields.

## Evidence and remaining criteria

| Issue | Evidence available | Required before completion |
| --- | --- | --- |
| #43 | [PR89](https://github.com/ATR-Lab/acoustic-vocabularies/pull/89), passing CI, three deliberate failed checks, enforced main protection | LFS server restoration and real upload/fresh-clone hash proof; protocol-template validation; maintainer merge |
| #45 | [PR96](https://github.com/ATR-Lab/acoustic-vocabularies/pull/96), Windows Link and Android ARM64 IL2CPP builds, logger | Headset runs on both topologies, refresh/frame/startup metrics, removal/sleep/disconnect behavior, captures, LTS decision |
| #46 | [PR101](https://github.com/ATR-Lab/acoustic-vocabularies/pull/101), reproducible import, 43/43 joint map, 5,060 passing link-pose comparisons plus 165 supplemental hand comparisons, aligned Android build | LFS binary publication, wearer visual review, 600-second Quest render-cost/frame-budget run |
| #44 | [PR93](https://github.com/ATR-Lab/acoustic-vocabularies/pull/93), 600-second headless and offscreen runs, short concurrent/multi-env probes, inventory/hashes, close hand images | Visible viewport condition, discarded-render-frame investigation, OS/GPU qualification, review of proposed pinned-USD use |
| #47 | [PR100](https://github.com/ATR-Lab/acoustic-vocabularies/pull/100), strict clients, actual ROS# integration, Android/Windows builds, live SSH diagnostics and both candidates rendering the robot | Full 2-option × 2-rate × 2-topology runs, clock qualification, reconnect/resource tests, headset capture, full ROS extension validation/deviation review and transport/rate recommendation |
| #48 | [PR94](https://github.com/ATR-Lab/acoustic-vocabularies/pull/94), compiled click harness and tested onset analysis | Independent clock synchronization, 200 physical onsets per route/mode, acoustic/electrical comparisons, real uncertainty and equipment photo |
| #49 | [PR95](https://github.com/ATR-Lab/acoustic-vocabularies/pull/95), compiled controller/poke panel, legality check and tested analysis | Three internal testers, 32 commands/method, legibility ladder, input-loss confirmation and captures |
| #50 | [PR97](https://github.com/ATR-Lab/acoustic-vocabularies/pull/97), seven Proposed ADRs, measured spike references, engineering schema CI, hardware arithmetic, model provenance | Complete remaining spike evidence, private protocol/template reconciliation, runtime/decoding qualification, review and merge |
| #51 | This readiness packet and risk register | All above criteria, review notes, developer/gate-owner sign-off, Accepted ADRs on main |

No empty template or synthetic test result counts as a measured spike. Consult
each spike report for the newest actual data rather than interpreting this table
as a substitute for its acceptance criteria.

## Measured evidence so far

- Unity editor 6000.6.0f1 built Windows x64 Mono/DX11 and Android ARM64
  IL2CPP/Vulkan. The aligned robot APK is 60,353,958 bytes. No Quest is attached;
  build success does not establish device refresh, latency or frame timing.
  The integrated bridge/robot scene, including actual ROS# and both transport
  paths, also built for Android ARM64 IL2CPP and Windows x64 Mono. Exact build
  revisions, file manifests and hashes are in the bridge report. Device execution
  remains a separate check.
- The fixed-base Isaac headless run completed 83,308 steps in 600.0011 seconds.
  Full step intervals were mean 7.2019 ms / p95 7.7469 ms / max 12.7667 ms.
  Total GPU use was 3,336 MiB, including the existing workload; pre-run total was
  1,019 MiB. Process RSS was mean 3,355.23 MiB, with mean CPU about 2.09 cores.
  This is the approved Ubuntu 24.04 / Quadro RTX 6000 feasibility environment.
- The 600.0121-second offscreen run completed 25,446 steps, with full step/render
  intervals mean 23.5787 ms / p95 25.4004 ms / max 41.6179 ms. Mean total GPU use
  was 5,189.12 MiB and mean RSS 6,339.63 MiB. A discarded-frame renderer error
  prevents an error-free rendering claim. This is not a visible viewport run.
- Three 60-second headless capacity trials completed without logged errors.
  A two-environment process had p95 step interval 9.7605 ms. Two concurrent
  one-environment processes had p95 16.3406 / 16.3115 ms and shared total GPU
  use 5,645 MiB. They overlapped approximately 59.44 s; these brief unloaded
  probes do not qualify sustained rendering/bridge capacity.
- All 43 driven joints, including seven per hand, match. Across 92 poses and 55
  aligned frames, maximum position/orientation errors were 0.000649038 mm and
  0.000105371 degrees. A real fixed-sensor-frame mismatch was corrected explicitly;
  the original URDF and failed baseline evidence remain available.
  A separate three-pose hand suite adds 165 passing comparisons, maximum
  0.000299510 mm / 0.000092271 degrees, with close images confirming flexion.
  The URDF and USD retain documented geometry/material differences.
- Thirty-second custom-bridge diagnostics crossed the actual Linux-to-Windows
  SSH path. The 30 Hz capture had 900 live frames and three gaps above 250 ms;
  the 60 Hz capture had 1,798 frames and ten such gaps. Neither had sequence
  loss or reordering. These use a Python collector without qualified source
  clocks or a renderer. They fail the acceptance screen and do not substitute
  for either headset topology or the full-duration comparison.
- A separate 30 Hz ROS/rosbridge Python capture received 900 live frames in
  30.007 s, RTT median/p95 36.659/63.477 ms, maximum gap 56.548 ms and no sequence
  loss/reordering. These separate short runs do not rank the transports.
  Actual ROS# in Unity applied 601 live 43-joint frames over 19.9955 s, with
  callback median/p95 0.3082/0.5207 ms and a verified robot image. The completed
  custom Unity capture applied 600 live frames over approximately 19.97 s,
  callback median/p95 0.3020/0.4133 ms, with a valid summary, CSV and image.
  Interrupted attempts are preserved separately and excluded from passes. Diagnostic
  application explicitly records unqualified source freshness; it cannot pass
  acceptance or establish total frame cost. The ROS path uses bundled Humble
  `rclpy` directly because full ROS/OmniGraph activation stalled; that requested
  extension path remains unresolved. Private shared IPC resolved observed
  Fast DDS delivery failure without host IPC/network changes.
- Physical audio onset, seated input and label legibility remain unmeasured.

LFS initialization, tracking and valid pointer creation are verified. After the
owner updated billing, both normal push and the documented all-object retry still
returned the server's disabled-service response. The local fixture commit is
retained; no unavailable pointers were published and no support request was sent.

## Decisions proposed for review

- Both standalone and Link remain candidates; build success does not pick a winner.
- Compare custom WebSocket and rosbridge with an identical public state contract.
  Prohibit trial/target metadata; apply the 250 ms stale-state fault.
  Keep diagnostic animation visibly ineligible for acceptance until conservative
  source-clock bounds and the required device/network conditions are measured.
- One isolated Isaac instance per station is the planning baseline until actual
  concurrency measurements support consolidation. Four/six role allocations are
  in [ADR-003](../adr/ADR-003-hosting.md).
- Input method, text size, audio route and buffer remain unresolved. Maintain
  nonspatial audio and monotonic Commit-minus-audible-onset timing.
- Reserve a separate >=24 GB GPU for the proposed pinned vLLM/Qwen runtime.
- Append-only events plus template-matched exports remain Proposed; unseen
  protocol templates cannot be replaced by public engineering fixtures.
- Fetch USDs by pinned revision/hash outside Git. A dataset card license is
  evidence of the declaration, not a complete downstream provenance audit.
  Propose use of the pinned dataset under its declared Apache-2.0 license;
  keep USD publication prohibited by project policy and revisit contradictions.

## Operator work

Connect an authorized Quest Pro by USB for standalone deployment; enable/test
Link for its separate condition. Perform seated visual/comfort/legibility checks
with three eligible internal testers. Assemble interface/mic/coupler and an
independent sync instrument for physical onset runs. Any Wi-Fi/network changes
require operator involvement. The remote Isaac setup has been authorized in an
isolated environment; this does not approve an OS/GPU baseline exception.

Methodology documents were not found in accessible desktop/remote locations.
Exact-name and related searches in connected Drive also did not locate the sources.
The read-only external source and templates must be supplied or located before
protocol claims, decoding settings, panel wording and export schemas can freeze.
Never copy them into this public repository.

## Review record

| Item | Value |
| --- | --- |
| Developer recommendation | Continue authorized engineering; complete qualification and protocol reconciliation |
| Gate-owner decision | Explicit Phase 2 development go-ahead using G1 in Isaac Sim; full apparatus qualification remains incomplete |
| Sign-off links | Owner development directive recorded on #51; full gate sign-offs pending |
| Re-review date | To be set by gate owner after operator availability is known |
| Hardware release | Not authorized |
| Phase 2 release | Authorized by the owner's subsequent explicit directive, with unresolved evidence retained |

If selected ADRs are accepted separately, record that explicitly with remaining
conditions and a dated re-review. The development release is not full G1
qualification. A trajectory fallback still requires an explicit decision; it
cannot silently replace the owner's requested live Isaac state.
