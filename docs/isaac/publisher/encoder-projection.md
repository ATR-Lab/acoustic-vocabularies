# Bounded public-state projection change

Refs #54. This follow-up reduces encoding work while preserving the failed
timing gate. It does not qualify an hour, a station, headset timing or G2.

`StateEncoder` now copies the accepted pose lists and visual-state dictionary
explicitly. The existing validator still checks every finite scalar, quaternion,
boolean, object/key registry and visual value before a frame is returned. Invalid
containers are rejected before conversion, so tuples or key/value sequences do
not become newly admissible. Accepted containers are detached in both directions.
`validate_frame` constructs the immutable registry's anchor set once per frame,
rather than once for each of the 60 objects. Physics, robot-only holding, actuator
flushes, full state reads, neutral checks, cache defaults and GC are unchanged.

The unit regression compares the complete encoded bytes for 250 deterministic,
randomized 43-joint/60-object states with the original deep-copy projection and
envelope. It also checks caller/output mutation independence and rejects malformed
containers, nested values and unknown visual keys without advancing the sequence.
The actual run independently loaded the exact baseline `protocol.py` bytes and
confirmed byte-identical output from the same measured neutral readback.

## Actual short comparison

The approved Isaac image ran one isolated `--network none` container with a
private Unix WebSocket and a separate strict Python receiver. Each configuration
ran for 12 seconds, in baseline/candidate/candidate/baseline order. A verified
neutral reset preceded every phase. The production default handle cache stayed
**off**. Each protected publication retained the full actual read and neutral
check. No clock or state values were fabricated, and no catch-up frames were sent.

| Phase | Physics steps/s | Received frames | Missed deadlines | P99 period error (ms) | Maximum gap (ms) |
|---|---:|---:|---:|---:|---:|
| Unprotected baseline 1 | 60.093 | 361 | 0 | 3.804 | 37.842 |
| Unprotected candidate 1 | 60.079 | 361 | 0 | 6.234 | 40.725 |
| Unprotected candidate 2 | 60.096 | 361 | 0 | 6.224 | 41.428 |
| Unprotected baseline 2 | 59.643 | 358 | 2 | 3.127 | 41.435 |
| Protected baseline 1 | 40.396 | 243 | 118 | 23.887 | 58.903 |
| Protected candidate 1 | 41.627 | 250 | 110 | 16.057 | 436.391 |
| Protected candidate 2 | 37.310 | 224 | 136 | 23.081 | 59.813 |
| Protected baseline 2 | 36.212 | 218 | 143 | 24.871 | 61.953 |

**All eight strict timing screens failed.** The unchanged limits require no
missed deadlines, p99 absolute period error at most 3.333333 ms, no gap above
250 ms and no queue overwrite. Every frame was received and validated; all
2,376 sequences were contiguous within their phase, with no neutral or publisher
fault. The final explicit reset passed. These integrity results do not override
the timing failures.

Four encoder-only blocks on the same measured readback, outside the physics
windows, measured baseline medians 2.786/2.793 ms and candidate medians
0.952/0.956 ms. During the protected phases, combined median encoder time was
2.885 ms baseline and 1.112 ms candidate. A generation-2 GC pause of **389.889 ms**
occurred inside the first protected candidate phase; it collected 258 objects,
left none uncollectable, and coincided with the 436.391 ms publication gap.
GC remained enabled at thresholds `[700,10,10]`. Its cause is not established by
this bounded diagnostic. The candidate's protected mean encoding duration,
including the pause, was 1.933 ms; the median must not conceal that stall.

The remaining work is dominated by full actual-state readback (combined medians
18.181–20.556 ms per protected frame), two physics steps (about 5.33 ms each),
two complete robot holds (about 4.0 ms each), the two unchanged pre-step flushes
(about 2.6 ms each), and full neutral comparison (about 4.72 ms per frame).
The one full read/check per protected publication was retained. Removing it
would weaken the guard. Later baseline and candidate phases both slowed, so
these sequential measurements do not establish a causal total-loop throughput
gain or a hardware ceiling. The narrow encoder saving is insufficient to close
the 60-steps/s gap. No longer run was started.

The older 46.29-steps/s protected profile used the experimental handle cache;
comparing it directly to this default-off run would mix configurations. The
original hour failures and the separate receiver-only hour failure remain valid
evidence for their recorded sources.

## Reproduction and provenance

The [machine-readable derivative](encoder-projection-results.json) includes
source/image/scene/snapshot hashes, all eight CSV and sample hashes, raw timer
aggregates, GC totals and pauses above 1 ms. Every individual GC event remains
in the hash-bound raw phase summaries. Runtime source was `2d9eb176d51cbf08b11749411576fd579afb76f0`;
the baseline protocol was exported from `819760f`. Baseline and candidate files
are SHA-checked before comparison. The diagnostic hook was an ignored runner
overlay; its exact hash and archive hash are retained. No asset USD is published.

Export the pinned baseline file without newline conversion and pass its independently
verified SHA to `tests/isaac/publisher_projection_benchmark.py`. After the canonical
scene runner establishes its actual reset snapshot, import and call:

```python
run_projection_profile(
    adapter, layout, output / "reset-check/neutral_v1.json",
    reset_summary["reset_snapshot_sha256"], output / "projection-check",
    baseline_protocol_path, baseline_protocol_sha256, seconds=12,
)
```

Use the existing approved image and asset cache, canonical scene camera enabled
(`--capture`) to preserve the recorded scene hash, `--headless --reset-check
--reset-cycles 1 --skip-reach`, a fresh ignored output directory, and no competing
task GPU run. The helper writes strict received first/last samples, publication
CSV, per-call timers, GC events, reset journal and summary. Its process receiver
does not import Isaac or touch USD. The helper is a diagnostic, not an application
configuration switch.

Independent post-run checks reproduced every CSV analysis, validated all 16
retained received samples against the bound registry, verified receiver counts,
recomputed timer counts/medians, verified the snapshot and source/archive hashes,
and checked the final reset. The container exited 0 and was removed. The task GPU
workload was left idle, with the unrelated existing container/process preserved.

Windows verification: 243 backend tests passed with seven explicit existing
platform/dependency skips. The focused projection/schema/published-state tests
passed 57/57. After the normal CI fixture-generation step, the full repository
suite passed 291 tests with eight explicit platform/dependency skips; the first
full invocation had reported a missing generated STL fixture. All 12 schemas,
six synthetic examples and the public repository guard passed. The actual
separate receiver ran in all eight Linux phases. No dependency was installed
and no acceptance threshold changed.
