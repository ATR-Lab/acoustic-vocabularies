# G1 architecture review — readiness packet

Status: **NOT READY FOR SIGN-OFF**. This is a draft evidence index, not a request
to accept the architecture. Phase 2 (#52–#74) remains blocked. Only the gate owner
can authorize G1; no ADR has been changed to Accepted and no PR has been merged.

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
| #46 | [PR101](https://github.com/ATR-Lab/acoustic-vocabularies/pull/101), reproducible import, 43/43 joint map, 5,060 passing link-pose comparisons, aligned Android build | LFS binary publication, side-by-side hand/visual review, 600-second Quest render-cost/frame-budget run |
| #44 | [PR93](https://github.com/ATR-Lab/acoustic-vocabularies/pull/93), 600-second headless run, measured inventory, hashes and resource/step CSVs; offscreen image | Rendered/concurrent evidence, visible viewport condition, OS/GPU qualification, final study-use license review |
| #47 | [PR100](https://github.com/ATR-Lab/acoustic-vocabularies/pull/100), strict Unity client, live custom publisher and diagnostic SSH captures; actual ROS# integration underway | Full 2-option × 2-rate × 2-topology runs, reconnect tests, CPU/client cost, live headset capture and transport/rate recommendation |
| #48 | [PR94](https://github.com/ATR-Lab/acoustic-vocabularies/pull/94), compiled click harness and tested onset analysis | Independent clock synchronization, 200 physical onsets per route/mode, acoustic/electrical comparisons, real uncertainty and equipment photo |
| #49 | [PR95](https://github.com/ATR-Lab/acoustic-vocabularies/pull/95), compiled controller/poke panel, legality check and tested analysis | Three internal testers, 32 commands/method, legibility ladder, input-loss confirmation and captures |
| #50 | [PR97](https://github.com/ATR-Lab/acoustic-vocabularies/pull/97), seven Proposed ADRs, engineering schema CI, hardware arithmetic, model provenance | Real measurements cited, private protocol/template reconciliation, runtime/decoding qualification, review and merge |
| #51 | This readiness packet and risk register | All above criteria, review notes, developer/gate-owner sign-off, Accepted ADRs on main |

No empty template or synthetic test result counts as a measured spike. Consult
each spike report for the newest actual data rather than interpreting this table
as a substitute for its acceptance criteria.

## Measured evidence so far

- Unity editor 6000.6.0f1 built Windows x64 Mono/DX11 and Android ARM64
  IL2CPP/Vulkan. The aligned robot APK is 60,353,958 bytes. No Quest is attached;
  build success does not establish device refresh, latency or frame timing.
- The fixed-base Isaac headless run completed 83,308 steps in 600.0011 seconds.
  Full step intervals were mean 7.2019 ms / p95 7.7469 ms / max 12.7667 ms.
  Total GPU use was 3,336 MiB, including the existing workload; pre-run total was
  1,019 MiB. Process RSS was mean 3,355.23 MiB, with mean CPU about 2.09 cores.
  This is the approved Ubuntu 24.04 / Quadro RTX 6000 feasibility environment.
- All 43 driven joints, including seven per hand, match. Across 92 poses and 55
  aligned frames, maximum position/orientation errors were 0.000649038 mm and
  0.000105371 degrees. A real fixed-sensor-frame mismatch was corrected explicitly;
  the original URDF and failed baseline evidence remain available.
- Thirty-second custom-bridge diagnostics crossed the actual Linux-to-Windows
  SSH path. The 30 Hz capture had 900 live frames and three gaps above 250 ms;
  the 60 Hz capture had 1,798 frames and ten such gaps. Neither had sequence
  loss or reordering. These use a Python collector without qualified source
  clocks or a renderer. They fail the acceptance screen and do not substitute
  for either headset topology or the full-duration comparison.
- Physical audio onset, seated input and label legibility remain unmeasured.

LFS initialization, tracking and valid pointer creation are verified. After the
owner updated billing, both normal push and the documented all-object retry still
returned the server's disabled-service response. The local fixture commit is
retained; no unavailable pointers were published and no support request was sent.

## Decisions proposed for review

- Both standalone and Link remain candidates; build success does not pick a winner.
- Compare custom WebSocket and rosbridge with an identical public state contract.
  Prohibit trial/target metadata; apply the 250 ms stale-state fault.
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
| Developer recommendation | Hold G1; complete evidence and reconcile protocol |
| Gate-owner decision | Pending |
| Sign-off links | Pending |
| Re-review date | To be set by gate owner after operator availability is known |
| Hardware release | Not authorized |
| Phase 2 release | Not authorized |

If selected ADRs are accepted separately, record that explicitly with remaining
conditions and a dated re-review. Do not infer full G1 or a Phase 2 start from
partial acceptance. A trajectory fallback requires an explicit decision; it
cannot silently replace live Isaac state.
