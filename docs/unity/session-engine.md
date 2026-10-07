# Fixed-slot session engine — development contract

`AcousticVocab.SessionEngine` consumes the existing #30 visit schedule, #29
permutation, #13 sealed audio package and #32 run-sheet evidence. Development is
authorized; missing methodology reconciliation, qualified apparatus timing and
the unfinished #68–70 content modules still prevent participant use.

`ScheduleLoader.Load` verifies the pinned producer schemas, the package's exact
schedule/permutation file hashes, private intended tuples, legal inventory,
held-out exclusion from lessons/dictionaries, stored atom order, family
alternation, once-per-pass pools, assessment totals and validity coverage. It
does not expose intended tuples, allocation factors or seeds in `SlotItem`.
DEMO inputs require explicit opt-in. The run-sheet CSV keeps the producer's
columns: `hash_check` means the **package** hash. The run-sheet manifest binds
the schedules-manifest hash, which binds the actual schedule bytes. A placeholder
hash or unbound CSV cannot authorize a cue. The original methodology template
has not been independently located on this workstation.

`ISlotContentFactory` provides the content-specific module. `SlotReadiness`
requires separate package/hash, audio preload, backend reset acknowledgement,
renderer readiness, panel idle, focus, input and matching backend-mode gates.
No rendered neutral state manufactures an acknowledgement. `ResetComplete`
means a **new** post-item reset acknowledgement plus current neutral rendering,
not the receipt used before the cue. Modules must use the qualified audio path;
the unmeasured calibration-only player cannot implement participant playback.

The engine fixes each onset and following slot boundary to the monotonic clock.
An early response never shortens the slot. After the response/reset portion it
may prepare and schedule the next item during the remaining tail; its requested
audible onset still equals the preceding slot's end. A missed readiness/reset
deadline pauses at a safe boundary. Lessons/menu internals belong to their
modules. This engine does not fill absent modules with demonstrations, speech,
or generic playback. #62 renderer binding, #55 private reset/mode transport,
#65 response binding and #68–70 production modules remain integration work.

Each `SlotContext` has a stable `OpportunityId` (the original scheduled trial
ID), an attempt `Item.TrialId`, optional `RetryOf`, and immutable distinct
`AudioRequestIds` for the scheduled play credits (0/1/3/8). Modules must use these
IDs to join individual playback events; unused menu credits are not audible
exposures. A technical retry gets a new attempt ID and new audio IDs while
retaining the original opportunity ID. Therefore 36 scheduled opportunities can
be distinguished from additional technical attempts without changing the
original record.

Before calling `RequestCue`, the engine durably records cue intent as consumed
and uncertain. A crash, missing callback or uncertain onset never earns another
play. Only trusted explicit `ConfirmedNoOnset` evidence with its SHA-256 can
queue one single-play retry at block end; multi-play no-onset is refused. Later
audible/uncertain evidence disables an unplayed retry. Ordinary Unity software
callbacks cannot positively prove acoustic absence. Novel buffers require the
engine's one-use package/message-bound `INovelSlotAuthorization` immediately
before composition; it is an application boundary, not an authentication token.

Startup and crash recovery require operator confirmation. Unplayed items can
resume; any durably requested or unresolved exposure is skipped and preserved.
Pause before a scheduled onset interrupts the pending request but conservatively
retains it as uncertain consumed unless a trustworthy no-onset receipt arrives.
There is no automatic cue retry or automatic interruption recovery.

The 54 current edit-mode tests cover all seven real public DEMO visit contracts,
semantic fault injection, 36 × 14 s virtual timing, first response/deadlines,
individual gate refusal, crash boundaries, one-use novel authorization,
contradictory onset evidence, journal tampering and repeated torn-tail recovery.
Separate integration also loaded real producer-sealed DEMO A/B audio packages
through `PackageLoader` and the generated #32 hash chain for all seven visits.
Unity 6000.6.0f1 passed all 54 cases. These are software checks, not acoustic or
participant acceptance.

A real elapsed-time desktop run with mock content, early responses and the
durable disk journal completed 36 opportunities in 504.0179885 seconds measured
from the first scheduled onset. Its maximum observed engine-Tick gap was
823.0419 ms under desktop workload; the result establishes total block duration,
not cue delivery latency or headset performance. No audio was played. Private
raw evidence is retained and its hashes are in `session-engine-validation.json`.

Three play-mode lifecycle tests inject focus loss, reopen the durable journal
across engine recreation, and withhold the new post-item reset acknowledgement.
All three passed using mock content and a controlled clock. Windows x64 Mono and
Android ARM64 IL2CPP builds at `44de0e0` passed with zero errors; the generated
native output includes the retained session-engine assembly. The Foundation
scene does not yet bind participant content. Physical on-device mock blocks, process-kill/device
recovery, qualified onset evidence, #62/#65 bindings and #68–70 modules remain
pending. Engine recreation is not described as a process-kill test.

## Native process-kill resume harness (#67 AC2)

`tools/session-kill/run-kill-resume.ps1` (PowerShell 7) runs the AC2 check
against the Windows SimulationTest player:

1. Launch the player with `-simulationMockBlock <run root>` and the pinned
   simulation capability. The run root must lie inside the capability's
   `output_directory`.
2. Send `load` and `start` through the normal durable operator mailbox.
3. Wait until the planned item's `CueRequested` and `audio_request` rows are
   durable in the DataJournal and its `Done` row is not.
4. Terminate the process with `Stop-Process -Force` (TerminateProcess). No
   shutdown code runs.
5. Relaunch the player on the same journal and explicitly `start` it again.
6. Wait for the block to finish and export.

`python -m tools.mock_visit.kill_resume` then checks the retained records:

- Exactly two process clock epochs, in order.
- The kill came after the last record the harness observed.
- The killed item was the only open cue. It stays consumed/`Uncertain`, and
  its trial-log row is `exposure_consumed=true`, `interrupted=true`.
- The second process's first session event is `operator_resume`.
- The first cue after resume is the next unplayed item.
- No item cued before the kill appears again.
- Every opportunity has at most one audio request and one exposure-ledger row.

`SimulationMockBlockHost` exists only in the SIMULATION_TEST scene. The build
verifier refuses it anywhere else. It does nothing without `-simulationMockBlock`
and the compiled capability. It runs the real `FixedSlotEngine`, DataJournal,
operator mailbox, `AudioPlayer` and export, and its content is synthetic:

- `MOCK-nn` single-play 14 s slots.
- A 0.5 s silent cue at the capability gain on the simulation audio route.
- Mock hash, reset, render, panel, focus and input gates.
- Mock operator health.
- No backend, package, panel or participant material.

A run therefore establishes process-level journal recovery for the engine. It
says nothing about joined modules, acoustic onset or headset behaviour.

One run on this workstation (`o551-sim-001`, source `aef2be5`) used the Meta XR
Simulator 205.0.0 as the OpenXR runtime, on the desktop with no headset. The
block had four slots, and the kill was planned for `MOCK-02`.

- The first player was killed about 14.0 s after `start` was accepted, immediately after `MOCK-02`'s
  durable cue and audio request (record 25). This was before any delivery
  callback was recorded.
- The relaunched player recovered 25 records and counted 2 opportunities as
  consumed. It waited for `start`, then cued `MOCK-03` and `MOCK-04`, completed
  the block, and exported with exit code 0.
- The verifier passed all eleven checks. There was one audio request and one
  exposure row per opportunity, with no duplicate first exposure.

Exact pins are in `session-kill-resume.validation.json`.

Hosted repository checks passed. The hosted Unity configuration gate remains
failed because its isolated licensed runner and required configuration have not
been provisioned. Local results do not bypass or qualify that gate.

See [the journal handoff](../interfaces/session-journal.md) for the #72 adapter
boundary. Exact methodology CSV headers, export, encrypted archival and upload
are owned by #72 and are not implemented here.
