# Native soak collection

The monitor and collection tools record facts; they do not grant participant admission. Use fresh ignored/private directories. No tool below installs software, launches Isaac, changes a network, injects a fault by itself, or resumes a paused exposure. The actual joined host must retain its existing package, review, calibration, schedule and source gates. A missing authority can produce truthful paused observations; it cannot become a completed teaching/protected run.

## Unity binding

`AcousticVocab.Soak.SoakCaptureHost.Install` accepts raw independently pinned plan bytes, a fresh output directory, the actual validated station/build/scene/snapshot/schedule identities, a function returning actual `SoakContext`, and a fault-pause callback. The host owner binds `SourceApplied` only after the real live renderer applies and displays a frame. Pass the latest progressing sample's local receipt time from `LiveIsaacSource`, in `Stopwatch.GetTimestamp()/Stopwatch.Frequency` seconds. Interpolated publish time is not a receipt timestamp. Snapshot/fallback frames cannot enter this callback.

The monitor counts distinct applied source session/sequence values and observes `Application.onBeforeRender`. It runs a ten-millisecond timer, detects source/render gaps above 250 ms even if the main thread stalls, and writes a heartbeat at least every 250 ms while the timer is serviced. Protected gap faults latch. Actual timer starvation or journal I/O stalls remain visible as heartbeat gaps. `onBeforeRender` is application callback evidence, not photon/headset latency. The counter is a conservative lower bound on applied live progress, not network packet throughput.

Native rows are fsynced, sequential, chained over exact compact UTF-8 bytes plus newline, and bound to one Stopwatch epoch/frequency. Journal failure escapes or latches a pause fault. Installation is transactional; shutdown closes callbacks, timer and file independently even if the terminal write fails. Incomplete logs remain incomplete.

Prepare the station monitor plan privately with exactly these fields; the hashes below are illustrative placeholders and must be replaced by independently verified actual values:

```json
{"version":1,"scope":"synthetic_nonstudy","participants":false,"station_id":"station-01","build_id":"engineering-build","scene_sha256":"<actual SHA-256>","snapshot_sha256":"<actual SHA-256>","schedule_sha256":"<actual SHA-256>","source_kind":"live","seconds":28930,"client_kind":"headset_equivalent","substitute_justification":"Describe the actual substitute and its limits"}
```

Keep the actual detector source revision/hash in the later qualification manifest. The joined-host integration owns command-line provisioning and actual context binding; this library does not discover authority by filename or set readiness booleans.

## Collection driver

Create a separate private collector plan with exact fields `version:1`, `scope:"synthetic_nonstudy"`, `participants:false`, integer `seconds`, and `stations`. Each station has exactly:

```json
{"station_id":"station-01","plan":{"path":"station-01-plan.json","sha256":"<raw plan hash>"},"capture_directory":"station-01-capture","source_logs":{"publisher":"publisher.native.jsonl","command":"command-events.jsonl","host":"host/resources.csv"}}
```

Paths may be explicit absolute private paths or relative to the collector plan. UNC paths and existing symlink/reparse ancestors are refused. Each source role must be distinct. The output cannot overlap an input. Preserve the input plan and compute its independent raw hash before invocation:

```text
python -m isaac.soak.collect private/collector.json --sha256 RAW_SHA256 --validate-only
python -m isaac.soak.collect private/collector.json --sha256 RAW_SHA256 --output private/fresh-collection --startup-timeout 120
```

The collector waits for each real native capture, acquires an exclusive `soak-collector.lock`, and publishes observation-only `coordinator-probe.json` requests. Unity logs each exact request inside its own native clock. The collector records actual monotonic send/receive nanoseconds. For each station, `receive(start)` is a conservative beginning and `send(end)` a conservative ending: Unity processed the start before that beginning and processes the end after that ending. Their common intersection is measured in one coordinator clock. No Unity/host epoch subtraction or assumed clock offset occurs.

Monitor duration must exceed the desired common window plus startup/ending margin. A 28,930-second monitor and 28,800-second collector allow a 120-second startup budget plus ten seconds margin; actual stagger or blocking can still fail. A five-second minimum margin is enforced, but it is not a guarantee of synchronized starts. Stale/missing probe replies, early shutdown, changed native prefixes and incomplete terminals are retained as failures. Probes are not automatically retried. Cleanup removes only the owned lock; probe artifacts remain. Restart uses a fresh capture directory, never an overwritten prior run.

Outputs include the coordinator hash chain, each exact native Unity journal, station plan, receiver-only normalized events, snapshots of publisher/command/host files, and `collection-summary.json`. Exit 0 means collection finished without a driver exception; the report still says `NO_GO`. Native source snapshots are not falsely labeled complete or semantically verified. Generic `Observe` payloads are preserved without granting them command, response, cue or recovery authority. The full analyzer still rejects receiver-only events without actual reset/trial/lock/fault evidence.

## Linux resource/step capture

Instrument the already approved simulation owner loop explicitly:

```python
from isaac.soak.host import StepTrace
trace = StepTrace(private_output / "step-trace.jsonl")
try:
    # Keep existing targets, full neutral checks, GC and stepping unchanged.
    with trace.measure():
        sim.step(render=False)
finally:
    trace.close()
```

In a continuous loop, use one `measure()` per actual step and close only when the run ends. The helper measures real monotonic intervals, refuses nested/wrong-owner measurements, and fsyncs one-second aggregates outside the measured physics call. That logging overhead still counts toward actual overall pacing. It does not fabricate a step duration when the source stops.

After the first complete aggregate, run the sampler in the same Linux PID/clock namespace as the source, with the explicitly identified existing source PID and GPU index:

```text
python -m isaac.soak.host --pid SOURCE_PID --gpu-index 0 --steps private/step-trace.jsonl --seconds 28800 --output private/fresh-host --source-kind isaac_runtime
```

It checks PID start ticks and command-byte hash every sample, rejecting PID reuse. RAM is whole-host used memory plus separate process RSS; VRAM is whole selected-device memory, including unrelated workloads. It reads only the named PID and `nvidia-smi`; it never stops another process. The step column is the maximum measured duration in the latest complete aggregate, not an interpolated or estimated simulator rate. Missing/stale step traces and subprocess failures preserve partial output. A synthetic sampler test must use `--source-kind synthetic_diagnostic`. Host-relative coverage and Unity coverage remain separate until their retained run windows are reviewed.

## Synthetic schedule driver

`isaac.soak.driver` generates and runs the per-station non-study schedule. The schedule is the canonical JSON output of a seeded generator: alternating teaching and protected blocks, a `set_mode` at each block start, a `reset` before every trial, legal-pair demos only in teaching blocks, a dummy response and a `demo` lock probe in every protected trial, and optional planned fault slots in protected blocks after the first. `load_schedule` accepts only the exact bytes that the generator reproduces for the pinned hash, then reruns an independent gate (demos only in teaching mode, probes and responses only in test mode, a fresh reset before every trial, a probe in every protected block, at least two seconds between commands and at most twenty commands per minute). Put that hash in the Unity monitor plan's `schedule_sha256`.

```text
python -m isaac.soak.driver schedule --station-id station-01 --seed SEED --seconds 28800 --fault-types isaac_crash,wifi_drop,uplink_disconnect --output PRIVATE/station-01/schedule.json
python -m isaac.soak.driver validate PRIVATE/station-01/schedule.json --sha256 SCHEDULE_SHA256
python -m isaac.soak.driver run --schedule PRIVATE/station-01/schedule.json --sha256 SCHEDULE_SHA256 --control-session-id SESSION --endpoint ws://127.0.0.1:PORT/commands --output PRIVATE/station-01/driver
```

`validate` prints the number of commands. The dispatcher never evicts an idempotency entry, so the station's `cache_size` must exceed that count or admission faults with `IDEMPOTENCY_CAPACITY` partway through the run. With the default 300-second blocks and 20-second trials, an eight-hour schedule has about 2,800 commands; the dispatcher default of 1,024 is too small.

The runner uses one private WebSocket on a loopback TCP endpoint or a Unix socket and a separately pinned control session. It sends one request at a time with its own pacing. Late steps never burst. Each `command_intent` row is fsynced before its send; the verbatim reply follows as `command_reply` with send/receive monotonic nanoseconds. Replies must have the exact private-reply shape, the same request ID, a known reason, `duplicate:false`, consistent `reset_ok` and the pinned health session. Anything else is a `REPLY_REFUSED` halt. A transport error or timeout is a `TRANSPORT_ERROR` halt. No request is retried or replayed. A well-formed reply that the schedule did not expect, such as an accepted or non-protected lock-probe rejection, a failed reset, or `DEMO_NOT_IMPLEMENTED` without `--allow-missing-demo-content`, is first forwarded to Unity and then halts as `UNEXPECTED_OUTCOME`. Its failure still reaches the analyzer. The driver also refuses a demo unless the last acknowledged backend mode is teaching, and a lock probe unless it is test.

`driver-journal.jsonl` and `unity-inputs.jsonl` are append-only SHA-256 chains over exact compact rows. The Unity input feed carries `block_begin`, `trial` (with its fresh reset request ID), `dummy_response`, `fault_marker` and `command_ack` (verbatim reply and its hash) rows. It is the schedule input that the joined Unity host consumes. A process restart resumes only with `--resume`. It refuses a torn tail, a completed journal, a diverged feed or an unrecovered fault. It records each unanswered request as `interrupted_exchange` without sending it again, records the skipped steps, and restarts at the next block boundary with that block's mode command.

Fault slots are inert (`fault_slot_not_injected`) unless `--authorize-fault-injection --fault-hook operator-file` are both given. The built-in hook writes `operator/fault-request-<id>.json` and waits for an operator-written `fault-done-<id>.json` with `{"version":1,"fault_id":...,"fault_type":...,"performed":true}`. The driver never kills a process or changes a network itself. After the hook returns, it waits for `operator/recovery-<id>.json` with `{"version":1,"fault_id":...,"control_session_id":...,"operator_initiated":true}`, reconnects, probes health, sends one recovery reset bound to the fault, and continues at the next fresh trial or block. It never resumes an exposure. That remains an operator action in Unity.

## Command/data normalization

`isaac.soak.normalize` joins the station plan, retained Unity native journal, pinned schedule, one or more durable private command logs, the driver journal, the Unity input feed and the hash-chained data journal into the analyzer's `events.jsonl`, plus a `normalization.json` summary. Every fact uses the `t_s` of the native Unity row that observed it. The joined host must call `SoakCaptureHost.Observe` with exactly these payloads:

| Native kind | Payload | Normalized fact and independent join |
|---|---|---|
| `reset_receipt` | `input_seq`, `request_id`, `reply_sha256` | `reset`: feed `command_ack`, driver reply bytes and exactly one durable reset or `set_mode` test terminal event; `reset_ok` from that event; the active Unity-observed fault, if any |
| `lock_probe_receipt` | same | `lock_probe`: block and ID from the latest native `context` row, which must be protected; `rejected` only for `PROTECTED_TARGET_COMMAND` in test mode |
| `fault_injection` | `input_seq`, `fault_id`, `last_committed_data_sha256` | `fault`: feed `fault_marker` and the driver's `fault_intent` type |
| `durable_record` | `data_event_id`, `data_sha256`, `reset_request_id` | data session `response` → `record_commit`; `state_before` → `trial_begin` bound to a Unity-placed reset; `session_paused` → `pause` of the active fault |
| `cue_observation` | `data_event_id`, `data_sha256` | data session `onset_evidence` → `exposure` and `cue_playback` (cue = original trial of a retry; audible = consumed exposure or confirmed audible; a retry is treated as unheard) |
| `operator_resume` | `fault_id`, `data_event_id`, `data_sha256`, `reset_request_id`, `last_committed_data_sha256` | data session `operator_resume` → operator-initiated `resume` of the active fault after a successful placed reset |

```text
python -m isaac.soak.normalize --plan station-plan.json --plan-sha256 PLAN_SHA256 --native soak-native.jsonl --schedule schedule.json --command-log command.native --driver-journal driver-journal.jsonl --unity-inputs unity-inputs.jsonl --data-journal data.jsonl --output PRIVATE/station-01/normalized
```

Repeat `--command-log` for each service restart. Unknown native kinds, malformed payloads, duplicate receipts, unmatched hashes, tampered chains and protected receipts outside a protected context are refused. Refusal also covers a driver exchange that differs from the durable command record, an accepted test-mode demo anywhere in a command log, a failing probe or failed reset with no Unity receipt, and a driver fault absent from the Unity journal. A rejected probe or successful reset that Unity never received is counted as unplaced and is not invented. The summary always says `NO_GO` and `analysis_required`; only the analyzer applies the criteria.

## Remaining full-soak integration

The joined Unity host still has to consume `unity-inputs.jsonl`: set its block context, run trials and dummy responses through the real engine, and log the receipts and data references above through `Observe`. That binding and its native validation are not implemented here. Until then, an actual run produces receiver-only facts and normalization or analysis returns `NO_GO`. The analyzer manifest is still assembled by hand from the normalized events, resources and hashed native sources.

Physical crash/Wi-Fi/uplink injections, explicit operator recovery resume, headset battery/thermal capture, eight-hour multi-station overlap, native-log review, and signed G2 remain unperformed. Qualifying motion content and passing publisher timing remain separate blockers. The newest actual backend reset/lock verification is recorded in the #53/#55 derivatives dated 2026-10-05; the contemporaneous twenty-second publisher screen still fails timing. No longer run is implied by these tools or their synthetic tests.
