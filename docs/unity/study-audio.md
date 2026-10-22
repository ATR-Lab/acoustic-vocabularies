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
Study A's `book` record is closed to the four SHA-256 fields of
[`package-format.md`](../interfaces/package-format.md) section 2 (`frozen_head`,
`snapshot_sha256`, `renderer_hash`, `validator_hash`); a missing, extra or
non-hash field is refused. The loader checks their form and their coverage by
`package_sha256`; it cannot re-verify them against the store.

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
`AudioRouteCalibration.FromStationConfig` accepts the station's `audio.route` and
`route_offset_ms` only when they exactly match a separately provisioned #80 report
containing schema_version1, route, route_offset_ms, onset_uncertainty_ms and
measurement_sha256. The supplied measurement hash is a provenance declaration;
the operator must retain and review the actual measurement artifact. A numeric
station offset alone cannot supply missing uncertainty or evidence.

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
