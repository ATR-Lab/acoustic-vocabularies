# Response panel engineering implementation

Issue #65. Development continuation is authorized; protocol, ADR-004 input choice
and physical headset legibility/reach remain unqualified. The #49 spike did not
produce human input/label measurements, so no final label size is inherited from it.
The public example is an explicit starting configuration and cannot run until an
operator creates a private reviewed copy. No study audio or learner package is
needed for this component.

## Response contract

`AcousticVocab.ResponsePanel` contains the public ontology, selection state,
monotonic deadline logic, process journal and fixed panel. It accepts target IDs
A–H and eight public action IDs. Targets A–D accept ADD_ONE, REMOVE_ONE, FLIP_CARD
and ALIGN_ARROW; E–H accept SCAN, TAG, CLOSE and QUARANTINE. Every visit can select
all 32 legal tuples. The interface has no taught-item filter or scoring input.

`ResponseState.Open(PanelRequest)` clears every selection. An active trial must
be completed or explicitly aborted before another can open. Same-family target
changes retain the chosen legal action; cross-family changes clear it. Disabled
Commit attempts, selection changes, repeated choices and post-lock input are
recorded. The first Commit or Don't know locks the state permanently for that
request. Clock regression, failed event persistence and an explicit abort prevent
further response. A timeout keeps the last selection as process data.

`PanelResponse.Target/Action` are populated only for COMMIT. `SelectedTarget` and
`SelectedAction` retain process choices separately, including DONT_KNOW and
TIMEOUT. Neither selecting an answer nor returning focus restarts a trial.

| Mode | Supplied anchor | Input opens | Deadline | Slot end owned by session engine |
|---|---|---:|---:|---:|
| FullMessage | measured audio onset estimate | 0 s | 12 s | 14 s |
| AtomicProbe | measured audio onset estimate (provisional anchor) | 0 s | 7 s | 9 s |
| LessonAtomic | lesson start | 6 s | 13 s | 20 s |
| LessonMessage | lesson start | 8 s | 17 s | 24 s |
| Practice | explicit engineering/practice start | 0 s | explicit caller window | caller-owned |

All times are host monotonic milliseconds in the same `Stopwatch` domain as
`PanelJournal.NowMs`. The caller supplies the verified onset/lesson anchor; an
audio API return time is insufficient. A deadline is exclusive: a Commit exactly
at the deadline first records TIMEOUT and then the late input. The panel records
the time it observed expiry; it does not pretend it processed an event during a
stalled frame. A fast response does not shorten any slot. Practice requires an
explicit 1–600,000 ms engineering bound; there is no default study duration.

Atomic modes display one role's eight options plus Commit and Don't know. The
selected label remains process data until explicit Commit. **Explicit atomic
Commit and anchoring the atomic-probe window to onset are provisional protocol
interpretations.** Review them against Common procedures before use with people;
do not treat implementation as approval of those choices.

## Integration

The scene component exposes `ReadyForTrial`, `Open(request)`, `Responded`,
`Faulted`, `CloseAtBoundary()` and `ConfirmInputRecoveryAtSafeBoundary()`.
`Open` requires tracked configured input, a ready Foundation and no panel fault.
The session engine must also satisfy its own reset, audio, schedule and protected
block guards. It receives the response event only after the process and response
records have been durably flushed. It owns slot completion, acknowledgment,
scoring, trial abort/retry policy and the next Open call.

Foundation faults and active input loss hide the panel and latch a panel fault.
Input recovery alone does not reopen it. At a safe boundary, recover Foundation
first, verify input, confirm panel recovery, then open a new explicit request.
An aborted attempt has no response and must not be silently converted to a
timeout or retried as though no exposure occurred.

The JSONL journal begins with build identity, station ID, exact panel configuration
and the clock frequency. It records `panel_open`, `select_target`, `select_action`,
`action_cleared_family_change`, `selection_repeated`, `commit_disabled`,
`before_window_input`, `post_lock_input`, `invalid_input`, `deadline_lock`,
`commit`, `dont_know`, `timeout`, `panel_aborted`, response and fault records.
Logs live in private app operator storage and are never imported into Assets.
Use opaque trial identifiers; the panel has no reason to receive an answer key,
condition label, participant name or lesson vocabulary.

## Private settings and input

Copy `apparatus/response-panel/response-panel.example.json` to
`response-panel.local.json` in the app's persistent data directory. Set
`configuration_status` to `provisioned_engineering` only after reviewing all
values, and match the installed build's `protocol_version`. A strict schema rejects
missing/extra fields, malformed JSON, out-of-range geometry and unknown methods.
The selected method must agree with `station.local.json`: `controller_ray` maps to
`controllers`; `hand_poke` maps to `hands`.

The world-space panel is placed once from the calibrated observer reference and
yaw, at the configured distance and vertical offset. It does not follow head
motion. Four-column target positions remain A–D on row one and E–H on row two;
selected-family actions occupy a fixed third row. Atomic options reuse the same
four-column grid. Before target selection, the family-action row is hidden.
The layout is shared across study/visit conditions; changing its private settings
requires apparatus review and a new recorded baseline.

Label height uses font glyph image extents and the measured TextMesh advance scale,
then `height = 2 * distance * tan(angle / 2)` at the calibrated eye reference.
Renderer line bounds alone include unused line spacing and understate visible
glyph size. A label that does not fit its button at the configured size raises a
fault; it is never silently shrunk. This is a geometric setup, not a measured
legibility result; off-axis glyph viewing and font rasterization still need the
headset ladder. [Unity's glyph metrics](https://docs.unity.com/en-us/engine/6000.5/script-reference/unityengine/characterinfo)
define the image extents used here.

The current preview panel covers much of the robot and workcell from its capture
camera. This is provisional engineering geometry. Review seated panel pose,
required workcell visibility, occlusion, reach and label legibility together on
the physical apparatus before freezing a layout.

Controller input uses the configured hand's tracked pose and a trigger edge;
connection/recovery while the trigger is held cannot generate a press. Hand input
uses XR Hands IndexTip with a 4 cm withdrawal before a segment crosses the button's
front face. Both hit the same button colliders. The intended device, pose alignment,
hand subsystem availability, accidental press rate and loss behavior still require
headset testing. No fallback silently substitutes one method for the other.

Reference APIs: [Unity InputDevices](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/XR.InputDevices.html),
[XR Hands joint pose](https://docs.unity3d.com/Packages/com.unity.xr.hands@1.6/api/UnityEngine.XR.Hands.XRHandJoint.html),
[renderer bounds](https://docs.unity.com/en-us/engine/6000.6/script-reference/unityengine/renderer/localbounds).

## Build and engineering smoke

Use the pinned editor and reviewed converted G1 cache already used by #61:

```powershell
./tools/build-unity.ps1 -Unity $Unity -Scene ResponsePanel -G1Description $G1Description `
  -Target Test -ProtocolVersion engineering-pending-review -BuildId panel-tests-reviewed
./tools/build-unity.ps1 -Unity $Unity -Scene ResponsePanel -G1Description $G1Description `
  -Target Android -ProtocolVersion engineering-pending-review -BuildId panel-quest-reviewed `
  -TemporaryDirectory $PrivateTemporaryDirectory -GradleCache $PrivateGradleCache
```

Use `-Target Windows` for the Link player. The generated response scene and meshes
stay ignored. Build validation permits only the reviewed component, includes its
font/shader dependencies explicitly, and keeps tests and editor preview code out
of participant builds. No package was added. The existing CI runner/license guard
must be configured before its actual Unity job can run; local results are separate.

For a one-shot engineering run, select an explicit `engineering_mode` and matching
role in the private panel config. Once Foundation and the selected input are ready,
the panel opens that single request using an engineering start timestamp. This
mode plays no sound and does not claim a measured audio onset. After a response or
fault, it never starts another request automatically. Set `engineering_mode` to
`disabled` when the session engine owns Open calls.

Deploy the APK/station config with #59's script; copy the private panel config to
the same persistent-data directory through the explicitly selected device. Check
all modes, first-response lock, exact-boundary timeout, family clearing and input
loss. Preserve logs privately. There is no connected Quest in the implementation
environment, so deployment and those observations remain operator work.

```powershell
& $Adb -s $DeviceSerial push $PrivatePanelConfig "/sdcard/Android/data/org.acousticvocab.experiment/files/response-panel.local.json"
```

`PanelPreview.Capture` is an editor-only, offscreen render of seven mode/selection
states. It uses synthetic request timing, a fixed camera and the public geometry
example, writes only to a fresh `.local` folder, and never supplies tracked HMD
state or edits persistent app configuration. Its images check arrangement and text
fit; they do not establish headset legibility, reach, comfort or input accuracy.

Before qualification, complete #49's controller/hand comparison and text ladder,
review atomic submission/onset policy and all wording against the protocol,
integrate #67's slot controller and perform the physical seated checks. Every
Acceptance criterion remains for human review.
