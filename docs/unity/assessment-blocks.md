# Protected assessments and forms

This is the development implementation for [#69](https://github.com/ATR-Lab/acoustic-vocabularies/issues/69). Participant qualification remains blocked by the apparatus gates and absent methodology reconciliation. It consumes the existing producer schedules; it does not regenerate a randomization or infer intended answers.

`ScheduleLoader` verifies trained → novel → atomic order, B V2/V3 pre-old placement before teaching, once-per-pass pools, fixed counts and stored final-validity interleave. `VisitSchedule` also carries the verified study, set and speech-list hash. These are identity checks, not participant-facing text.

`ProtectedContentFactory` implements `ISessionContentPump`. The engine pumps its response deadlines and retained visual tails before engine transitions. Full-message trials remain 14 seconds, with the response deadline at 12 seconds; atomic trials remain 9 seconds, with the deadline at 7 seconds. Commit, Don't know and timeout receive the same acknowledgment during the final two seconds. An early response closes the response panel but does not advance the slot. Preparing the next item does not release the previous item's acknowledgment.

The acknowledgment interface accepts only `Neutral()` and `Acknowledgment()`. It has no intended tuple, correctness, score, replay or execution input. The existing response panel offers all legal commands, or the eight labels for the requested atomic role. Response persistence precedes any acknowledgment. Qualified audio onset must match the calibrated scheduled estimate; missing or mismatched estimates cannot silently become audible-onset evidence.

`UnityProtectedState` accepts only the private control client's **test** mode. Every trial requires a new matching reset acknowledgment, current neutral rendering, fresh control health, focus and input readiness. A scene or mode failure interrupts the protected content. No target-bearing command is exposed by this state adapter.

`UnityAssessmentAudio` uses the sealed package. A novel message is composed only under the engine's one-use permit, after durable cue intent. Before that permit, only an existing atom is preloaded to establish device readiness. Speech follows the same deferred-read principle and additionally requires the reviewed bank, exact stored speech-list hash, study/set match and final-visit stage authorization. No-cue trials never invoke the audio port; their scheduled anchor and `AudibleStatus.NoCue` remain in the session journal.

`AssessmentStages` requires durable completion of the scheduled protected opportunities and a host-confirmed safe boundary before forms. The boundary predicate must be true only after the engine has finished its visual tail and paused or completed; it must never be a constant in a participant host. The final validity block requires completed forms and is permitted only at A D7 or B W4. B W1 is rejected. Form values are recorded individually before the display advances. An interrupted final completion write requires explicit recovery, not replay of earlier ratings.

Engineering wording and scale decision: A difficulty and usability use 1–7; B ownership, preference fit, influence and pleasantness use 1–7 and mental demand uses 0–10. Exact public engineering wording lives in `RatingPlan`. Non-DEMO admission requires its wording-review gate; no methodology approval is claimed. Forms have no audio dependency or correctness value.

`AssessmentJournal` stores private, ordered, hash-linked stage rows with a separately flushed tip and a mutually exclusive writer handle. A damaged, truncated or edited history fails closed and is preserved. It does not repair source bytes. Post-W4 help and execution records use only `post_w4_optional`, separate from protected response rows. The final data-template/export integration belongs to #72.

The module is a reusable integration component. It does not provision a participant, select a B vocabulary, authorize an audio route, sign off wording, or qualify recorded motion by itself. Native-build and test results are recorded separately from physical headset evidence.
