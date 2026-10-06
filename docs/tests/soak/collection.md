# Native soak collection

The monitor and collection tools record facts; they do not grant participant admission. Use fresh ignored/private directories. No tool below installs software, launches Isaac, changes a network, injects a fault, or resumes a paused exposure. The actual joined host must retain its existing package, review, calibration, schedule and source gates. A missing authority can produce truthful paused observations; it cannot become a completed teaching/protected run.

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

## Remaining full-soak integration

The real engine must execute approved non-study schedule inputs, log each fresh reset and protected lock probe, and bind the Unity receipt time to the exact backend request/reply. A complete normalizer must independently join private command log records and durable #72 data/audio records before emitting trial, exposure, fault, recovery and cue-retention facts. Do not derive them from a generic observation payload or engine label. The present receiver-only adapter deliberately omits those facts.

Physical crash/Wi-Fi/uplink injections, explicit operator recovery resume, headset battery/thermal capture, eight-hour multi-station overlap, native-log review, and signed G2 remain unperformed. Qualifying motion content and passing publisher timing remain separate blockers. The newest actual backend reset/lock verification is recorded in the #53/#55 derivatives dated 2026-10-05; the contemporaneous twenty-second publisher screen still fails timing. No longer run is implied by these tools or their synthetic tests.
