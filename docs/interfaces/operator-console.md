# Operator display contract

Issue #73 uses a Python standard-library server on `127.0.0.1`. One console writer
reads configured private inputs. Browser requests contain only configured visit
aliases, coded staff IDs and bounded actions. Every mutation checks exact Host,
Origin and a per-process CSRF token. Responses use no-store, a restrictive content
policy and frame-ancestor denial. No CORS, arbitrary file route or remote assets.

The private catalog supplies `allocation_list`, `reveal_log`, `participant`,
`allocation_list_sha256`, `manifest`, `manifest_sha256`, `schedules`, `schedule`,
`sheet`, `packages`, `anchors`, plus the `screening` alias described below.
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
Orientation receipt failures are likewise not overrideable.

## Pre-allocation orientation outcome

Issue #66 (AC5) shows the native orientation eligibility outcome before any
allocation is revealed or bound. The private `--config` may contain a
`screenings` map of coded aliases to exactly `screening_id`, `receipt_path`,
`receipt_file_sha256` (independent raw file pin) and `journal_path`: the expected
screening ID plus the triple the #31 admission CLI accepts in `orientation_files`.
The receipt's screening ID must match the configured one. Every `visits` entry
must name one of those aliases in `screening`; a visit without one does not load.

The console never re-implements receipt checks. `av_schedules.admission.read_orientation`
(the unchanged admission gate) decides whether allocation may proceed. The
display-only `inspect_orientation` runs the same file pin, closed-shape, canonical
hash, journal byte/hash, header, terminal-outcome and per-item evidence checks,
but also accepts a consistently recorded `fail` or engineering-draft receipt so
the operator can see it. It is never admission evidence.

The operator selects a screening and presses **Verify receipt** (`orientation`
command, CSRF bound, available with no visit loaded). Each verification appends an
`orientation_verified` audit row before the result is displayed. If that append
fails, the screening returns to `not_verified`. Rows show closed fields only:

| Field | Values |
|---|---|
| `status` | `not_verified`, `incomplete` (no receipt file), `verified`, `rejected` |
| `outcome` | `pass_first`, `pass_second`, `fail`, or null unless verified |
| `engineering_draft` | boolean, or null unless verified |
| `receipt_sha256` | canonical receipt hash, or null unless verified |
| `reason` | closed lowercase code, such as `orientation_failed`, `orientation_engineering_draft`, `orientation_receipt_missing`, `orientation_file_hash`, `orientation_journal_hash`, `screening_receipt_mismatch` |
| `allocation` | `eligible_for_handoff` only when `read_orientation` accepts the pinned files; otherwise `blocked` |

The screening ID, orientation ID, station, paths, item responses and check scores
never serialize. Item-level evidence remains in the private orientation journal.
Visit load verifies the bound receipt again and refuses `orientation_blocked`.
It also requires the configured screening ID to equal the visit participant.
Start/resume reads the pinned files again. A receipt or journal changed after load
raises the `orientation_unverified` fault and blocks start; pause/stop remain
available. DEMO and simulation visits carry no receipt and stay labelled synthetic.
A non-DEMO visit without a binding is refused.

This view does not perform allocation. The eligibility and reveal receipts still
come only from `DurableRevealLog` / `admission_cli`, which re-reads the same files.
An `eligible_for_handoff` row is a software integrity result. It is not consent,
screening, human-subjects approval or protocol-owner acceptance of the wording.

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

### Native owner integration

`AcousticVocab.OperatorConsole.OperatorMailbox` accepts an already validated
`FixedSlotEngine`, a synchronous durable `IOperatorCommandJournal`, and trusted
admission, health and monotonic-clock providers. `FileOperatorCommandJournal`
creates a new nonce-specific, append/fsync, sequence/hash-chain audit. It never
overwrites an older process journal. Generate a fresh GUID32 nonce for every
process; keep the directory private and retain all failed/torn evidence. An
exclusive writer handle prevents two engine owners. This adapter does not load
participants, reveal allocations, construct content factories or grant readiness.

The owner calls `OperatorMailbox.Tick()` on its engine thread. This is the sole
driver of `FixedSlotEngine.Tick()`: a factory implementing `ISessionContentPump`
is pumped by the engine before slot and response boundaries. Do not add a separate
Unity Update scheduler for module deadlines. Disposal or a durable-write/host
failure latches the adapter and stops active content; recovery requires a new
trusted owner and explicit console load. Restart does not replay unacknowledged
commands. A request without a durable terminal result remains uncertain.

Load checks trusted admission. Start/resume additionally check trusted health and
the engine boundary. Pause/stop remain available after admission or health fails.
The snapshot counts completed original scheduled IDs in producer block order;
retries do not double-count them. No trial or answer fields leave the adapter.

The implemented transport target is same-PC Windows Link. State publication
flushes a fresh temporary file and uses same-directory `MoveFileExW` replacement.
Only Windows errors 5/32/33 permit up to ten requested 2 ms waits for contention;
the same file publication is retried, never an engine action. Persistent failure
retains the temporary file, latches the engine and leaves old state to become stale.
The Python reader opens one file generation with delete sharing and validates its
type/byte bound through the opened descriptor. Freshness, nonce and sequence checks
are unchanged. The OS may extend wait duration; this is not a real-time timing
guarantee. See Microsoft's [MoveFileExW contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw).
This is software recovery behavior, not a power-loss durability qualification.
Android IL2CPP compilation does not provision this Windows transport; invoking
publication on an unqualified platform fails closed.

No participant scene host is fabricated by this issue. A joined host must supply
the real validated schedule, durable session/content journals and measured
readiness authorities, and implement/capture the neutral participant pause view.

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
