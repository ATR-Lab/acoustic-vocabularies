# Verified audio subsystem

Issue #64 provides a package/audio boundary for the future session engine. It is
development code; private methodology, ADR-005, device memory, acoustic onset,
headphone comfort and G3 qualification remain open.

`PackageLoader.Load` verifies the canonical producer manifest hash, exact file
inventory and every file/PCM hash. It rejects links, traversal, extra files,
noncanonical JSON, malformed hidden records and noncanonical WAV headers. PCM is
mono, signed 16-bit, 48 kHz. The loader validates both Study A's 32 combinations
and Study B's 1,536 profile/rank combinations without returning answer manifests,
allocations or schedules through its public audio API. DEMO loading requires an
explicit engineering opt-in. A post-load read rechecks the manifest/file tree.

`MessageComposer` concatenates verified action PCM, 9,600 zero samples and verified
referent PCM. Stored trained phrases reproduce exactly; novel buffers are available
only through the package's single-use `INovelSlotAuthorization` boundary. The
session engine must own that authority and consume the exposure safely. Composed
values retain both atom hashes, and playback events carry them with the composite
hash. No novel WAV is written. Public atom access is not a security boundary against
a malicious application; package distribution and participant access stay private.

`AudioPlayer.Configure` requires an event sink and an exposure-gate function.
`Preload` verifies immutable PCM, creates resident clips and checks a declared
memory budget plus reported Unity allocation. These are software checks; device
memory acceptance needs measurement. `Ready` also requires an active component;
`TrialReady` additionally requires a supplied calibrated route. The session engine
must independently require hashes, reset acknowledgement, current rendered neutral,
input/focus and its other readiness conditions.

`Schedule` maps a requested monotonic onset to DSP time, subtracting the supplied
route offset from the scheduled output time. The sampled mapping includes a full
DSP quantum and bracket width in its uncertainty, rejects stale/regressing clocks
and late requests, and records the request before `PlayScheduled`. All public times
in this API are seconds; journal fields explicitly convert to milliseconds.
The route offset and onset uncertainty come only from a pinned #80 record; see
[Onset calibration record](#onset-calibration-record-80).

The output is 2D with no panning, Doppler, spatializer, mixer group or effect
components. `OnAudioFilterRead` observes the unmodified output callback sequence
on the audio thread; it performs no logging, allocation, file I/O or locks.
Missing callbacks, noncontiguous/overlapping buffers and format changes are
distinct faults. Counts establish software processing coverage, not earphone
delivery. The first callback DSP timestamp is separate from the main-thread event
observation time. Completion also waits through the scheduled/estimated interval.
Focus loss, pause, component disable, changed clip/path or a failed evidence sink
stop playback and latch failure; enabling a component does not resume it.

`ScheduleCalibration` is an explicit non-study path: an unmeasured route produces
null acoustic onset, uncertainty and offset. It never grants trial readiness.
The [calibration host](audio-calibration.md) plays only the three pinned reserved
examples, saves relative gain under a coded ID, and enforces two complete plays
with the requested two-second gap before each profile's comfort answer. No gain
change depends on accuracy. A malformed or torn gain history blocks restoration.

The [runbook](../spikes/O5.4.1-runbook.md) separates local software, native UI and
operator/device evidence. Local retained results are indexed in
`study-audio-validation.json`. Native screenshot/button interactions and acoustic
measurements are not inferred from unit tests or silent callback coverage.

## Onset calibration record (#80)

`AudioRouteCalibration.FromStationConfig(station, recordBytes, runtime)` loads the
full record written by `tools/onset_calibration.py`
([schema](../../apparatus/schemas/audio-onset-calibration.schema.json),
[runbook](../spikes/O6.1.3-runbook.md)). A numeric station offset alone never
supplies uncertainty, evidence or qualification. No physical record exists yet. The
EditMode and PlayMode tests use "qualified" variants of the synthetic example; these
variants are test fixtures only and qualify no route.

Station configuration declares the binding. These `audio` fields are optional in
the [station schema](../../apparatus/schemas/station.schema.json), but the loader
needs all three:

- `onset_calibration_record_sha256`: SHA-256 of the record file;
- `connection_mode`: `headset_speakers`, `wired_3_5mm_earphones` or
  `link_pc_headphones`;
- `output_device`: the same text as the record's `output_device`.

`audio.route`, `audio.buffer_samples` and `audio.route_offset_ms` must also equal
the record. In the joined bootstrap the record is the pinned `audio_calibration`
file, so it is checked against both the joined-config pin and the station pin.

The loader has three outcomes.

1. **Refused (throws `AudioFault`).** Any of these stops loading:
   - `AUDIO_CALIBRATION_PIN_MISSING` or `AUDIO_CALIBRATION_PIN_MISMATCH`: there is no
     station pin, or the file's SHA-256 differs from it;
   - `AUDIO_CALIBRATION_REPORT_RETIRED`: the file is the old five-key report;
   - `AUDIO_CALIBRATION_RECORD_INVALID`: the record fails strict parsing.

   Strict parsing (`AudioOnsetCalibrationRecord.Parse`) is equivalent to the schema.
   It checks exact keys, types, ranges, enums, constants, hashes, timestamps and the
   schema's `allOf` rules, and it refuses non-strict JSON. It is stricter in two ways:
   integer fields must be JSON integers, and `1.0` is refused. A test keeps its key sets
   equal to the schema.
2. **Loaded but not qualified (an unmeasured route).** This happens for a
   `provisional` record, which includes every synthetic fixture. The loader logs the
   warning `AUDIO_CALIBRATION_NOT_QUALIFIED`. It also happens for a qualified record
   that does not match the running setup: station, route, connection mode, output
   device, sample rate, DSP buffer frames or count, `buffer_samples` or the station
   offset. The loader then logs `AUDIO_CALIBRATION_FAULT <code>`, for example
   `AUDIO_CALIBRATION_DEVICE_MISMATCH`. `NotQualifiedCode` holds the reason.

   The route stays unmeasured, so the existing delivery-evidence rules still apply:
   - trial `Schedule` is refused with `AUDIO_ROUTE_UNCALIBRATED`;
   - `ScheduleCalibration` writes null `audio_onset_estimate_mono_ms`,
     `onset_uncertainty_ms` and `route_offset_ms`;
   - `JoinedVisitArtifacts` blocks startup with `JOIN_<code>`, for example
     `JOIN_AUDIO_CALIBRATION_NOT_QUALIFIED`.
3. **Qualified.** The record is `qualified`, and the schema then requires that it is a
   reviewed, physical, acoustic-coupler, full-scene, scheduled-play measurement with a
   met target or a signed response-time qualification. Every binding field must also
   match.
   - The route takes `route_offset_ms` and `onset_uncertainty_ms` from the record, and
     its evidence hash is the record's SHA-256.
   - `Schedule` starts output at the requested onset minus the offset, which is the
     logged `scheduled_onset_mono_ms`. It logs `audio_onset_estimate_mono_ms` as
     `scheduled_onset_mono_ms` + `route_offset_ms`, the reference fixed by the #80
     decision.
   - `onset_uncertainty_ms` is the record's value plus the per-play DSP mapping bound.
   - The route is bound to the record's sample rate and DSP buffer. `AudioPlayer.Configure`
     refuses it on any other device format with `AUDIO_CALIBRATION_RUNTIME_MISMATCH`.

Unity cannot observe the transducer, so `connection_mode` and `output_device` are
operator declarations in station configuration. Sample rate and DSP buffer are read
from the audio device (`AudioRuntimeRoute.Observe`). The volume step and app build in
the record are not checked at runtime; the operator must rerun the measurement when
either changes.

The five-key report (`schema_version`, `route`, `route_offset_ms`,
`onset_uncertainty_ms`, `measurement_sha256`) is **retired, not kept**. It could
declare a route calibrated with no review, qualification status or settings, so it is
refused explicitly instead of being read as a fallback.
