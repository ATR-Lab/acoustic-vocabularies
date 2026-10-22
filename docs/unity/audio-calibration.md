# Nonsemantic audio calibration host

The #64 calibration scene plays only the three hash-pinned, stored two-second
examples from the existing [reserved asset specification](../../sound/docs/nonlexical.md).
It records comfort responses and relative gain. It does not load a participant
package, create novel audio, or enable a trial. This is a development integration;
headset interaction, acoustic delivery, route latency and protocol-owner review
remain separate evidence.

`AcousticVocab.StudyAudio.Editor.AudioBuild.Configure` generates the ignored scene
`Assets/Generated.local.data/StudyAudio/Calibration.unity`. `BuildWindows` and
`BuildAndroid` call the foundation build with that explicit scene. The foundation's
ordinary scene allowlist stays unchanged; calibration has an exact additional
component allowlist, one host, one AudioSource and no study workcell. Build with
the pinned editor and already installed packages, using the foundation's
`EXPERIMENT_PROTOCOL_VERSION`, `EXPERIMENT_COMMIT_SHA`, `EXPERIMENT_BUILD_ID` and
optional `EXPERIMENT_DIRTY_SOURCE` environment fields. Use fresh build IDs.
Do not put private configuration or WAV files under Assets.

Provision a valid `station.local.json` using the foundation runbook. Beside it in
the player's private persistent directory, provision `audio-calibration.local.json`
from the [example](../../apparatus/examples/audio-calibration.example.json) and
[strict schema](../../apparatus/schemas/audio-calibration.schema.json). Replace the
example coded ID and visit ID, and copy the session's already stored profile order;
the host never randomizes it. The coded ID indexes a private comfortable-gain
history across visits; the visit ID distinguishes the current visit. Protect these
files and logs as participant data. The example is not a session allocation.

Generate the public reserved assets with the existing producer, without installing
anything or changing the renderer:

```text
python sound/tools/make_reserved_assets.py --check --wav-dir .local/reserved-audio
```

Use the repository's existing sound runtime environment. Copy only
`calibration-P1.wav`, `calibration-P2.wav` and `calibration-P3.wav` into that private
persistent directory. The loader verifies each canonical mono 48 kHz signed
16-bit WAV, its 96,000 samples, file hash and PCM hash against the fixed #14
registry. A private config cannot substitute other audio. Generated WAVs stay
ignored and outside Git. The host preloads all three into memory within the
configured budget; no sound is synthesized by Unity.

For each profile in the stored order, set a comfortable relative level while idle,
then choose **Listen twice**. The example is scheduled twice, with requested starts
four seconds apart (two seconds of digital audio followed by two seconds of
silence). The second request is scheduled only after the first software completion;
a missed scheduling deadline stops the sequence instead of shifting it. **Yes**
and **No** become available only after both full callback coverages and their
software intervals have completed. The next profile begins only after that answer.
The final screen asks the observer to continue with the operator; it grants no
study readiness. Gain changes are disabled during a pair. **Stop**, lost focus,
pause, tracking loss during playback, changed device format, missing callback,
log failure and file/hash errors stop the host. Restart only after the operator
resolves the reason; no automatic replay or resume occurs.

The world canvas is fixed relative to the configured observer reference. A tracked
right controller points at a button and its trigger selects it; no button starts
selected. Mouse clicks are available for engineering checks. A private
`engineering_desktop_preview: true` permits an untracked desktop calibration
preview when no XR device is active, and is rejected on Android. This flag is
logged explicitly and does not change the audio trial gate. Controller ergonomics,
text legibility and actual headset use still require operator testing.

The route is explicitly unmeasured for this host, even if station configuration
contains other fields. `ScheduleCalibration` writes null acoustic onset estimate,
uncertainty and route offset. Callback coverage and software completion do not
prove speaker or earphone delivery. The ordinary scheduler rejects this route for
trial scheduling; independent calibration evidence is still required by #80.

That evidence is produced offline from a physical capture by
`tools/onset_calibration.py`. Its record format is
[audio-onset-calibration.schema.json](../../apparatus/schemas/audio-onset-calibration.schema.json),
and the procedure is in the [O6.1.3 runbook](../spikes/O6.1.3-runbook.md). No
physical measurement has been made yet, and no record exists. Only synthetic
fixtures exist, and the schema keeps them provisional. This host does not read
the records.

Private `operator-logs/audio-calibration-*.jsonl` records the configuration hash,
route, preview status, stored order, example hashes, preload memory estimate,
relative gain, requests, scheduled DSP/monotonic times, callback coverage,
software completion, comfort answers and bounded faults. Each event is flushed to
disk before the dependent action proceeds. Gain history under `comfortable-gain`
is append-only with old/new values and visit/time fields; malformed or torn history
blocks restoration. Neither history is an SPL measurement. Preserve failed runs
and do not publish these private logs.

Automated configuration tests check stored order, exact producer pins and rejection
of arbitrary audio, unknown fields and invalid budgets. Package/composition tests
are separate from host interaction. Builds, callback smoke tests, human comfort
screening and acoustic measurements must each report their own observed result;
none substitutes for another.
