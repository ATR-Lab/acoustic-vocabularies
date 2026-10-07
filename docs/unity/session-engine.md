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

Hosted repository checks passed. The hosted Unity configuration gate remains
failed because its isolated licensed runner and required configuration have not
been provisioned. Local results do not bypass or qualify that gate.

See [the journal handoff](../interfaces/session-journal.md) for the #72 adapter
boundary. Exact methodology CSV headers, export, encrypted archival and upload
are owned by #72 and are not implemented here.
