# Private validity speech

Issue #71 supplies a deterministic preparation path and a hash-verified Unity
reader. The measured bank is **DEMO, engineering-unreviewed**. No human listening
review, final apparatus freeze or eligibility to play in a study is claimed.
Generated WAVs, selections, manifests and review records remain private.

The voice decision is the already installed Microsoft Zira Desktop, en-US,
voice ID `TTS_MS_EN-US_ZIRA_11.0`, voice version 11.0, rate 0 and synthesis volume
100. The actual engine DLL, voice resources, System.Speech assembly and OS version
are hashed in the private synthesis evidence. No cloud service, new installation
or human voice is used. These pins document a build; the reviewed output hashes
are the playback authority if a later OS/voice revision changes synthesis.

Wording is the panel's exact action words with underscores replaced by spaces,
then the one-letter target. The target is explicitly spelled using the speech
API so A is not treated as an article. No object description, adjective or extra
hint is added. Public panel labels and the task's canonical legal families are
checked in tests. All 32 legal commands are synthesized twice; the two passes
produced byte-identical pairs. They are repeat renders, not independent human takes.

The stored #30 speech list already chooses the balanced eight commands for each
study/set. The preparer requires that list and its expected SHA-256, keeps its
bytes as `selection.local.json`, and chooses take 1 for those exact commands.
It never draws a new permutation or substitutes a diagonal allocation. Both
families, all eight actions/targets and the schedule IDs are validated. The
DEMO marker propagates into the bank and blocks study playback. The initial
private diagonal-selection diagnostic is retained but superseded; it is not
the schedule-bound bank or a freeze.

Raw synthesis is mono signed 16-bit PCM at 48 kHz. The fixed trim keeps the first
through last sample whose absolute value exceeds 33, with up to 480 samples of
padding at each end. Integer scaling sets the peak to 23,170 with nearest rounding
and ties away from zero. This is a digital level rule, not an acoustic calibration
or loudness match. Durations come from the resulting sample counts. Canonical
44-byte PCM headers, original/processed file hashes and PCM hashes are retained.
The selected route and comfortable gain still come from #64/#80.

`SpeechBank.InspectEngineering` verifies every file but returns no playable PCM.
`LoadReviewed` additionally requires independently supplied manifest, listening
review and stored speech-list hashes. It checks the review's binding to all 64
original files, wording/clarity flags, coded reviewer and timestamp. It cannot
establish that a person actually listened; that is an operator responsibility.
Changing the manifest, selection, review or waveform after load blocks access.
Extra files, links, illegal commands and inconsistent durations are rejected.

`ReadForValidity(scheduleSpeechId, authorization)` exposes only a selected take
from a reviewed, non-DEMO bank. It consumes an `ISpeechSlotAuthorization` bound to
the schedule ID, manifest hash and speech-list hash. The #69 content module must
issue that one-use authority only after the final delayed protected battery and
forms, and must still satisfy the session engine's audio/reset/readiness gates.
The reader does not create that authority. Final stage wiring and a mock W4
recording remain part of #69; an interface alone is not that evidence.

The [runbook](../spikes/O5.5.5-runbook.md) contains generation, listening review and
freeze commands. Sanitized measured results are in `validity-speech-validation.json`.
No speech bank is packaged inside a public app build or uploaded to CI.
