# Station install and audio-route calibration (Link stations)

Status: **procedure only**. No station hardware has been delivered, and nothing in
this document has been run. Every value marked **TBD-at-install** is measured or
chosen at install and recorded in the private station record. This document
supplies no numbers beyond those already in the ADRs and the linked procedures.

Issues: #296 (O5.3.2, Link-mode station provisioning), the install half of #88
(O1.3.8, install and calibrate all stations) and of #80 (O6.1.3, onset
calibration on the chosen route). Pass rules for the requirement IDs below are in
[the measurement procedures](../requirements/measurements.md).

A Link station is one Windows render PC with a discrete GPU, one headset and its
controllers on a USB Link cable, one dedicated USB audio interface, wired
closed-back headphones and an operator monitor. It also has an Isaac host
allocation and a wired network connection to that host (ADR-001, ADR-003,
ADR-005). The audio measurement kit is shared between stations.

Out of scope: Isaac host installation ([station isolation](../isaac/stations.md),
ADR-003), RA certification (O6.1.8) and participant procedures.

## 1. Prerequisites

| Item | State on 17 Nov | Needed for |
|---|---|---|
| ADR-001 wired USB Link, ADR-004 controller ray, ADR-005 PC headphone route accepted (#268, stacked on #194) | open PR | everything below |
| Onset calibration analysis, record schema and [O6.1.3 runbook](../spikes/O6.1.3-runbook.md) (#192) | open PR | §5.5 |
| Unity loads a pinned record; station schema gains `audio.connection_mode`, `audio.output_device`, `audio.onset_calibration_record_sha256` (#211, stacked on #192) | open PR | §5.6 |
| Panel-loss injection in the frame probe (#207) | open PR | §8 row 14 |
| Operator-machine checklist, `docs/operator-console/station-setup.md` (#195) | open PR | §3.4, §3.7 |
| Hidden-tuple reader inventory (#259) and snapshot-mode leakage run (#288) | open PR / open issue | §8 row 17 |
| G2 live-or-snapshot decision (#63) | unsigned | §7 |
| Hardware: headsets for every station plus a spare (O1.3.2), Isaac host GPUs (O1.3.3), audio measurement kit (O1.3.4), station network (O1.3.5), booths (O1.3.7), one render PC per station (ADR-003) | not delivered | all physical steps |
| Independent common-clock sync instrument for onset capture ([O5.1.6](../spikes/O5.1.6-runbook.md): no hardware timestamp adapter exists in this repository) | not arranged | §5.5 |
| Compliance confirmation that engineering checks with lab staff may run before human-subjects approval ([measurement procedures](../requirements/measurements.md)) | pending | tester checks in §6 and §8 |
| Accepted session build: reviewed protocol version, clean source, non-development | not yet built | §3.5 |

**Tooling gaps.** These steps are manual until the following exist (#296):

- **No Link inventory or pre-session check tool.** `tools/quest_station.py inventory`
  reads an installed APK and its Android build manifest. On a Link headset the
  experiment app is not installed, so the command fails.
- **No Windows install script.** `tools/deploy-quest.ps1` installs an APK and
  validates the station file with `--topology standalone_quest`. For sessions,
  the Link path below replaces it. Its APK steps are engineering-only.
- **No full-scene onset driver.** No driver yet plays 200 stored stimuli in the
  full scene and exports the O6.1.3 events CSV.
- **The player does not apply `audio.buffer_samples`.** See §5.4.

General rules (from the [Quest provisioning runbook](O5.3.2-runbook.md) and #195):

- Record the exact UI path and the value you saw on that OS or software build.
  A blank means not observed.
- Use only supported settings screens. Network, firewall, Group Policy and
  registry changes belong to the network or IT owner.
- Keep serials, account names, IP addresses, host names and participant data
  out of public records. Use logical station and unit IDs.
- Preserve failed runs and raw observations. Tests, simulator runs and the
  public examples never qualify a station.

## 2. Per-station record

Store the record privately as `*.local.*` files, as described in the
[stations README](../../apparatus/stations/README.md). Fields that have an
[apparatus manifest](../apparatus-manifest.md) field go there too, through
`tools/apparatus_manifest.py collect`. Fields marked "private record" have no
manifest field yet.

| Group | Field | Read from | Recorded in |
|---|---|---|---|
| Station | station ID, role (`station` or `spare`), booth | operator | station config `station_id`; manifest `station_id` |
| Render PC | logical PC ID, CPU class, RAM | Windows system information | private record |
| | OS edition, version and build | read-only `Get-CimInstance Win32_OperatingSystem` (`Caption`, `Version`, `BuildNumber`) | private record; manifest `host_os` (see note) |
| | GPU model, VRAM, driver version | model and driver from read-only `Get-CimInstance Win32_VideoController`; VRAM from Task Manager or the driver's utility | manifest `gpu_vram_driver` |
| | USB ports used for the Link cable and the audio interface, and whether either goes through a hub | physical inspection | private record |
| | operator monitor, display mode | Display settings (#195 W1) | private record |
| Link | Link PC software version, update channel, update-deferral method | the Link software's settings | private record |
| | active OpenXR runtime | registry, read-only (§3.3) | private record |
| | offered and chosen refresh rate, render resolution, other graphics settings | the Link software's graphics settings | station `refresh_hz`; manifest `display_resolution_refresh`; private record |
| | Link cable logical ID | label | private record |
| Headset | unit ID, model, OS build, fingerprint, security patch | device properties (§4.1) | manifest `headset_units`; station `headset_unit_id`; private record |
| | controller pair logical ID and firmware, where shown | headset settings | private record |
| | developer mode, update deferral, notifications, boundary, sleep, Air Link off | headset settings (§4.2) | private record |
| Audio | interface logical ID, model, firmware and driver version | interface and driver panel | manifest `headphone_audio_device`; private record |
| | Windows playback endpoint name, exactly as shown | Sound settings | station `audio.output_device` (#211); onset settings `output_device` |
| | default format, enhancements, spatial sound, exclusive-mode options, other playback endpoints | Sound control panel | private record |
| | OS volume step; hardware output control position, if any | Sound settings; physical | private record; onset settings `volume` |
| | DSP buffer frames and count | app log; onset record | station `audio.buffer_samples`; manifest `audio_buffer` |
| | headphones logical ID and model | label | private record |
| | loopback wiring, capture interface, microphone and coupler IDs | physical | onset settings `capture_interface`; private record |
| | level-cap measurement: dB(A) peak at the ear for a 0 dBFS peak at gain 0 dB, date, coupler ID | coupler (§5.3) | station `audio.level_calibration` (#348); private record |
| Network | adapter, segment | network owner | manifest `station_network_settings` |
| | fixed address, Isaac endpoint | network owner | station `isaac_endpoint` (private); private record |
| | Isaac host allocation (logical) | ADR-003 plan | private record |
| Software | build ID, commit, `experiment.exe` and build-manifest SHA-256 | §3.5 | manifest `app_build`, `app_build_source_commit`; deployment record |
| | station config SHA-256, onset record SHA-256 | §3.6, §5.6 | deployment record |
| | operator console version, tools checkout commit, Python version | §3.7 | manifest `operator_console_version`; private record |

Note: the manifest has one `host_os` field, but a Link station has two hosts:
the render PC and the Isaac host. It has no field for the Link software version
or the active OpenXR runtime. Record all of these privately until #83 decides
where they go.

## 3. Windows render PC

### 3.1 Operating system

1. Record the Windows edition, version and build (§2). The Link software lists
   its supported Windows versions in its [published requirements](https://www.meta.com/help/quest/articles/headsets-and-accessories/oculus-link/requirements-quest-link/),
   already cited in the [OpenXR operator run](../spikes/O5.1.3-runbook.md).
   Use the same build on every station.
2. Use a local lab account that does not sync to a personal account. Keep the
   account name out of records.

### 3.2 GPU and driver

1. Check the GPU against the Link software's current requirements table and
   record model, VRAM and driver version.
2. Turn off automatic GPU driver updates in the driver's own utility and in
   Windows Update (#195 W8). Record how each is held.

### 3.3 Link software and OpenXR runtime

1. Install the Link PC software if it is absent; installation needs approval
   ([O5.1.3](../spikes/O5.1.3-runbook.md)). Record its version.
2. Select it as the active OpenXR runtime, then confirm with a read-only query:

   ```powershell
   Get-ItemProperty 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -Name ActiveRuntime
   ```

   Record the value privately. Unity's [Link setup](https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.6/manual/get-started/link.html)
   describes the runtime selection.
3. Pin the graphics settings: refresh rate (§4.4), render resolution and any
   encode or bitrate setting. Record every value shown on that page
   (**TBD-at-install**). Use the same values on every station.
4. Stay on the release update channel; turn off any beta or test channel. Defer
   automatic Link software updates by whatever method the installed version
   supports, and record the method. If the software cannot defer updates, say
   so; do not claim a frozen version. Add a manual check to §9.
5. Turn off Air Link on the PC side, if the software offers the switch (ADR-001:
   Air Link is not used).

### 3.4 Notifications, updates and power

1. Apply and record #195 rows W5 to W11: notifications, notification sounds, OS
   updates, app and driver updates, sleep, screen lock and screen saver.
2. Power: the PC, its display and its USB ports must not suspend during a
   booking. Record the power plan, sleep and display timers and the USB
   selective suspend setting. Change them only through the supported settings
   screens.
3. Bluetooth: turn it off, or confirm no Bluetooth audio device is paired
   (ADR-005: Bluetooth is not used). Record which.

### 3.5 Install the build and verify its hash

1. Build the accepted session scene with `tools/build-unity.ps1 -Target Windows`
   using a fresh build ID. The output is `unity/Builds/<BuildId>/Windows/`, with
   `experiment.exe` and `experiment.exe.build.json`. The build manifest lists
   every player file with its size and SHA-256. `build-unity.ps1` already
   checks those files after the build.
2. Copy the whole directory to a fixed install location on the render PC.
   Verify it there before first launch:

   ```powershell
   $Build = (Resolve-Path -LiteralPath $InstallDirectory).Path
   $Manifest = Get-Content -Raw -LiteralPath (Join-Path $Build 'experiment.exe.build.json') | ConvertFrom-Json
   if ($Manifest.result -ne 'Succeeded' -or $Manifest.errors -ne 0 -or $Manifest.development_build -or
       $Manifest.build_identity.dirty_source -or $Manifest.build_identity.target -ne 'StandaloneWindows64') {
       throw 'Build record is not a clean, non-development Windows build.' }
   foreach ($Entry in $Manifest.files) {
       $File = Join-Path $Build $Entry.path
       if ((Get-Item -LiteralPath $File).Length -ne $Entry.bytes -or
           (Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLowerInvariant() -ne $Entry.sha256) {
           throw "File does not match the build manifest: $($Entry.path)" } }
   Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Build 'experiment.exe.build.json')
   ```

   Also confirm that the directory holds no files the manifest does not list,
   apart from the manifest itself. Record any you find.
3. Record build ID, commit, protocol version, the `experiment.exe` SHA-256 and
   the build-manifest SHA-256 in a private deployment record under `.local/`.
   For the apparatus manifest, pin `experiment.exe.build.json` as the `app_build`
   artifact. It hashes every player file, so step 2 ties the installed bytes to
   that pin.

### 3.6 Station configuration

The Windows player reads its private files from its persistent data directory:
`$env:USERPROFILE\AppData\LocalLow\AcousticVocab\Acoustic Vocabulary Experiment\`
(company and product names from `unity/ProjectSettings/ProjectSettings.asset`).
It writes `operator-logs/` there.

1. Write `station.local.json` from the [example](../../apparatus/examples/station.example.json)
   and the [schema](../../apparatus/schemas/station.schema.json). Set
   `topology: pcvr_link`, `input_method: controllers`, `tracking_origin_mode:
   device` and `provisioning_status: provisioned`. Until §5.6, set
   `audio.route` to the logical route ID used on every station,
   `audio.buffer_samples` to the build's DSP buffer and `audio.route_offset_ms`
   to null, and leave out the #211 binding fields. The player refuses any other
   topology with `build_topology_mismatch`.
2. Validate it against the installed build's protocol version:

   ```powershell
   python tools/validate_station.py $StationConfig --protocol $ProtocolVersion --topology pcvr_link
   ```

   With #348, the validator refuses a provisioned file without the
   level-cap measurement (`LEVEL_CAP_OFFSET_MISSING`), and the app plays no
   study sound without it. Add it in §5.3, then validate again.

3. Put `state-source.local.json` and `response-panel.local.json` in the same
   directory, following the [state sources](../unity/state-sources.md) and
   [response panel](../response-panel/README.md) procedures.
4. Record the SHA-256 of each file. Never overwrite an earlier file without
   keeping it.

### 3.7 Operator console on the same PC

The console's mailbox transport is same-PC Windows only. Other platforms fail
closed with `mailbox_platform_unqualified`
([operator console interface](../interfaces/operator-console.md)).

1. Start the console as described in the [console runbook](../spikes/O5.6.2-runbook.md),
   with the private `--config`, `--audit` and `--protocol` values.
2. Apply #195 sections 1 to 4: an extended operator display the participant
   cannot see, no casting, the clock and time zone, and a single console. The
   Windows desktop and the console must never appear inside the headset (#195 H5).
3. Check the logging and upload path (#88): one engineering visit writes the
   audit and run sheet, and the copy reaches the approved store.

### 3.8 Reboot recheck

After §3 to §5 are complete, reboot the PC and the headset. Re-read every value
in §2 that a reboot can change: OS build, drivers, Link version and runtime,
refresh rate, default playback endpoint, default format, enhancements, spatial
sound, OS volume step, power settings. Record both readings. Any difference
fails the station until it is explained and fixed (#296, ADR-005).

## 4. Headset on Link

### 4.1 Identity and OS build

Read the same properties that `quest_station.py` reads, with an explicitly
selected device. Keep the serial in a private shell variable and the output in
a private file:

```powershell
foreach ($Property in 'ro.product.model','ro.build.version.incremental','ro.build.fingerprint','ro.build.version.security_patch') {
    & $Adb -s $DeviceSerial shell getprop $Property }
```

Record unit ID, model and OS build for every station unit and the spare.
REQ-APP-02 passes when all units share one model and one OS build.

### 4.2 Per-unit settings that still apply

Follow steps 1 to 4 and 6 of "Per-unit physical configuration" in the
[Quest provisioning runbook](O5.3.2-runbook.md): developer mode, OS update
deferral, notifications, stationary boundary and sleep. Label the unit as in
its step 7. Its step 5 (audio on the headset) and the APK install in step 7 do
not apply to Link sessions.

### 4.3 Link connection

1. Connect the headset to the recorded USB port with the station's Link cable.
   Accept the Link prompt on the headset and record the UI path.
2. Turn off Air Link on the headset (ADR-001).
3. If the Link software offers a connection or USB test, run it and record the
   result (**TBD-at-install**).
4. Launch the player from the PC. Confirm that the seated scene appears in the
   headset and that the private log has `configuration_validated` with
   `topology: pcvr_link`.

### 4.4 Refresh rate

1. Record the rates the Link software offers. The station schema allows 72, 80,
   90 and 120 Hz.
2. Choose the rate with the Link section of the
   [OpenXR operator run](../spikes/O5.1.3-runbook.md) on the real joined scene.
   The ADR-001 acceptance conditions apply: a 900-second capture and no
   presentation freeze over 250 ms in cue or response windows. The chosen rate
   is **TBD-at-install**.
3. Set it in the Link software, set the same value as station `refresh_hz`, and
   use frame setup `require_current`, because the app does not request the rate
   over Link ([frame timing runbook](../spikes/O5.6.3-runbook.md), step 2).
4. Use one rate on every station. An apparatus version freezes its rates
   ([versioning](../versioning.md)).

### 4.5 Kiosk-equivalent restrictions

Link has no app-level kiosk mode. The proposed engineering approach is the one
in the Quest provisioning runbook: manual launch with focus-loss detection.

1. The operator launches the player from the PC. The participant never opens
   headset menus, the headset library or the PC desktop view (#195 H5).
2. Casting, screen recording and streaming stay off (#195 H1, H2).
3. Press the headset's system menu during an engineering run, then return.
   Confirm `application_focus_lost` or `application_paused`, `restore_pending:
   true` and a neutral view. Restore only through the operator at a safe
   boundary; a silent resume fails the check.
4. Remove the headset, let it sleep, and pull the Link cable, each in a separate
   engineering run. Record the callbacks you observe, as the O5.1.3 "Connection
   loss" section describes. A Link disconnect must pause before the next
   exposure (ADR-001).
5. Run the 30-minute idle observation from the Quest provisioning runbook over
   Link. Record any update prompt, notification, boundary prompt or volume
   change.

### 4.6 Power over Link

The headset charges over the Link cable. The `battery` and `assess` commands in
`quest_station.py` assume the standalone app and continuous discharge, so they
do not apply. Record the battery level at the start and end of one full-length
mock booking under the real workload. The planning booking is 75 minutes; the
result is **TBD-at-install**.

## 5. Audio route (ADR-005)

The order matters. The level check (§5.3) fixes the OS volume, and the onset
record includes the volume step. Changing the volume after §5.5 means running
§5.5 again.

### 5.1 Hardware and wiring

1. Connect the dedicated USB audio interface to its recorded port on the render
   PC. Connect the closed-back headphones to its headphone output. Every station
   uses the same interface model and the same headphone model (MEAS-REQ-AUD-04).
   Models are recorded privately.
2. If the interface or a headphone amplifier has a physical output control, set
   it once, mark it, and record the position (**TBD-at-install**).
3. Headset speakers and Link audio are not used (ADR-005). Set the headset's own
   volume to its minimum and record it. During one scheduled play with the
   headphones off the ears, confirm that nothing is audible from the headset.

### 5.2 Windows output settings

1. Make the interface's playback endpoint the Windows default playback device.
   Copy its name exactly; it becomes station `audio.output_device` and the onset
   settings' `output_device`. #211 compares the two.
2. Set the endpoint's default format to 48 kHz and record the bit depth. The app
   refuses any other output rate with `AUDIO_DEVICE_FORMAT`.
3. Turn off audio enhancements and spatial sound for the endpoint (#296).
4. Disable every other playback endpoint, including any that carries audio to
   the headset over Link, or leave them non-default. Record the list.
5. Record the exclusive-mode options as found. Changing them later is a route
   change.
6. Turn off automatic volume changes: set communications ducking to "Do nothing"
   and use a sound scheme with no sounds (#195 W6).
7. Set the endpoint volume to the station step chosen in §5.3. Record it as a
   step out of the slider maximum.

### 5.3 Level check with the coupler (REQ-AUD-09)

This compares levels across stations with one measurement chain. It is not a
sound-pressure calibration unless the microphone itself is calibrated (O5.1.6).

1. Use the same coupler, microphone and capture interface on every station.
2. With the [calibration host](../unity/audio-calibration.md), play a
   calibration example at three in-app gains: low, middle and high of the
   comfort range. The gains are **TBD-at-install**, taken from the screening
   procedure. The host's log gives the exact gain.
3. Measure the level at the coupler. On the first station, choose the OS volume
   step (**TBD-at-install**). On every later station, adjust only the OS volume
   step until the middle gain matches the first station, then check low and
   high.
4. Pass (from the measurement procedures): at each gain, levels differ by at most
   1 dB across stations. Record a level table per station.

#### Level cap: 80 dB(A) peak at the ear (decision I7)

Each person's comfortable level stays below a hard cap of 80 dB(A) peak at the
ear. The app computes the largest allowed gain from one number per station, the
coupler measurement below. The app logic and its refusal codes are described in
`docs/unity/level-cap.md` (#348). This check needs absolute
levels, so the measurement chain must be calibrated: check the microphone with an
acoustic calibrator before and after, and record both readings.

5. **Measure.** With the OS volume step from step 3 fixed, play a signal of known
   peak `P` dBFS at a known in-app gain `g` through the headphones on the
   coupler. Read the A-weighted peak `L` in dB(A). The offset is
   `L - P - 20*log10(g)`: the dB(A) peak at the ear for a 0 dBFS peak at gain
   0 dB (linear gain 1). A full-scale signal at gain 1 gives the offset directly.
   Tooling gap: the calibration host plays only its three examples, and no tool
   yet plays a full-scale reference through the app's output path or reports an
   example's peak. Record the signal used, `P` and `g` (**TBD-at-install**).
6. **Record it in the station file.** Add `audio.level_calibration` to
   `station.local.json`:
   - `route`: the same value as `audio.route`;
   - `ear_peak_dba_at_0dbfs`: the offset from step 5;
   - `measured_on`: the measurement date, `YYYY-MM-DD`;
   - `coupler_id`: the logical ID of the coupler and meter.

   Validate the file again (§3.6). Record its new SHA-256 and keep the earlier
   file.
7. **Verify at the cap.** Start the calibration host under an engineering test
   code, never a participant's. Its `calibration_preloaded`
   log row gives `level_cap_max_gain`. Raise the level control until Louder is
   disabled; that is the cap. Play the loudest stimulus at that gain and read
   the coupler. Pass: at most 80 dB(A) peak. Until a tool can play any stored
   stimulus at a chosen gain, play each calibration example at the cap and a
   full-scale signal at `level_cap_max_gain`, and record which signals were used
   (**TBD-at-install**). A reading above 80 dB(A) blocks the station: repeat
   steps 5 to 7, and do not raise the cap by editing the offset.
8. **Re-check after any audio-route change.** Any change in §10, a new route
   value, or a change of OS volume step, interface, headphones or their cable
   invalidates the measurement. Repeat steps 5 to 7 and write a new
   `level_calibration` with the new date. The app refuses a measurement whose
   `route` differs from `audio.route` (`LEVEL_CAP_ROUTE_MISMATCH`).

### 5.4 Buffer selection

ADR-005 sets the rule: use the smallest DSP buffer with zero underruns over the
station's calibration run, recorded as `buffer_samples`.

How the app handles the buffer today:

- The player takes its DSP buffer from the build's project audio setting:
  `m_DSPBufferSize: 1024` in `unity/ProjectSettings/AudioManager.asset`. It
  does not request the station's `audio.buffer_samples`.
- #211 reads the running size with `AudioSettings.GetDSPBufferSize`. It refuses
  a record whose `buffer_frames` or `buffer_count` differs from the running
  values, or whose `buffer_frames` differs from `buffer_samples`.
- So a candidate buffer is a property of the build. Each candidate needs its own
  build and hash, and the chosen size applies to every station that runs that
  build. Whether the player should instead request the station value at
  startup is an open decision.

Underrun evidence:

- **App faults.** Each play's callback coverage check raises `AUDIO_UNDERRUN`
  (a coverage gap), `AUDIO_MISSING_PLAYBACK` (no callbacks) or
  `AUDIO_CALLBACK_INVALID`.
- **Analysis statuses.** The O6.1.3 analysis marks a play `missing` or
  `contaminated` instead of `matched`.
- **Driver counters.** If the interface software shows glitch or underrun
  counters, record them too.

Zero underruns means zero app faults and every play `matched`. Callback coverage
is software evidence, not proof of delivery at the transducer
([study audio](../unity/study-audio.md)).

1. List the candidate sizes, smallest first (**TBD-at-install**).
2. For each candidate, run the full-scene scheduled plays from §5.5 and count
   the evidence above. Choose the first candidate with zero underruns.
3. If the qualifying run in §5.5 then shows any underrun, move up one candidate
   and repeat §5.5 in a new output directory. Keep every failed run.

### 5.5 Measurement setup and calibration run (REQ-AUD-08)

Follow the [O6.1.3 runbook](../spikes/O6.1.3-runbook.md) (#192). Station
specifics:

1. **Capture chain.**
   - Put the measurement microphone in the ear coupler under one headphone cup.
   - Tap the headphone output electrically into a second input of the same
     capture interface, so every channel shares one capture clock (O5.1.6).
   - Turn off AGC, noise suppression and automatic volume on the capture side.
   - Check the chain with a test click:
     `python spikes/O5.1.6/audio_onsets.py click local-data/click.wav`.
2. **Detector threshold.** Record a noise-only capture with the scene running.
   Choose `threshold_fs` from it, write how in `threshold_source`, and freeze it
   before the run.
3. **Sync anchors.** Collect at least 3 `fit` and 3 held-out `check` anchors
   from the common-clock instrument, spread before, during and after the run.
   Audio onsets are never anchors. Record `sync.external_bound_ms` and the
   evidence (**TBD-at-install**).
4. **Settings file.** Start from the
   [settings example](../../apparatus/examples/audio-onset-calibration-settings.example.json)
   (#192) and set:
   - `evidence_kind: physical_measurement`;
   - `connection_mode: link_pc_headphones`;
   - `play_mode: scheduled`;
   - `output_device`: the endpoint name from §5.2;
   - `app_sample_rate_hz: 48000`;
   - `buffer_frames` and `buffer_count` from §5.4;
   - `volume.step` and `volume.max_step` from §5.2;
   - `full_scene_loaded: true`.
5. **Run.** Play at least 200 stored 48 kHz mono stimuli through the scheduled
   path, covering atoms and whole messages from all three profiles. The scene,
   the panel and the state source chosen at G2 run throughout. The route is
   still unmeasured here, so trial scheduling is refused and the plays go
   through the calibration scheduling path (`ScheduleCalibration`) with zero
   offset. The driver for this run is a tooling gap (§1).
6. **Analyse.** Run `tools/onset_calibration.py analyze`, then `check`, once
   per channel into fresh directories:
   - `apparatus/calibration/<station_id>/<route>/<YYYYMMDD>-acoustic_coupler-scheduled`
   - `apparatus/calibration/<station_id>/<route>/<YYYYMMDD>-electrical_loopback-scheduled`

   Pass (ADR-005 acceptance conditions):
   - at least 200 plays, every one `matched`, with every profile × {atom,
     message} cell covered;
   - zero underruns (§5.4);
   - `target.met: true`, meaning `onset_uncertainty_ms` (p95 absolute residual
     plus held-out sync error plus the external bound) is at most 20 ms.

   `route_offset_ms`, `onset_uncertainty_ms` and `residual_abs_p95_ms` are
   **TBD-at-install**.
7. **If it fails.** Investigate buffer, scheduling and transport, repair, and
   rerun in a new directory. If p95 stays above 20 ms after repair, store a
   signed `response_time_qualification` with the record (#80).
8. **Review.** A reviewer marks the acoustic-coupler record `qualified` and
   adds a `review` block. Raw WAVs go to Git LFS or the private evidence store
   ([calibration records](../../apparatus/calibration/README.md), #192).

### 5.6 Pin the record and check that the app refuses a changed route (REQ-AUD-04)

1. **Pin.** In `station.local.json`, set these from the qualified record (#211):
   - `audio.onset_calibration_record_sha256`;
   - `audio.route_offset_ms`;
   - `audio.connection_mode`;
   - `audio.output_device`;
   - `audio.buffer_samples` (the record's `buffer_frames`).

   In the joined bootstrap, pin the same file as `audio_calibration`. Validate
   the file again (§3.6) and record its new SHA-256.
2. **Manifest.** Set the `audio_onset_calibration` artifact to the record path.
   Fill `audio_route_adr005`, `audio_buffer`, `sample_rate` (48000) and
   `headphone_audio_device`.
3. **Accept check.**
   - The app starts with no `AUDIO_CALIBRATION_*` or
     `JOIN_AUDIO_CALIBRATION_*` fault.
   - Trial scheduling is allowed.
   - Logged `onset_uncertainty_ms` equals the record's value plus the per-play
     DSP mapping bound ([study audio](../unity/study-audio.md), #211).
   - Spot-check a few trial-log rows against the record (#80).
4. **Refusal checks.** Run each one on a copy of the accepted configuration.
   Record the observed code, then restore the accepted file and confirm its hash.
   - Change `audio.output_device`: `AUDIO_CALIBRATION_DEVICE_MISMATCH`, or
     `JOIN_AUDIO_CALIBRATION_DEVICE_MISMATCH` at joined startup. Trial
     scheduling is refused with `AUDIO_ROUTE_UNCALIBRATED`.
   - Change `audio.onset_calibration_record_sha256`: `AUDIO_CALIBRATION_PIN_MISMATCH`.
   - Pin a provisional record: `AUDIO_CALIBRATION_NOT_QUALIFIED`.
   - Set the endpoint to another sample rate: a refusal (`AUDIO_DEVICE_FORMAT` or
     `AUDIO_CALIBRATION_RUNTIME_MISMATCH`). Set 48 kHz again afterwards.
   - Change the Windows default playback device during an engineering play:
     `AUDIO_DEVICE_CHANGED`.
5. **Repeat.** After the refusal checks, repeat the accept check and the reboot
   recheck (§3.8).

The app cannot see the OS volume step, the build, Windows audio settings,
drivers, firmware or the physical units. The operator checks those (§9).

## 6. Input: controller ray check

Station `input_method: controllers`; the private panel file uses
`input_method: controller_ray` (ADR-004).

1. Set `engineering_mode` in the private panel file for a one-shot engineering
   request ([response panel](../response-panel/README.md)).
2. Over Link, check each of these with each hand. The ray hand can change at
   screening, so both are checked.
   - The tracked controller pose drives the ray.
   - One trigger rising edge gives one activation.
   - Holding the trigger through a reconnect produces no press.
3. Turn one controller off, then cover it to lose its pose, then open the system
   menu. Each must hide the panel and latch a fault. Recovery happens only after
   explicit confirmation at a safe boundary. Record the log events.
4. This is a station check, not a tester measurement. The tester measurements
   run at G3 with #49's harness on the Link build, and only after the compliance
   confirmation in §1:
   - three testers × 32 commands, p95 prompt-to-Commit at most 3.5 s
     (REQ-APP-09);
   - the legibility ladder (REQ-APP-07).

   Testers cannot later take part as participants (ADR-004).

## 7. State source and network

1. **Isolated wired segment.** The render PC and its Isaac host sit on an
   isolated wired segment. The network owner supplies:
   - fixed addresses;
   - gateway mappings;
   - a reviewed firewall and switch policy
     ([station isolation](../isaac/stations.md)).

   There must be no route to physical robots or their DDS traffic (#88).
   Station traffic uses only the wired adapter (ADR-001). Record any other
   adapter and its state.
2. **Configuration.** Record `station_network_settings` (topology, adapter,
   segment). Put the endpoint in the private `isaac_endpoint`; a live source
   ends in `/state`. `validate_station.py` rejects credentials in the URL.
3. **Isolation evidence.** Capture packet counts on every relevant egress
   interface during a full run. Publish counts only.
4. **Freshness check.** If G2 (#63) selects the live source, run the
   StateSources or joined engineering build over Link with the live source for
   **TBD-at-install** minutes. The evidence is the private receive-age CSV and
   source events. Pass at install:
   - zero `STATE_STALE` events (a gap over 250 ms) after startup;
   - one fresh reset confirmation;
   - clock evidence present in `state-source.local.json`.

   The eight-hour simultaneous soak belongs to G2. If G2 selects the snapshot
   source, check the snapshot hash instead.
5. **Cross-talk.** Once more than one station runs, use the cross-talk and
   wrong-station harness in the station isolation document.

## 8. Station acceptance checklist

A station that fails any INSTALL row is not used for sessions until it is fixed
and rechecked (#88). Rows marked G3 run on station hardware for the engineering
pilot.

| # | Check | Requirement | Gate | Evidence produced |
|---|---|---|---|---|
| 1 | Render PC baseline recorded and unchanged after reboot (§3, §3.8) | #296 | INSTALL | private station record, both readings |
| 2 | Build files match the build manifest; station file valid for `pcvr_link` (§3.5, §3.6) | #88 | INSTALL | private deployment record; manifest `app_build` |
| 3 | One headset model and OS build on all stations and the spare; update deferral recorded (§4.1, §4.2) | REQ-APP-02 | INSTALL | manifest `headset_units`; private headset record |
| 4 | Link rate chosen; 900-second capture with no freeze over 250 ms; removal, sleep and disconnect callbacks (§4.4, §4.5) | ADR-001 acceptance | INSTALL | O5.1.3 frame CSV, `summary.local.json`, events |
| 5 | Kiosk-equivalent restrictions and 30-minute idle observation (§4.5) | #296 | INSTALL | private record |
| 6 | Level within 1 dB across stations at three gains (§5.3) | REQ-AUD-09 | INSTALL | level table per station |
| 6a | Level-cap measurement in the station file; loudest stimulus at the capped gain at most 80 dB(A) peak at the coupler (§5.3, decision I7) | REQ-AUD-09 | INSTALL | station `audio.level_calibration`; calibrator readings; coupler readings at the cap |
| 7 | Smallest underrun-free buffer (§5.4) | REQ-AUD-04 | INSTALL | candidate run logs; record `buffer_frames` |
| 8 | 200 scheduled onsets, target met including the sync bound (§5.5) | REQ-AUD-08 | INSTALL | `apparatus/calibration/<station>/<route>/<run>/` record, `plays.csv`, `residual-histogram.csv`, inputs |
| 9 | Record pinned and accepted; changed route or device refused (§5.6) | REQ-AUD-04 | INSTALL | private app logs with codes; station file hash; manifest `audio_onset_calibration` |
| 10 | Controller ray check over Link (§6) | REQ-APP-09 (station part) | INSTALL | response-panel JSONL |
| 11 | Network isolation (§7) | #88 | INSTALL | packet counts |
| 12 | Live-state freshness short check (§7) | REQ-APP-10 (related) | INSTALL, then G2 | receive-age CSV, source events; G2 soak (#63) |
| 13 | Console on the same PC, logging and upload, separate operator display (§3.7) | #88, #195 | INSTALL | console audit; #195 results table |
| 14 | Full mock visits with frame capture, no freeze over 250 ms in cue or response (O5.6.3, #207) | REQ-APP-08 | G3 | frame capture logs and summaries |
| 15 | Legibility ladder with the longest real labels (§6) | REQ-APP-07 | G3 | ladder sheet; `text_angle_deg` in the private panel file |
| 16 | Three testers × 32 commands with the controller ray (§6) | REQ-APP-09 | G3 | #49 input harness results |
| 17 | 32 commands × 5 poses view capture with zero pixel tolerance ([O6.1.2](../spikes/O6.1.2-runbook.md)) | REQ-NEUT-04 | G3 | `tools/view_leakage.py` report and comparison CSV |
| 18 | Apparatus manifest collected and verified | #88 | INSTALL | private manifest and its pinned SHA-256; reviewed public summary |

## 9. Pre-session recheck

Until #296 provides a tool, check by hand before the first session of each day.
Compare against the signed station record:

- Windows build;
- GPU driver;
- Link software version and active OpenXR runtime;
- headset OS build (§4.1);
- interface firmware and driver;
- default playback endpoint name, 48 kHz format, enhancements and spatial sound
  off, and the OS volume step;
- refresh rate;
- the build hash loop (§3.5);
- the SHA-256 of the station file and of the onset record;
- the station file's `audio.level_calibration` is present and its `route`
  equals `audio.route` (§5.3).

Also run the #195 checks: casting, notifications, clock and console. Any
difference blocks sessions on that station until it is requalified (§10).
Record the date and the result.

## 10. What invalidates a calibration

An onset record applies only to its own station, route, connection mode, play
mode, output device, buffer size and count, sample rate, volume step and app
build ([calibration records](../../apparatus/calibration/README.md)). Treat each
of these as a change that invalidates it too:

- the interface's firmware or driver;
- the Windows endpoint settings in §5.2;
- the hardware output control;
- the USB port of the interface;
- swapping the interface, the headphones or their cable for another unit, even
  of the same model;
- a Windows, GPU driver or Link software update;
- a change of refresh rate;
- underruns, measured drift or a gain path change (ADR-005 revisit trigger).

On any such change:

1. Stop sessions on that station.
2. Rerun §5.4 to §5.6 into new record directories, and recheck §5.3 if the
   level path changed. After any audio-route change, repeat the level-cap
   measurement and check (§5.3 steps 5 to 8). Keep superseded records unedited.
3. Start a new apparatus version after freeze, with reason, affected records and
   validation ([versioning](../versioning.md)).

Unity checks station, route, connection mode, output device, sample rate and
DSP buffer at startup (#211). Everything else in the list is the operator's
check (§9).

A different interface or headphone **model** is a route change. It reopens
ADR-005 for every station.

A swap to the recorded spare headset does not change the audio route, because
the route and its clock are on the render PC (ADR-005). The proposed rule:
record the swap, run §4 for the spare on that PC, and keep the onset record.
