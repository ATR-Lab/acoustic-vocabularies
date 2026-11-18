# Measurement procedures

Requirements that software tests cannot settle, because they need station hardware or
people, are verified by these procedures. Each names its gate, its evidence file and its
pass rule. Gates: `INSTALL` (station install and audio-route calibration), `G2` (live
Isaac go/no-go), `G3` (engineering pilot accepted), `PILOT` (participant pilots).
Internal testers take part only once the compliance office has confirmed that engineering
checks with lab staff are allowed before human-subjects approval.

Record every run in the station's apparatus manifest (`tools/apparatus_manifest.py`) with
the build commit, station ID and date. Keep raw captures in the private evidence store,
never in this repository.

### MEAS-REQ-APP-02

**Gate:** INSTALL. **Evidence:** apparatus manifest headset block.

1. For every station and the spare, read the headset model, OS build and runtime version
   from the device and record them.
2. Pass: one model and one OS build across all stations. Freeze automatic headset updates
   for the study period and record how.

### MEAS-REQ-AUD-04

**Gate:** INSTALL. **Evidence:** onset calibration record per station (#80, #192, #211).

1. Install the single output route chosen in ADR-005 on each station: fixed OS volume,
   fixed buffer, the same interface and headphone model.
2. Run the onset calibration (200 scheduled onsets with electrical loopback) and load the
   pinned record in the app (`docs/unity/audio-calibration.md`).
3. Pass: the app accepts the record and refuses playback when the route or device
   changes. Freeze the route; any later change starts a new apparatus version.

### MEAS-REQ-AUD-08

**Gate:** INSTALL. **Evidence:** onset calibration record and the independent sync capture.

1. On each station, capture 200 scheduled onsets at the ear position (measurement
   microphone and coupler) together with the independent common-clock trigger.
2. Compute the residual of acoustic onset minus estimated onset after the fixed offset.
3. Pass: 95th-percentile absolute residual at most 20 ms, including the sync bound.
   Otherwise repair the route or qualify response-time analyses before freezing.

### MEAS-REQ-AUD-09

**Gate:** INSTALL. **Evidence:** level table per station.

1. With the fixed station volume, play the calibration example at three in-app gains
   (low, middle, high of the comfort range) through each station's headphones on the
   coupler.
2. Pass: the level at each gain differs by at most 1 dB across stations; otherwise
   adjust the fixed station volume and repeat before any participant session.

### MEAS-REQ-ELIG-05

**Gate:** PILOT. **Evidence:** screening log of the pilot pairs.

1. In the pilot screening, time the comfort check of the three profiles at each person's
   chosen level and record the outcome per profile.
2. Pass: every screening fits its booking and every outcome is logged per person and per
   profile; report the share of pairs that are compatible.

### MEAS-REQ-NEUT-04

**Gate:** G3. **Evidence:** leakage verifier report (`tools/view_leakage.py`, #79, #288).

1. On the installed station, capture the participant view before the cue for all 32
   requests at the frozen observer reference and at the five head poses.
2. Run the verifier with zero pixel tolerance; inspect any difference by hand.
3. Pass: no request-dependent difference in view, object state, text or audio routing.

### MEAS-REQ-APP-07

**Gate:** G3. **Evidence:** legibility ladder sheet (ADR-004, #49, #74).

1. With the longest real labels on every panel position, step the text angle down a
   ladder; at least three testers read each label aloud.
2. Pass: the frozen text angle is at least 1.5 times the smallest angle every tester
   read without error.

### MEAS-REQ-APP-08

**Gate:** G3. **Evidence:** frame probe log (#207) from a full mock visit.

1. Run a full mock Study A visit and a mock Study B visit on station hardware with the
   frame probe recording every frame interval.
2. Pass: no presentation gap over 250 ms during any cue or response window; any longer
   gap is classified as a freeze fault by the app.

### MEAS-REQ-APP-09

**Gate:** G3. **Evidence:** input harness results (#49).

1. Three testers each enter all 32 commands with the controller ray.
2. Pass: no illegal or accidental activation; every Commit corresponds to an intended
   press.

### MEAS-REQ-APP-10

**Gate:** G2. **Evidence:** live-state soak and mock visit logs (#62, #63, #203).

1. Run a full mock visit with live Isaac state on the station topology.
2. Pass: state age stays within the freshness bound for every exposure; the robot holds
   the neutral pose throughout protected blocks; any stale state refuses the next
   exposure instead of playing it.

### MEAS-REQ-LES-15

**Gate:** G3. **Evidence:** headset capture of aligned lessons.

1. Record the headset view during two aligned lessons per family at the station frame
   rate together with the audio output.
2. Pass: each highlight starts and ends within one frame of its motif's onset and offset,
   and no highlight is visible in static lessons.

### MEAS-REQ-INT-10

**Gate:** G3. **Evidence:** fault-injection run on station hardware (#81, #212).

1. During a mock visit, unplug the headset link once mid-block and remove the controller
   once before a Commit.
2. Pass: the visit pauses before the next exposure, the last committed record is kept,
   no audible item is replayed as new, and the operator display never shows the answer.
