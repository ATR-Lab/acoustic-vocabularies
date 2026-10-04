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
| #46 | One-time import/reproducibility work tracked in [issue46](https://github.com/ATR-Lab/acoustic-vocabularies/issues/46) | Imported prefab, canonical map against loaded Isaac inventory, three pose comparisons, render cost and headset checks |
| #44 | [PR93](https://github.com/ATR-Lab/acoustic-vocabularies/pull/93), host/container provenance, isolated setup and harness | Ten-minute step/resource evidence, viewport screenshot, concurrent-instance/environment checks, OS/GPU qualification, asset-use license decision |
| #47 | Candidate prototypes tracked in [issue47](https://github.com/ATR-Lab/acoustic-vocabularies/issues/47) | Full 2-option × 2-rate × 2-topology runs, reconnect tests, CPU/client cost, live headset capture and transport/rate recommendation |
| #48 | [PR94](https://github.com/ATR-Lab/acoustic-vocabularies/pull/94), compiled click harness and tested onset analysis | Independent clock synchronization, 200 physical onsets per route/mode, acoustic/electrical comparisons, real uncertainty and equipment photo |
| #49 | [PR95](https://github.com/ATR-Lab/acoustic-vocabularies/pull/95), compiled controller/poke panel, legality check and tested analysis | Three internal testers, 32 commands/method, legibility ladder, input-loss confirmation and captures |
| #50 | [PR97](https://github.com/ATR-Lab/acoustic-vocabularies/pull/97), seven Proposed ADRs, engineering schema CI, hardware arithmetic, model provenance | Real measurements cited, private protocol/template reconciliation, runtime/decoding qualification, review and merge |
| #51 | This readiness packet and risk register | All above criteria, review notes, developer/gate-owner sign-off, Accepted ADRs on main |

No empty template or synthetic test result counts as a measured spike. Consult
each spike report for the newest actual data rather than interpreting this table
as a substitute for its acceptance criteria.

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
