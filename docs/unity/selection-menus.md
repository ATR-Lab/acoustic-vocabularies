# Study B selection menus (#70)

This development module implements the issue-defined 60-second profile menu and
45-second atom menus. It is not participant-ready: the #26 qualified bank handoff,
reviewed methodology/script files, independently qualified audio route, and mock
headset dyad remain required. The currently supported producer input is explicitly
DEMO-only `av-sound/provisional-bank` from #13; the loader refuses participant
admission rather than treating those engineering fixtures as an accepted bank.

The [software validation record](selection-menus.validation.json) binds 278
EditMode tests, seven PlayMode tests, and successful Windows/Android builds to
the tested source. The separate [store interop record](../interfaces/menu-store-bridge.validation.json)
records four actual Unity-to-Python mailbox requests and 47 Python tests.
Neither record establishes participant admission or headset/acoustic qualification.

`MenuTimeline` requests eight plays per menu. Profile offsets are 6.5, 10.5, 14.5,
18.5, 22.5, 26.5, 50 and 54 seconds. Atom offsets are 5, 8, 11, 14, 17, 20, 35 and
38 seconds. Choices may change only during 30–45 seconds or 22–32 seconds,
respectively. At the deadline the last valid choice takes effect; absence of one
selects the first stored option and records `defaulted=true`. No extra listening
control exists. The last two plays require a verified selection receipt.

The engine closes its lifecycle at the neutral tail start (58 or 40 seconds) to
request reset and prepare the following content. Those lifecycle timestamps are
not choice deadlines. The menu owns its display through the complete 60/45-second
slot. Preparing future content never displays it, and the external sole session
engine calls the content pump before processing any boundary.

## Reviewed assets and authorities

`MenuCatalog` checks the #13 package/bank pins, #31 allocation file and stored
profile order, package-bound permutation and wave atom order, and exact current
schedule slots. Candidate ranks are always 1–3; reserve rank 4 is unavailable.
All candidate PCM is read through the #64 package loader, including its post-load
file/hash checks. Profile examples are the three #14 `calibration-P*` files with
96,000 samples each; their registry/file/PCM hashes are verified.

A separate private directory has exactly `menu-script.local.json` and
`review.local.json`. The script identifies the package, profile display ID,
profile names, profile/atom instructions, active-choice/assigned wording and
three candidate labels. The independently pinned review binds that script to the
existing teaching review and methodology hash. The menu obtains each current
wave's immutable reviewed atom definition/image from `TeachingCatalog` through a
one-use selection-meaning authorization. It does not maintain another semantic
answer parser. No supplied example is a final participant script.

`MenuContentFactory` owns the AudioPlayer subscription only while the menu module
is active. A joined session must lazily create/dispose block modules; overlapping
lesson/assessment/menu subscriptions are invalid. The optional
`Action<SlotContext,int,PcmWave>` binding hook runs immediately before each actual
schedule request with a zero-based index and the already verified PCM. A joined
host uses this to bind one durable audio/data/frame observer without rereading or
rescheduling the sound.

The real host requires separately acknowledged private `test` mode, a matching
reset receipt, current neutral renderer readiness, focus, tracked input, verified
store state, and a measured route with onset uncertainty no greater than 20 ms.
There is no inferred reset from a rendered neutral image and no unmeasured-route
trial fallback. Controller ray and hand poke use the installed XR APIs; physical
comfort, pointing accuracy and the device selection remain operator checks.
Uninstalled/disabled/faulted hosts conceal all content and abort through the
still-attached durable audio observer. Cleanup stages are independently attempted.

## Ledger and replay

`MenuLedger` creates a new private append-only JSONL segment. Each canonical line
carries sequence, prior hash and record hash, and is flushed durably before its
effect is allowed. The header binds package, bank, allocation, schedule, unit,
review, visit, role, expected menu order, UTC start and monotonic epoch. Each menu
event binds attempt/opportunity/menu/meaning-display IDs; each play also binds its
request ID, candidate, whole-WAV/PCM hashes, presentation index, nominal time,
observed time, and qualified onset uncertainty. No answers/scores/comments enter
the menu event schema.

Sealing requires all menus, three independently pinned candidate files, eight
correlated requests/onset authorities/completions per menu, final selection and
independently re-read store receipt, and all display transitions. The onset check
uses the event's reported uncertainty, capped at 20 ms. Display transitions and
the final choice are observed on the first rendered frame at or after their
boundary, so their stamps lag by up to a frame plus in-frame work. `MenuTimeline`
refuses such an observation more than 250 ms after its boundary
(`MENU_DISPLAY_LATE`/`MENU_DECISION_LATE`) and the active seal applies the same
bound, the limit already used for onset observation and the frame-gap fault. A
check first discovered at the seal, after every menu's exposure, would end the
visit without protecting any exposure. The 20 ms display residual remains a
separate engineering screen (`MENU_DISPLAY_TIMING_SCREEN_FAILED` in mock-visit
reconciliation); yoked rows keep the 20 ms comparison against the recorded active
offsets. None of these is evidence of acoustic or physical display timing or a new
approved protocol tolerance. Incomplete, interrupted,
late or malformed segments remain on disk but cannot authorize yoked replay.
An interruption's event ID is also its `matching_deviation_id`; recovery never
automatically repeats a partial exposure. Explicit reconstruction/owner review is
needed before a replacement active sequence can be admitted.

The [native rerun record](selection-menus-native-017.validation.json) covers #220.
Simulator player `simulation-native-017` was built clean from a local test merge
of main, #201, #199 and this change. It ran B V1 active with a fresh DEMO store
and Resume at each block-boundary pause. The profile menu and all eight atom
menus completed, and the ledger sealed (`menu_count` 9). The atomic-lessons
preflight passed. Atomic lessons (8/8) and message lessons (8/8) followed.
The visit then stopped in the second trained assessment item with the separate
`ASSESSMENT_RESPONSE_LOG_MISSING`. Reconciliation verified integrity and
recorded 123 of 123 requested plays with software completion. It no longer
lists the unsealed-ledger reasons. It still reports the
`MENU_DISPLAY_TIMING_SCREEN_FAILED` and frame-budget screens. This is
simulator engineering evidence only.

`MenuReplaySequence.Load` requires an independently pinned sealed active-file
hash and active binding, verifies canonical bytes/hash chain/schema, independent
assets and actual store receipts, and refuses age outside 0–24 hours. The session
owner supplies the UTC time and explicit monotonic replay anchor. Recorded
inter-menu gaps are retained, never added again on resume or compressed after a
missed anchor. A missed anchored start faults before content creation or any
exposure consumption; previous completed trial history remains intact.

Yoked audio uses recorded onset offsets and stored final selection. Yoked display
transitions use recorded observed offsets. Every yoked event references its
corresponding active source event. No active cursor or intermediate choice is
copied. `CompareYoked`/`SealYoked` check independent hashes, meanings, role/unit
bindings, all eight audio events, selected/defaulted outcome, relative gaps and
bounded timing against that exact active sequence. A returned comparison means
those checks passed; it does not assert a headset or acoustic measurement.

## Store boundary and remaining work

`FileMenuStore` is the concrete Unity client for the private local mailbox served
by [`tools/menu_store_bridge.py`](../../tools/menu_store_bridge.py). The Python
process uses the actual #11 `VocabularyStore`, including its normal reserved
signals and separation threshold. The client never executes Python or receives
recipes. See the [closed bridge contract](../interfaces/menu-store-bridge.md).

The caller independently pins the bank, package, decoded configuration, initial
snapshot manifest, exact book head and complete snapshot digest. Construction
requests fresh verification and remains unready until its response arrives.
Each profile or atom request has a unique ID, the previous head/snapshot pins and
a durable client intent. One request may be outstanding. Request and response
files are published as complete new files and retained as private evidence.

The bridge holds the store writer lock, writes a durable intent, performs the
actual commit, verifies all old entries and chosen bytes, and durably records the
receipt before replying. The client checks the closed response and receipt,
self-hashes, exact request/configuration bindings, coherent resulting snapshot,
all earlier entries, and selected PCM/file hashes against the loaded package.
It persists the verified checkpoint before exposing a receipt to the menu. A
missing, rejected, late or mismatched response blocks the selected repeats.
Profile selection cannot stand in for an atom commit. An upstream `E_REJECTED`
remains a rejection; no reserve, alternate candidate or weaker validator is used.

At the final menu's neutral tail, the real factory requests a fresh complete-wave
verification. Reset completion and the subsequent teaching handoff remain blocked
until that succeeds. `Get` then returns only verified stored profile/rank choices.
Read-only yoked clients verify the completed source wave and its actual selection
receipts. Hashes establish identity/integrity; private mailbox permissions and the
trusted admission owner provide the process boundary. This is a local operator
transport, not an Android-to-workstation network service.

A new active client accepts an exact prior-wave snapshot (0, 8 or 12 entries);
a yoked client requires that wave's complete snapshot (8, 12 or 16). Partial-wave
process reconstruction is intentionally refused. A retained store client may
survive a normal safe module replacement, but it does not repair an interrupted
presentation ledger. Preserve partial bytes and obtain an explicitly reviewed
reconstruction before admitting a replacement sequence. After an uncertain store
write, only the identical request may recover its durable intent. A terminal
mailbox error is retained; CLI recovery uses a fresh output file after inspection.

The module host takes the response panel's configured input side for both
controller ray and hand poke. The joined owner must uninstall each host before
another module acquires the shared player, construct a fresh backend/host lease,
and complete the asynchronous mode handshake before explicit resume. The separate
session-owner API does not supply a complete production admission bootstrap.

Required operator evidence includes reviewed private assets and #26 bank handoff,
full active/yoked V1–V3 ledger comparison (128 atom-menu + 8 profile plays per
person), native headset input/legibility recording, actual route onset calibration,
interruption/reconstruction procedure and typed #72 export/template verification.
Missing read-only methodology templates keep exact-header acceptance pending.
The [runbook](../spikes/O5.5.4-runbook.md) and
[blank result template](../../apparatus/spikes/O5.5.4/results-template.json) keep
engineering, simulator, acoustic and physical-headset evidence separate.

## Provisional logging handoff

`MenuExposureProjection` returns one immutable row for every play request,
including both rejected candidates and the selected repeat. The proposed #72
mapping is:

| Requested field | Typed value | Interpretation |
|---|---|---|
| `candidate_id` | `CandidateId` | Exact verified file option; no hidden answer |
| `accepted_or_rejected` | `AcceptedOrRejected` | `accepted`, `rejected`, or `unresolved` until verified final choice |
| `active_choice_or_default` | `ActiveChoiceOrDefault` | `choice`, `default`, or `unresolved` |
| `yoked_source_event_id` | `YokedSourceEventId` | Exact active play-request event |
| `pause_ms` | `PauseMs` | Recorded gap before this menu; repeated on its rows |
| `matching_deviation_id` | `MatchingDeviationId` | Interrupted menu event ID, or absent |

`PlaybackStatus` distinguishes requested/uncertain, qualified onset observation,
and software completion. These fields never upgrade software completion into an
acoustic measurement. This typed derivation is available to #72; it is not a
claim that the missing read-only methodology's exact CSV headers were validated.

Cancellation before `MenuTimeline.Start` writes no menu event because preparation
has not displayed a menu, requested a play, or committed a selection. The engine
and frame/data journals retain the pre-cue cancellation. Explicit resume must
replace the invalidated module/backend lease; its new timeline can use the
untouched visit ledger and fresh audio request IDs. It does not restart the old
timeline or compress the slot. Once `Start` is attempted, interruption still
permanently invalidates the segment, even before the first display or audio.
That partial segment is preserved and cannot seal; a fresh complete segment and
explicit reconstruction/admission are required before yoked replay. The separate
module-lease restart must also create a fresh backend authority. The joined owner
calls `MenuSessionHost.Uninstall` before creating another module so delayed host
focus/disable/destroy callbacks cannot abort the later owner's shared player.
