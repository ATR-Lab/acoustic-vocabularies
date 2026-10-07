# Detached robot target for neutral holding

Refs #52, #54, #148. `CommandDispatcher.after_physics_step` previously obtained
its neutral robot target from `reset_manager.neutral_state["robot"]`. The public
getter deep-copies the entire snapshot state: robot, 60 objects, observed frames
and environment. The hold discarded everything except the robot on each step.

`ResetManager.neutral_robot_state` now returns a fresh deep copy of just the
snapshot robot. The dispatcher uses that accessor for the existing neutral hold
branch. The public full-state accessor and full reset are unchanged. There is no
cache or shared mutable robot target. Paused non-neutral motion retains its
existing paused target. Owner-thread enforcement, mode decisions, physics,
kinematic hold, full actual readback, neutral comparison, capture proof and fault
handling are unchanged. No snapshot, protocol, threshold or GC policy changes.

The 172 focused Python tests passed with no failures or skips, including 19 new
cases. They cover nested output/input mutation isolation, full restore, every
neutral/paused/stopped branch, non-holding branches, hold/read failures, object/
frame/environment drift, capture forwarding and wrong-thread refusal. Existing
reset, command-lock, same-iteration proof and E2E service tests also passed.

## Offline measurement

A copy-only benchmark loaded the independently pinned recorded neutral snapshot
`e2628102a9a85dfc7566052297e0b038cdf4aa4025319d0e868425cecbb1a80e`
(43 joints, 60 objects), using the normal strict snapshot loader and ResetManager.
It compared the unchanged full-state getter followed by indexing against the new
robot getter. Both returned identical robot values. Five fixed ABBA cycles of
200 copies per block gave 2,000 copies per method on Windows Python 3.12.14.

| Copy operation | Median | P95 | Maximum |
|---|---:|---:|---:|
| Full state, then select robot | 0.802850 ms | 1.182900 ms | 4.026700 ms |
| Robot only | 0.030900 ms | 0.048100 ms | 0.164000 ms |

The median of this isolated operation was about 26 times smaller. A separate
20-copy `tracemalloc` observation measured median incremental peak traced memory
of 97,648 bytes versus 2,512 bytes. Those are transient traced allocation peaks,
not total allocated bytes, RSS or a measurement of native memory pressure.
GC stayed enabled at thresholds `[700,10,10]`; no GC collections occurred within
the timed blocks. This does not establish an improvement to GC pauses.

No Isaac, GPU, physics, network or native player ran for this measurement. It
does not establish total-loop throughput, transport reliability, the 250 ms
freshness requirement, a completed visit or participant qualification. All prior
timing failures remain unchanged. Runtime/harness source was clean commit
`1962d201c8699f3a9cdc9f32107593079dc21dbd`. The
[sanitized result](neutral-robot-copy-results.json) retains every block summary,
source/snapshot hashes, original private report hash and test XML hash.
Snapshot, report, XML and `source_sha256` pins are SHA-256 of exact raw file
bytes. The separately named `git_blob_sha256` values hash Git blob content at
the recorded source commit, so checkout line-ending conversion cannot be mistaken
for different source. The commit identifier itself is a Git object identifier.

Reproduce from clean committed source with an existing recorded snapshot and a
fresh private output path; this command does not launch a simulator:

```text
python -m isaac.reset.neutral_copy_benchmark --snapshot <private-neutral.json> --sha256 <independent-raw-pin> --out <fresh-private-report.json>
python -m pytest tests/isaac/test_neutral_robot_hold.py tests/isaac/test_reset.py tests/isaac/test_command_lock.py tests/isaac/test_same_iteration.py tests/isaac/test_e2e_service.py
```

## Actual joined-source comparison

A separate actual G1 screen compared baseline `a90f651` with candidate
`8c30ae7` in four fresh processes, A/B/B/A, requesting 40 seconds each. Only the
two exercised runtime files for the robot-copy change differed. The image,
scene, snapshot, camera setup, complete hold/readback, neutral checks, physics,
durable telemetry and GC policy were retained. The handle cache, same-iteration
experiment and timing observer were off. No native player, public receiver or
private control client ran.

| Phase | Full-service physics steps/s | Maximum publication gap (ms) | Gaps >250 ms |
|---|---:|---:|---:|
| A1, full copy | 15.775 | 379.705 | 1 |
| B1, robot only | 16.816 | 451.626 | 1 |
| B2, robot only | 16.848 | 432.386 | 1 |
| A2, full copy | 15.487 | 400.015 | 1 |

The copy change improved the observed total rate modestly; all four complete
timing screens still failed. Every process exited 0 and retained its initial
and final 43-joint/60-object projection; those projections agreed exactly
across the four runs. This is not a full-state or client-delivery equality claim.

The predeclared secondary window starts 10 seconds after the first retained
publication and ends at the last actual publication, rather than claiming an
exact 30-second exposure. Its measured rates were 15.932, 17.016, 17.034 and
15.502 steps/s, with no >250 ms gap in those shorter windows. Every full-run
large gap occurred before that window and remains a failure. No GC observer
was enabled, so this screen cannot assign those stalls to GC. Four short,
ordered processes do not establish long-run reliability or a completed visit.

The [source-screen result](neutral-robot-copy-source-results.json) retains both
complete and post-warm-up measurements, source/image/archive pins, all raw
artifact hashes and cleanup. Task-owned containers were removed; unrelated
workloads were preserved. The required 60 physics steps/s and 30 public frames/s
remain unmet, and no longer qualification run is justified by this change alone.
