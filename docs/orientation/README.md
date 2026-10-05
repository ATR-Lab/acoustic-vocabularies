# Silent orientation and preallocation eligibility

Issue #66. This is an engineering implementation. Common procedures and the
approved orientation script were unavailable. The supplied meanings, practice
pairings, stored second order, 60-second example practice window and screen
geometry are **drafts for protocol-owner review**. They are not a reconstructed
protocol or a study randomization schedule. No study audio or learner package
is loaded by the orientation assembly.

## Flow and durable handoff

`OrientationFlow` presents eight action cards, then eight target cards. An action
cannot advance until its demo reports completion at the common nominal duration.
The check uses `ResponsePanelController.Open` in practice mode; all 32 commands
remain available and every response retains the panel's deadline/lock behavior.
Written ordinary task feedback follows each item. Practice does not issue robot
commands or play sounds.

Eight correct on the first check records `pass_first`. Otherwise the exact same
action/target explanation is shown once more, followed by the same eight requests
in a different, explicitly stored order. Eight correct records `pass_second`;
any other second-check score records `fail`. There is no third check. Don't know
and timeout are incorrect check responses and remain distinct in the item record.
No allocation value, condition or study sound is exposed here.

The journal flushes each item and final outcome to disk before changing state or
notifying subscribers. A write failure, callback failure, tracking/input loss,
focus/pause or disabled component latches a fault. The flow does not silently
restart an exposed attempt. A new orientation attempt requires an operator's
decision under the approved protocol; the prior private log remains intact.

`OutcomeRecorded` publishes an immutable outcome only after the durable record.
`EligibleOutcomeRecorded` is true only for a recorded pass with reviewed content;
an engineering draft can produce a clearly labelled synthetic/engineering outcome
but never grants this gate. The operator console (#73) and allocation/session
engine (#67) must subscribe to the event and retain that gate. Those integrations
are pending. This is a necessary gate, not consent, human-subjects approval or an
apparatus qualification. All such approvals still apply before human use.

JSONL records include build/station/plan/demo-index identities, action and target
screens, actual demo duration, practice openings, selected and submitted values,
`COMMIT` / `DONT_KNOW` / `TIMEOUT`, item correctness, check totals and
`eligibility_outcome`. The final row contains both item-level correctness arrays,
one re-explanation count, `preallocation: true` and `learning_result: false`.
Full submitted item responses occur earlier in the same append-only log. Logs
stay in private operator storage; only synthetic tests may be committed.

## Actual demo requirement

`OrientationDemos` consumes #56's hashed `index.private.json` and its eight
`group: orientation` rows. The remaining execution rows are not played or parsed
as orientation items. The producer must report a complete 40-record suite. The
consumer checks the exact station, scene, neutral and canonical joint-order hash;
eight distinct actions; successful reset/replay flags; 300 frames at nominal
30 Hz; a common nominal 10-second duration; retained host timestamps; and no
time compression. Retained frame timestamps must match the declared span and
interval statistics. All streams pass the real #62 public-v2 parser and neutral
start check before any meaning can begin. A reviewed-content run also requires
the producer's collision and methodology review flags; kinematic display is not
claimed to validate grasp/contact physics.

Snapshot playback uses `StateSourceHost.PlaySnapshotTrajectory`, restores and
confirms the real neutral before each demo, and waits for the source's completion
event. It preserves recorded timing and holds the final frame through the common
nominal end. A late completion beyond one recorded sample period (33.33 ms at
30 Hz) faults and is logged, rather than shortening or retiming the movement.
That tolerance is the issue's proposed engineering interpretation and still
requires protocol review. Missing, incomplete, changed or mismatched demo files
block Start. Synthetic test trajectories are not visual demonstrations or robot
validation evidence.

**Live demo control is pending.** A public live state stream is read-only and
does not expose a private demo-command interface. The current host refuses a
live orientation with `ORIENTATION_LIVE_DEMO_CONTROL_UNAVAILABLE`. Implement and
validate the authenticated station command adapter before a live dry-run; never
substitute a fabricated movement or silently fall back to snapshot playback.

## Provisioning and operator runbook

1. Keep the reviewed Foundation, state-source and response-panel configurations
   private. The panel's `engineering_mode` must be `disabled`: orientation owns
   every Open call. Its configured controller ray or hand poke also controls the
   orientation Next button. Held inputs require release/withdrawal before use.
2. Copy `apparatus/orientation/orientation-plan.example.json` to
   `orientation-plan.local.json` in the app's persistent directory. Retain
   `engineering_draft` until the protocol owner has reviewed all wording, lists,
   order and timing. Reviewed content requires a non-null review-evidence hash.
   Do not insert allocation lists, seeds, codebooks or study audio paths.
3. Place the actual #56 index and referenced NDJSON files in the private
   `orientation-demos` subdirectory. Hash the exact retained bytes. Provision
   `orientation.local.json` from the public config example, matching the build's
   protocol version, plan hash and demo-index hash. `example_only` and null hashes
   cannot run. `allow_engineering_draft` is explicit and never qualifies a pass
   for allocation.
4. Build with `tools/build-unity.ps1 -Scene Orientation -Target Windows` or
   `-Target Android`, the pinned editor and reviewed G1 description. Test targets
   are `Test` and `TestPlayMode`. No dependency was added. The existing Unity CI
   runner/license guard remains unconfigured; local evidence is separate.
5. On a physical seated apparatus, verify every action/target screen, common
   demo duration, labels, pointer/poke alignment, viewing angle, reach, workcell
   occlusion, first response and loss/recovery behavior. Preserve private logs.
   Check the three eligibility paths with scripted engineering responses before
   any participant run. Inspect the final row and operator callback before
   testing the external allocation gate. Do not treat an editor preview or
   synthetic test outcome as a human eligibility observation.

The runtime screen is fixed relative to the calibrated observer reference and
does not follow the head. Its preliminary dimensions and text need headset
review. Orientation can record faults without revealing private paths in the
participant view. An unavailable configuration shows no allocation and remains
an operator startup failure.

Study B V1 refresher reuse remains a protocol decision; the current host always
runs the full preallocation check and does not invent a refresher schedule.

## Validation scope

Edit-mode tests cover the three required score paths, every first-check mistake
position, a maximum of one re-explanation, no eligibility before durable write,
draft-content blocking, missing/short/late demos, list coverage and invalid
configuration. Synthetic asset tests exercise the real public-v2 parser, exact
duration and metadata matching, missing/changed files and refusal of audio paths.
Play-mode tests move the real panel state machine through the three score paths
over actual Unity frames with an explicitly injected test clock. These are
software tests, not elapsed-time, headset or human eligibility measurements.

Actual #56 recordings, live demo control, operator-console/allocation integration,
approved protocol content and physical checks remain explicit qualification work.
Issue Acceptance criteria are reserved for human review.
