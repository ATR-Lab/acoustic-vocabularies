# ADR-005 — Nonspatial audio route and onset timing

Status: Proposed — route, play mode and buffer pending physical measurement

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

Use 48 kHz, nonspatial, preloaded audio in the comparison. Keep route, buffer and
plain/scheduled choice unresolved. The proposed p95 residual onset uncertainty
target is <=20 ms; no candidate is presently recorded as passing.

Estimation formula after calibration: `estimated_onset_mono_ms =
request_mono_ms + measured_route_mode_offset_ms`. Include any scheduling lead
in that offset consistently. Compute `response_ms = commit_mono_ms -
estimated_onset_mono_ms`; preserve raw timestamps and calibration ID.

A photodiode measures photon arrival, not the host command time. Independently
calibrate command-to-photon uncertainty or use a common-clock instrument. Fit
clock drift with held-out checks, never audio onsets as sync anchors. Include
instrument/clock uncertainty and investigate detector bias before claiming the
20 ms target. A route without synchronization evidence stays unqualified.

## Consequences

#64/#80 receive route, mode, sample rate, buffer and calibration procedure after
G1. Store gain and actual device volume in private station config and session
logs. Never silently change route or normalize device volume between calibrated
runs. If uncertainty exceeds the target, repair timing or qualify RT claims.

## Manifest fields

`audio_route`, `play_mode`, `sample_rate_hz`, `dsp_buffer_frames`, `buffer_count`,
`gain_config_hash`, `onset_offset_ms`, `residual_p95_ms`, `sync_bound_ms`, calibration hash.

## Revisit trigger

Audio device/driver/route/buffer/runtime change, underruns, measured drift, gain
path changes affecting onset detection, or failure of the <=20 ms criterion.
