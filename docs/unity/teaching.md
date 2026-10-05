# Teaching lessons (#68)

This module implements the issue-defined teaching timelines. It does not qualify
participant use: the methodology folder and reviewed teaching text/images are
still unavailable, device onset/highlight timing is unmeasured, and the actual
private control listener has not yet been exercised by this Unity client.

`TeachingContentFactory` handles only `atomic_lesson` and `message_lesson` slots
from the verified #30/#32 schedule. A session-level factory multiplexer must route
the other blocks to their own modules. `TeachingSessionHost.Install` accepts an
already verified catalog, the stored comfortable gain, a qualified route report,
independent control/renderer services and durable event sinks. It never chooses
a participant, visit, sound profile, atom candidate or random seed. The scene
remains concealed and silent until the trusted session owner installs it.

| Lesson | Plays (seconds) | Retrieval | Meaning visible | End |
| --- | --- | --- | --- | --- |
| Atomic | 0, 6, 14 | 6–13 | 0–6 and 14–20 | 20 |
| Whole message | 0, 8, 18 | 8–17 | 0–8 and 18–24 | 24 |

An early response locks the one retrieval without shortening the slot. A timeout
is instructional feedback. There is no replay control or kinematic consequence.
The third play must complete before the module requests its final backend reset.
The old presentation retains its owner token until the fixed end, even when the
engine prepares the next item during the remaining tail. Preparation preloads
audio but does not expose the next meaning or image.

The factory implements `ISessionContentPump`: `FixedSlotEngine.Tick()` pumps
private control, advances the response-panel deadline, then advances each active
and retired lesson timeline before evaluating boundaries. The session-level
multiplexer delegates the interface to its modules. A bound teaching host does
not independently tick the engine. All use the absolute local Stopwatch clock. Sim
time is not used for human intervals. Missing/late scheduling, journal failures,
focus/input loss, missing onset authority, or reset failures stop further plays
and require explicit recovery; nothing is replayed automatically.

Aligned whole-message lessons use the qualified `AUDIO_ONSET_ESTIMATED` event,
actual action PCM sample count / 48000, exactly 9600 silent samples, and referent
sample count. Highlights are off through the 200 ms gap and throughout retrieval.
Dictionary uses the same display, image, PCM and timing with no highlights. The
20 ms onset/error bounds are a conservative **proposed engineering screen**, not
measured apparatus qualification. Observed display/highlight timestamps and
expected edges are logged separately; a late frame is never backdated. Images
decode before the cue. Display/highlight intents persist before changing the
view; success timestamps sample the clock after the software visibility call.

Study A permits aligned whole-message lessons only. Study B binds the supplied
allocation bytes by their independently trusted SHA-256, finds the exact stored
person slot, and checks `SQ-1` → K structured or `SQ-2` → Q structured. The stored
schedule presentation must match. `ITeachingSelections` is supplied by #70's
verified selection/admission path; this module has no default B profile or rank.

## Private content provisioning

The **provisional** directory format is `catalog.local.json`, `review.local.json`
and exactly the PNGs named by the catalog. No example participant package or final
feedback wording is committed. The trusted admission path supplies catalog and
review SHA-256 pins. The review must bind a methodology document hash and the
exact catalog bytes; setting a local `approved` flag without those pins cannot
load content. This is an application trust boundary, not a cryptographic signature.

The catalog contains `format: "av-teaching/1"`, `package_sha256`, `content`,
`images` and `feedback`. Content has exactly the 16 atoms and 18 trained phrases
from the package-bound permutation. Each record contains `content_id`,
`meaning_display_id`, `definition`, `action_words`, `target_words`, and `image_id`.
Each image maps to a restricted `images/<opaque-id>.png` path and SHA-256. Feedback
has exactly `atomic`, `correct`, `incorrect`, and `timeout`, each with a distinct
`id` and reviewed `text`. Review has integer `version: 1`, `catalog_sha256`, boolean
`approved: true`, and `methodology_sha256`. Extra files, links, changed bytes and
held-out IDs are rejected. Semantic scoring comes from the package permutation;
the catalog cannot supply a different correct action/target.

## Control and logging boundaries

`StateIntegration.PrivateModeResetClient` requires a separately provisioned
loopback `/commands` endpoint and independently pinned control-session ID. It
sends only `set_mode` and `reset`, serially, with unique request IDs. An accepted
matching reset reply, current mode, progressing health sample, neutral-verification
age, publisher age and local receive age are required. HTTP `/health` avoids
filling the server's command-id cache. The public state channel is never used for
commands, and a rendered neutral never substitutes for the private acknowledgement.
There is no automatic reconnect or command retry. A teaching health record need
not say `exposure_ready` (the server reserves that for protected mode); a test
mode client requires it. Loopback SSH forwarding remains an explicitly separate
operator-provisioned diagnostic transport and carries no timing qualification.

Every play gets its own `audio_request_id` and `presentation_index` 1–3, alongside
the stable opportunity ID and distinct attempt ID. `LessonEvent` carries
`meaning_display_id`, observed/expected monotonic times, retrieval opportunity,
feedback ID, composite PCM SHA-256 and constituent hashes. `display_start` and
`display_end` pair software visibility intervals; actual HMD scanout remains
unmeasured. The audio sink separately
persists #64's full request/onset/callback/completion fields. Callbacks are
synchronous durable transactions: exceptions prevent scheduling or abort the
ticket. #72 must retain its distinction between estimated onset, acoustic evidence
and uncertain consumed exposure. No hidden semantic answer enters these events.

The #14 `GrammarAssets` loader admits only the pinned `ready-cue` and
`click-grammar-demo` canonical WAVs and validates file/PCM hashes and sample
counts from the reserved registry. `BeginGrammar` is an explicit admission action
with an independently verified neutral/control gate. It shows READY, then static
Action/Target labels with the compound click. Each plays once; the second waits
for software completion of the first. Its 750 ms scheduling lead between plays
is a proposed engineering sequence pending review against the missing script.
An unmeasured route can use the nonsemantic calibration path, with null onset
qualification logged. Semantic lessons still require a qualified route.

`ReadPostStudyDictionaryAtom` has no audio path. It accepts one
`IPostStudyDictionaryAuthorization` consultation bound to package hash, schedule
hash and one atom ID, only for B W4. #69 supplies the one-use authorization after
final forms, validity and optional-task admission. Phrase IDs are refused.
Execution belongs to the separate qualified demo provider.

## Local verification

Build with `tools/build-unity.ps1 -Scene Teaching -Target Windows` or `Android`,
supplying the existing reviewed G1 description and private build identity. The
generated scene binds the actual workcell/state renderer, response panel and
isolated audio source. It starts without admission or semantic content.

Edit tests use synthetic PCM with producer package structure. For the actual
producer B check, set `AV_PACKAGE_DEMO_ROOT` to the verified DEMO-only #13 artifact
containing `package-demo` and `dyad-demo`. No participant package is a fallback.
The PlayMode timeline test uses an explicitly simulated clock and no acoustic
output; it is not a device timing measurement.
