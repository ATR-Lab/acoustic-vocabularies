# Station provisioning records

The public files here are blank engineering examples. They do not identify a
device or certify a station. Follow [the RA procedure](../../docs/provisioning/O5.3.2-runbook.md).

Store real records as `apparatus/stations/<unit>-<observation>.local.csv` and
charging plans as `*.local.json`; those suffixes are ignored. Keep original
observations, installed APK copies, recordings and operator sign-off private.
Do not put account names, serials, participant information or raw device output
in issues or pull requests. Logical station/unit IDs are separate from serials.

`tools/quest_station.py` owns the exact CSV column order. `inventory` creates one
row and leaves all manual fields blank; a blank means **not observed**. Preserve
that output and complete a private reviewed copy. `compare` checks only recorded
device/OS/app identity; its success does not re-observe manual settings.

| Fields | Source and interpretation |
|---|---|
| `record_status`, `observed_utc` | Tool observation status and UTC time; never automatic qualification. |
| `station_id`, `unit_id`, `unit_role` | Operator-selected logical IDs and `station` or `spare`. |
| `hardware_serial`, `model` | Selected device properties, private. |
| `os_incremental`, `os_fingerprint`, `security_patch`, `android_release` | Actual reported software baseline, private. A changed value requires apparatus review. |
| `installed_version_name`, `installed_version_code` | Package metadata; these alone do not establish the exact app build. |
| `expected_build_id`, `expected_commit_sha`, `expected_apk_sha256` | Verified nondevelopment, clean-source Android build manifest. |
| `installed_apk_sha256`, `installed_artifact_match` | Pulled installed single APK bytes compared with the manifest. Split APKs are unsupported and fail closed. |
| `battery_percent`, `battery_health_raw_code`, `battery_status_raw_code` | Android observations. The health integer is not measured capacity or battery state of health. |
| `lab_account_confirmed`, `developer_mode_confirmed` | Staff verification, without recording account identifiers. |
| `os_deferral_method`, `notifications_suppressed`, `stationary_boundary_confirmed` | Operator records exact build-specific UI steps and observed result. |
| `audio_route`, `system_volume_level`, `automatic_volume_control` | Actual route and system controls; ADR-005 approval is still required. |
| `sleep_setting`, `idle_observation_minutes`, `idle_prompts_seen`, `focus_fault_observed` | Manual device observation and private evidence reference. |
| `measured_runtime_minutes`, `booking_minutes`, `buffer_minutes`, `agreed_start_min_percent` | Measured duration and operator-approved planning values. No inferred threshold. |
| `operator_signoff` | Private review reference/date; a tool never signs off a unit. |

Battery CSVs use monotonic elapsed seconds plus UTC, unit ID, battery percentage,
raw status/health codes, three power-source flags and app process presence. They
contain no serial. Process presence does not establish foreground view or the
intended workload. The assessor requires explicit duration, buffer and maximum
sampling gap; it reports coverage only and never extrapolates remaining runtime.

The charging-plan example deliberately leaves buffer, start threshold and
measured evidence unset. Its 75-minute booking is the issue's planning duration,
not measured endurance. Set `qualified_for_sessions` only through the lab's
approved apparatus review after all physical checks are complete.
