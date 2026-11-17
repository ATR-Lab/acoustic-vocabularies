# ADR-005 — Nonspatial audio route and onset timing

Status: Accepted at G1 (#51) — wired PC headphones through a USB audio interface,
DSP-scheduled; buffer and offset are calibrated per station before the pilot

## Context

WBS O5.1.8 / #50 and #48. Response time is Commit on the host monotonic clock
minus actual or calibrated estimated audible onset in that clock. Simulation
time, DSP time alone and a successful play call are not audible onset.

## Options

Standalone headset speakers; standalone wired 3.5 mm earphones; or PC headphones
with Link. Bluetooth is not a candidate. Compare plain playback with DSP-scheduled
playback, at least on the best route, with the same preloaded 48 kHz mono test cue.

## Measurements

The [audio analysis](../../spikes/O5.1.6/audio_onsets.py),
[runbook](../spikes/O5.1.6-runbook.md) and
[results template](../../apparatus/spikes/O5.1.6/results-template.md), merged in
[PR98](https://github.com/ATR-Lab/acoustic-vocabularies/pull/98), provide
capture analysis and empty templates. No physical route has 200 measured onsets
yet. Mean offset, residual SD, p95 absolute residual, maximum, underruns,
capture kind, rate/buffer/volume and independent sync uncertainty are all Pending.
Electrical loopback and acoustic measurements are separate conditions.

## Decision

Use **one route for every station: wired, closed-back headphones driven by a
dedicated USB audio interface on the station's Windows render PC** (ADR-001).
Audio is 48 kHz mono PCM, preloaded and nonspatial (`spatialBlend = 0`, no
spatializer), played with **DSP-scheduled** `PlayScheduled` as the app already
does ([study audio](../unity/study-audio.md)). Headset speakers, Link audio to
the headset and Bluetooth are not used. The OS output volume is fixed per station
and recorded; each participant's comfortable level is applied only as the app's
logged relative gain. The DSP buffer is the smallest setting with zero underruns
over the station's calibration run, recorded as `buffer_samples`.

Reasons, without a physical onset measurement yet:

1. The study procedure needs one calibrated nonspatial route fixed before
   piloting. The app already refuses an uncalibrated route and anchors response
   time to a calibrated scheduled onset.
2. Request, Commit and electrical loopback all live on the render PC's monotonic
   clock, so the offset needs no cross-device clock mapping. Acoustic capture at
   an ear coupler adds a fixed transducer delay on top.
3. Headset speakers on Link carry audio through the Link stream (extra, variable
   latency) and leak sound to neighbouring stations. Closed-back headphones keep
   the level controlled and the room quieter for four to six stations.

The p95 residual onset uncertainty target is <=20 ms; no station is recorded as
passing yet.

Estimation formula after calibration: `estimated_onset_mono_ms =
request_mono_ms + measured_route_mode_offset_ms`. Include any scheduling lead
in that offset consistently. Compute `response_ms = commit_mono_ms -
estimated_onset_mono_ms`; preserve raw timestamps and calibration ID.

A photodiode measures photon arrival, not the host command time. Independently
calibrate command-to-photon uncertainty or use a common-clock instrument. Fit
clock drift with held-out checks, never audio onsets as sync anchors. Include
instrument/clock uncertainty and investigate detector bias before claiming the
20 ms target. A route without synchronization evidence stays unqualified.

## Acceptance conditions

Measured on each installed station (#80, `docs/spikes/O5.1.6-runbook.md`) before
the engineering pilot:

- 200 scheduled onsets with electrical loopback and acoustic capture, independent
  synchronization evidence, zero underruns and zero mismatched pulses.
- p95 residual onset uncertainty <=20 ms including the sync bound, recorded as the
  station's `route_offset_ms` and calibration report.
- Station OS volume, interface settings and buffer recorded and rechecked after a
  reboot.

## Consequences

#64/#80 use this route, scheduled play mode and 48 kHz; #80 calibrates each
station. Store gain and actual device volume in private station config and session
logs. Never silently change route or normalize device volume between calibrated
runs. If uncertainty exceeds the target, repair timing or qualify RT claims.

## Manifest fields

`audio_route`, `play_mode`, `sample_rate_hz`, `dsp_buffer_frames`, `buffer_count`,
`gain_config_hash`, `onset_offset_ms`, `residual_p95_ms`, `sync_bound_ms`, calibration hash.

## Revisit trigger

Audio device/driver/route/buffer/runtime change, underruns, measured drift, gain
path changes affecting onset detection, or failure of the <=20 ms criterion.
