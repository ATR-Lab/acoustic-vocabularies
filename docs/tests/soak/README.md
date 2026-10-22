# O5.2.7 evidence preparation

The eight-hour multi-station soak has **not run**. This package contains the evidence analyzer, continuous Unity receiver monitor, native byte verifier, causal-clock collection driver, read-only host sampler, deterministic synthetic schedule driver and command/data normalizer. It is not an autonomous fault injector or signed G2 gate. The tests use explicit synthetic fixtures and make no eight-hour or hardware claim. Phase 2 development authorization does not authorize unplugging participant stations, changing Wi-Fi, killing unrelated processes or signing a gate.

Run the eventual analysis with standard Python:

```text
python -m isaac.soak.analyze ignored/run/manifest.json --output ignored/run/report.json
```

Use a fresh output path; existing reports are never overwritten. Exit 0 means `CANDIDATE_GO` for the supplied declared evidence, pending review. Any missing, synthetic, changed, short or failing evidence returns `NO_GO`; G2 remains unsigned in every output. The included template is deliberately rejected as actual evidence. Keep raw logs, station configuration and identifiers outside the public repository; only a reviewed sanitized report and retained artifact hashes may be published.

## Collection and normalization contract

Freeze an explicit inventory of every intended station, scene/snapshot hashes, receiver-detector source hash and client type before a real run. Record which stations use a justified headset-equivalent client. Prepare non-study synthetic schedules through the real Unity application, fresh reset acknowledgements before trials, and protected lock probes. The receiver monitor, observation driver, seeded schedule driver (`isaac.soak.driver`) and command/data normalizer (`isaac.soak.normalize`) are implemented and tested only against an in-process fake command server. The joined Unity host's consumption of the driver's input feed and its receipt/data observations remain integration requirements. The collection driver deliberately emits `native_collection_only` and `NO_GO`, even when its measured window completes. It never substitutes a heartbeat for a reset, lock rejection, retained response or audible cue.

Record a common `coordinator_clock_id` and each station's `coordinator_start_s`/`coordinator_end_s` in that actual coordinator clock. Their shared overlap must be at least eight hours; sequential eight-hour station runs cannot pass. Retain the coordinator's observations in the native logs and review alignment with the receiver/resource windows. Do not invent clock offsets to populate these fields.

Collect native Unity, publisher, command and host logs. The manifest hashes all four native sources and the normalized event/resource files. Each referenced path must remain within the manifest directory, and every byte hash must match. Hash binding establishes retained bytes, not the truth of a provenance declaration; reviewers must audit normalization against native sources and the recorded detector implementation.

Normalized JSONL events have contiguous integer `seq`, finite nondecreasing `t_s`, `clock_domain: "unity_monotonic"`, `source_kind: "live"` and `kind`. Command/reset acknowledgements use their Unity receipt time here; never combine unqualified host and Unity clocks. Start/end events enclose at least 28,800 seconds and the end declares `completed:true`. Start declares `monitor:"continuous_receiver_stale_and_freeze_detector"`. A detector heartbeat at least once per second proves logging coverage; it is not a substitute for continuous stale/freeze detection.

| Kind | Additional fields |
|---|---|
| `heartbeat` | `block` teaching/selection/protected/paused, `block_id`, `state_age_ms`, `frame_age_ms`, monotonic `mirrored_frames` |
| `stale_gap`, `frame_freeze` | `block`, measured `duration_ms` |
| `record_commit` | committed durable record `sha256` |
| `fault` | unique `fault_id`, `fault_type` isaac_crash/wifi_drop/uplink_disconnect, `last_committed_sha256` |
| `pause` | `fault_id` |
| `reset` | unique `reset_id`, boolean `reset_ok`, optional active `fault_id` |
| `resume` | `fault_id`, successful recovery `reset_id`, retained `last_committed_sha256`, `operator_initiated:true` |
| `lock_probe` | `block:"protected"`, `block_id`, booleans `rejected`, `logged` |
| `trial_begin` | fresh successful `reset_id` |
| `exposure` | receiver event time; forbidden during an unresolved fault |
| `cue_playback` | `cue_id`, booleans `audible`, `treated_as_unheard` |

Every protected block needs a rejected/logged probe. Failed resets outside declared active faults fail. Every station needs all three fault types, a pause, preserved committed record, verified recovery reset and explicit operator resume. Replaying an already audible cue as unheard fails. Fault injection is not an exemption from protected stale-state criteria. The analyzer conservatively treats protected frame freezes above 250 ms as failures too; this additional engineering screen is explicit and is not a secretly revised issue threshold.

Resource CSV columns are `t_s,ram_mb,ram_capacity_mb,vram_mb,vram_capacity_mb,step_ms`. Samples span eight hours with gaps no greater than 60 seconds. Host-relative resource time is separate from receiver event time. Review their run-window alignment during normalization. The analyzer reports ordinary least-squares growth and whether peak usage plus nonnegative remaining growth projects exhaustion within a 10-hour day. This is a screening assumption, not proof that memory cannot leak. Retain battery/thermal logs for real headsets; substitute clients need an explicit justification. Stream latency/jitter and clock-quality plots remain native-log review inputs; no cross-host latency is manufactured by this analyzer.

## Operator runbook and unresolved work

Before actual operation, obtain approval for the named station set, power/cooling arrangement and each physical network fault. Confirm #57 isolation and #62 receiver pause/stale behavior. Use only designated task processes for Isaac crash injection. Log planned/actual fault times, detector and console/health captures, and the last committed record before each fault. Keep study cues and human participants out of this test.

During recovery, keep exposure paused until the source restarts, the correct neutral snapshot resets successfully, and the operator resumes. Preserve audible-cue history. Collect and retain all source logs, hashes and power/thermal events. Run analysis only after every station's full window ends. Any failed criterion recommends no-go for live Isaac; the issue's recorded-trajectory/snapshot fallback needs separate validated artifacts and an apparatus amendment, never an automatic substitution.

Actual eight-hour station operation, the Unity-side input-feed and observation binding, physical fault injections, headset endurance, native-log review and signed G2 remain open. `report-template.md` intentionally contains no invented measurements. See [native collection, driver and normalization](collection.md) for the executable path and its explicit boundaries, and the [O5.2.7 runbook](../../spikes/O5.2.7-runbook.md) for the remote-host procedure.
