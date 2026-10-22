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
eight distinct actions; successful reset/replay and execution flags, empty
execution failures, a protected-factory unchanged-state proof, exact private
plan identity and a valid expected-object hash. Every one of the 40 rows must
carry the exact #56 fixed sim-step `capture` record (`isaac/demos/recording.py`;
see `docs/isaac/demos.md`): the field set is exact, `capture_complete` and
`schedule_ok` are true, the `schedule` is `fixed_sim_step` with 300 samples at
30 Hz, 2 physics steps of 1/60 s per sample and 600 total steps, every row
records the identical 600 physics steps (10 s), `playback_clock` is
`host_monotonic_fixed_sample_period`, host timestamps are retained and nothing
is time compressed. A pre-fixed-step capture record (host-span `timing_ok`
screen) is refused as `ORIENTATION_DEMO_LEGACY_CAPTURE_FORMAT`; any other field
difference is `ORIENTATION_FIELDS`, and a schedule or duration difference is
`ORIENTATION_DEMO_SCHEDULE` / `ORIENTATION_DEMO_DURATION`. Each orientation
stream has 300 frames whose retained host stamps must progress and match
`capture_host_seconds` and `capture_interval_ms`. Those stamps are unpaced
capture provenance, so their span (which may exceed 10 s) and gaps are not
qualification criteria. All streams pass the real #62 public-v2 parser, the
fixed-step frame check (`sim_step` 2, 4, ..., 600) and neutral start check before
any meaning can begin. A reviewed-content run also requires the producer's
collision and methodology review flags; kinematic display is not claimed to
validate grasp/contact physics.

Snapshot playback uses `StateSourceHost.PlaySnapshotTrajectory`, restores and
confirms the real neutral before each demo, and waits for the source's completion
event. Playback is paced on the host monotonic clock by sample index (sample *i*
at *i*/30 s, the last sample held to the common 10 s end), never by simulation
time or the captured host stamps, so every demo has the same displayed duration.
A late completion beyond one sample period (33.33 ms at
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

[Validation metadata](validation.json) records 186/186 edit-mode tests, 8/8
play-mode tests, 137 Python passes (one Linux-only skip), schema checks and the
exact build/test revisions. Both clean native builds succeeded with zero errors:
Windows Mono `orientation-link-001` and Android ARM64 IL2CPP
`orientation-quest-002`. The latter APK is 54,742,242 bytes, SHA-256
`6ff6272d6eadba69a22ce74091f9ccece83dd87a0bac1bd68236dac8cc6b982d`.
The full public manifests record every output file; binaries and raw logs stay
private. No Quest was connected for deployment or runtime verification.

Edit-mode tests cover the three required score paths, every first-check mistake
position, a maximum of one re-explanation, no eligibility before durable write,
draft-content blocking, missing/short/late demos, list coverage and invalid
configuration. Synthetic asset tests exercise the real public-v2 parser, exact
duration and metadata matching, missing/changed files and refusal of audio paths.
Play-mode tests move the real panel state machine through the three score paths
over actual Unity frames with an explicitly injected test clock. These are
software tests, not elapsed-time, headset or human eligibility measurements.

The retained actual #56 library is present and is deliberately rejected: its
`recording_complete` flag is false. `OrientationRecordedEvidence.VerifyRejected`
uses its exact index and neutral hashes and confirms
`ORIENTATION_DEMO_UNQUALIFIED` before any frame is applied. This is a successful
rejection test, not a demonstration qualification. The library's failed timing
and visible grasp issues remain with #56; no flag or timestamp is rewritten.

The static preview tool renders five synthetic screen states without applying a
robot demo, accessing persistent application configuration or producing human
eligibility evidence. The wide camera shows the complete provisional layout;
the screen is above the response panel, and the panel obscures part of the
workcell. Glyph size, viewing angle and reach still require headset review.
Three synthetic journal traces also verify that an actual on-disk outcome is
readable before its callback runs. They use an injected clock and draft content;
they never grant the host's allocation gate.

Qualified #56 recordings, live demo control, operator-console/allocation integration,
approved protocol content and physical checks remain explicit qualification work.
Issue Acceptance criteria are reserved for human review.
