# Hard cap on the comfortable level

Each person chooses a comfortable level below a hard cap of **80 dB(A) peak at the
ear** (decision I7). The app enforces the cap from a per-station coupler measurement
taken at station install (see the station install runbook).

## Station measurement

The station configuration carries an optional `audio.level_calibration` object.
The schema treats it as optional, but the app refuses to play study sound without
it.

```json
"level_calibration": {
  "route": "<same value as audio.route>",
  "ear_peak_dba_at_0dbfs": 104.5,
  "measured_on": "YYYY-MM-DD",
  "coupler_id": "<coupler and meter identifier>"
}
```

- `ear_peak_dba_at_0dbfs`: the dB(A) peak at the ear, measured with the coupler,
  for a 0 dBFS peak played at gain 0 dB (linear gain 1) with the station's fixed
  OS and interface volume. Accepted range: 40 to 140.
- `route` must equal `audio.route`. A measurement taken on another route does not
  cap this one, so changing the route requires a new measurement.
- `measured_on` and `coupler_id` record provenance.

`tools/validate_station.py` refuses a provisioned station without this object
(`LEVEL_CAP_OFFSET_MISSING`), with a different route (`LEVEL_CAP_ROUTE_MISMATCH`)
or with an impossible date (`LEVEL_CAP_OFFSET_INVALID`).

## The cap

`LevelCap` (`unity/Assets/ExperimentApp/Runtime/StudyAudio/LevelCap.cs`):

```
max gain (dB) = 80 - ear_peak_dba_at_0dbfs - loudest stimulus peak (dBFS)
```

- 80 is the constant `LevelCap.CapDbaPeakAtEar`.
- Packages do not record a per-file peak, so the loudest stimulus is assumed to
  reach 0 dBFS (`LevelCap.LoudestStimulusPeakDbfs`). This gives the lowest gain
  limit. Real study sounds peak below full scale, so the actual level at the cap
  is lower than 80 dB(A).
- The app's gain is linear. `MaxGain` is the largest float gain whose level in dB
  does not exceed the limit, and never more than 1. A gain exactly at `MaxGain`
  is allowed. Any larger gain is refused, even one float step above.

## Where it is enforced

| Place | Behaviour |
| --- | --- |
| Calibration scene start | No measurement: `LEVEL_CAP_OFFSET_MISSING`. A malformed one: `LEVEL_CAP_OFFSET_INVALID`. Wrong route: `LEVEL_CAP_ROUTE_MISMATCH`. The scene fails before any sound. |
| Calibration scene, first level | With no recorded history, the scene starts at the initial gain or at the cap, whichever is lower. |
| Calibration scene, recorded or carried level | A recorded, carried or held gain above the cap is refused with `GAIN_ABOVE_CAP`. A screening level above the cap is refused before any carried row is written. |
| Level control | Louder is clamped at the cap and the button is disabled there. The step floor never goes above a lower cap. |
| Visit start (every role that plays study sound: Study A learners and both Study B roles) | `JoinedVisitArtifacts` reads the station measurement before anything else that can play sound. Missing, malformed or wrong-route measurements refuse the visit with the codes above. A held level above the cap is refused with `GAIN_ABOVE_CAP`. |

A level above the cap is refused, never lowered. Lowering it silently would change
the person's held level between visits. If a held level is above the cap at a
station, the visit does not start there. The operator decides what happens next.

The calibration log's `calibration_preloaded` row records `level_cap_max_gain`,
`level_cap_dba_peak_at_ear`, `ear_peak_dba_at_0dbfs` and
`level_calibration_measured_on`.

Simulation-test runs (`SimulationTestAuthority`) play no sound to a person and are
not capped.
