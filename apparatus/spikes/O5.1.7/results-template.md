# O5.1.7 input and legibility results

Status: **Needs operator run. No input method or text size selected.**

| Method | Testers with all 32 commands | Median / p95 seconds | Wrong selections / all selections | Wrong Commits | Accidental Commits, reviewed | Loss events / recovery | Comfort | 3.5 s screen |
|---|---|---|---|---|---|---|---|---|
| Controller ray | Pending | | | | Unknown | | Pending | Pending |
| Hand poke | Pending | | | | Unknown | | Pending | Pending |

- Observer reference, seated posture, reach/dominant hand and device/build settings: pending.
- Smallest angle read without error by every tester: pending.
- 1.5× candidate angle and subsequent panel recheck: pending.
- Controller disconnect, controller pose loss, hand subsystem/pose loss and app focus tests: pending.
- Pause occurs before next prompt, explicit Next resumes/retries: pending device verification.
- Retry attempts, missing commands and timing outliers: report without silently discarding.
- Sanitized headset captures of both methods, no people/names/device identifiers: pending.
- ADR-004 recommendation and rationale: deferred to measured results.

The action labels `tray-1`…`tray-4` and `container-1`…`container-4` are public engineering placeholders. Actual protocol action-label widths remain unverified while the read-only methodology copy is unavailable. Final label legibility must be rechecked privately with authorized wording before G1 acceptance.

Export sanitized `docs/spikes/input/trials.csv` only after internal engineering review; keep raw captures and local settings in ignored `local-data/`. Team testers must not later be pilot/confirmatory participants.
