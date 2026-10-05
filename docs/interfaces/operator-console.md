# Operator display contract

Issue #73 uses a Python standard-library server on `127.0.0.1`. One console writer
reads configured private inputs. Browser requests contain only configured visit
aliases, coded staff IDs and bounded actions. Every mutation checks exact Host,
Origin and a per-process CSRF token. Responses use no-store, a restrictive content
policy and frame-ancestor denial. No CORS, arbitrary file route or remote assets.

The private catalog supplies `allocation_list`, `reveal_log`, `participant`,
`allocation_list_sha256`, `manifest`, `manifest_sha256`, `schedules`, `schedule`,
`sheet`, `packages`, `anchors`.
It uses #31 `RevealLog.revealed()`; the restricted key is rejected. A typed ID
cannot allocate someone or bypass consent/screening/orientation. Load binds the
revealed person/slot and checks independently pinned #32 manifest, #30 schedule
manifest, schedule bytes, sheet bytes, package mapping and template oracle.
The configured allocation hash is independently pinned alongside the manifest.
The reveal API compares its source study/set, DEMO marker and private seed label
without returning that label to the UI. Repeating slot IDs across seeds cannot
substitute a different authorized list. Placeholder hashes fail.
#64/#67 independently verify the actual package/WAVs;
neither a CSV hash nor a browser flag establishes engine readiness.

Windows use the station's local calendar and the person's recorded anchors:
A D7=D0+7±1; B V2/V3=V1+2±1/+4±1; W1/W4=person's V3+7±1/+28±2 days.
Yoked must follow active within 24 elapsed UTC hours. Missing anchors block.
A logged timing exception lasts only that visit/calendar day. Hash, old-hash,
lock, audio, reset, input, headset and stale failures are not overrideable.
This tightens #73's broad override wording to preserve #64/#67 mandatory gates.

## Local mailbox v1

The first transport is a private directory shared by the console and injected
Unity adapter on one Link PC. No LAN listener/firewall/network change is made.
Keep the directory inaccessible to other accounts. Atomic UTF-8 `state.json`
is ≤64 KiB, with an increasing heartbeat at least twice/second. Reject >2 s old,
>0.5 s future, regressing/nonadvancing state; changed nonce requires explicit load.

Exact state fields:

- `version`: integer 1; `session_nonce`: 32 lowercase hex; `sequence`: positive integer;
  `utc`: ISO 8601 UTC.
- `receipt`: null or `{request_id, sequence, status, code}`. Status is `accepted`
  or `rejected`. Upstream text is never echoed to the browser.
- `run_sheet_manifest_sha256`, `schedule_sha256`, `package_sha256`: lowercase SHA-256.
- `engine_state`: `awaiting_operator/running/paused/stopped/complete/faulted`.
- `completed_counts`: integer array in producer block order, never raw trials.
- `admission`: exact booleans `verified`, `old_hashes_ok`, `locks_ok`, from trusted host.
- `health`: exact booleans `headset`, `audio`, `reset`, `input`; finite nonnegative
  `bridge_age_ms`, `frame_ms`, `max_gap_ms`. State age/frame gap >250 ms faults.

The consumer fsyncs and atomically replaces `command.json` with exactly `version`,
`session_nonce`, `request_id` (32 lowercase hex), `sequence` (next integer), `command`
(`load/start/pause/resume/stop`), `run_sheet_manifest_sha256`, `schedule_sha256`.
All requests bind the loaded visit/current process. The adapter durably records
before invoking #67 and sends a bound receipt afterward. Load compares the
independently loaded host visit; it never makes unknown content ready. One request
may be outstanding. Timeout is uncertain, not permission to retry. Only the exact
late receipt reconciles it, after the console durably records its terminal result.
Stop alone can supersede a pending lower sequence: the adapter consumes the higher
sequence and rejects older commands. No start/resume/pause/load can skip a sequence.
The uncertain original request remains in the audit. Replayed requests cannot
repeat side effects. A mailbox-scoped lock prevents two consoles with different
ports or audit filenames from racing the same engine.
#67 remains the authority for onset consumption and neutral pause boundaries;
an acknowledgment is not evidence the headset is already paused.

## Masked records

Visible state is rebuilt from closed fields. Intended tuple, correctness,
candidate history, hidden schedule content and raw journal events never serialize.
The producer masker scans emitted Study A HTML, JSON and CSV, plus notes and coded
IDs before append. Static JavaScript's HTTP option name is technical syntax;
it carries no generation label or hidden study data.

Append/fsync audit rows include UTC, protocol, coded staff, sequence and previous
line hash. A separate fsynced atomic tip pins the last complete line. Interior or
final-line edits, truncated suffixes against the retained tip, partial lines and
writer failure block continuation. A crash between row and tip persistence also
blocks rather than discarding evidence. Preserve raw failures. The local tip does
not resist coordinated tampering with both files; approved-store/export
qualification remains #72. One file lock
permits one console writer; inspect a stale lock after a crash, never delete
evidence or silently restart a participant visit.

Run-sheet CSV uses #32 `RUN_SHEET_COLUMNS`, reviewed template hash
`b0bd19bf23f8b676429ef5d1d54321d3773a227fdc6e996287d19a7865947a46`.
Expected/actual counts remain separate after interruption. Per-block start/end
times stay blank until actual engine block receipts exist; visit request times
are not substituted. The external deviations header is unavailable, so that
six-column CSV is explicitly provisional. Formula-like notes are escaped.
Sign-off records observed counts; it does not invent completion or qualify hardware.
