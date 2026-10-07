# Post-reset health admission

Refs #148, #81 and #82. This is a bounded engineering correction, with
participant admission still disabled in the simulation player.

Native build 009 completed both reserved grammar examples in A, then refused
the first lesson with `LESSON_CUE_REFUSED`. B refused its first menu with
`MENU_CUE_REFUSED`. Both attempts had a durable consumed cue intent but no
study audio request. The engine's readiness checks passed; the inner content
check later refused. Neither incomplete run is a completed visit.

In A, the last completed control exchange was the reset command: its full
request RTT was 125.5652 ms and the returned publisher age was 90.972948 ms.
Only 33.461852 ms of the existing conservative freshness budget remained at
receipt. In B those values were 127.9307, 93.045653 and 29.023647 ms. The
recorded terminal snapshots were after interruption, so they do not identify
the original failed conjunct. No transport timeout is inferred from them.
The producer constructs the reset reply after actual reset verification and
before the next public sample; its publisher age refers to the preceding
public sample, not the new neutral verification.

The client now retains each exact durable reset acknowledgment together with
its original local receipt time and server health sample time. Admission
requires a real correlated probe sent after that receipt, with a strictly
greater server sample timestamp. The latest observation must be a probe;
a command snapshot alone cannot grant reset readiness. Missing, duplicate,
pre-ACK, stale, wrong-session or wrong-mode observations do not grant readiness.
Probes do not reissue resets or replace the durable command history.

The 250 ms gate still includes the full measured RTT, original source ages and
elapsed local receipt age. The 200 ms probe deadline, sequential socket,
bounded queue, 75 ms cadence, source identities, exact reset IDs and all
post-persistence checks remain unchanged. This avoids using a nearly expired
command snapshot for initial admission; it cannot guarantee that a later
probe stays fresh through every subsequent operation. Any later refusal
continues to fail closed.

Teaching and menu inner cue checks now evaluate their existing readiness once
and emit `content_cue_gate_refused` only on failure, before interruption. The
supplemental module audit retains the eight observed gates, check time, onset
and non-pumping control diagnostic. The original authorization/interruption
short-circuit order is preserved. Observer failure retains the original cue
refusal and prevents further admission from that factory lease; normal cleanup
still runs. There is no successful-path diagnostic write or cached grant.

Exact software test and native build evidence is recorded in the adjacent
validation record. Successful affected-segment native completion remains necessary;
software tests do not establish acoustic onset, physical legibility, timing
qualification or a complete visit.

## Build 010 preflight observation

The first native B010 run reached `JOIN_READY_EXPLICIT_RESUME` and constructed
its menu view after the new post-reset probe gate. It later failed in preflight
before any operator Start, slot or audio request. Its closed native result
records `SESSION_PREFLIGHT_FAILED`, successful cleanup/export and incomplete
status. The candidate had not been committed, so the terminal control snapshot
was null after cancellation. The retained evidence cannot establish the
original exception category or a transport timeout. Separately observed source
publication slowdown does not prove that category.

The next diagnostic records module `preflight_failure` inside the original
`Preflight.Pump` exception path, before candidate cancellation. It includes the
bounded operation phase, original typed control/session/audio code (or a fixed
IO/unexpected category), observation time and non-pumping control snapshot.
It then rethrows the original exception. A failing diagnostic sink cannot
replace that exception; the existing staged failure and cleanup still run.
There are no new successful-path writes or health reads. This coverage is
limited to `Pump`; exceptions in separate `Ready`, explicit-resume or commit
calls outside it are not claimed to have this snapshot.

## Build 011 cancellation and refusal

The native B011 active V1 run passed the post-reset probe gate and prepared its
menu view. The normal console accepted Start and then a deliberate Pause before
the scheduled cue. The frame record reports `cancelled_before_window=true` with
zero frames; it is not a measured frame freeze. The menu ledger remained
header-only, with no `menu_interrupted` entry, and the owner began a fresh lease
for the same block. This verifies cancellation without poisoning the shared
ledger, but does not establish successful resumed menu presentation.

That fresh lease's reset exchange was sent at local monotonic 62061.5561 ms and
fully received at 62598.4520 ms: 536.8959 ms elapsed. The unchanged 250 ms health
check rejected it. The new failure-only snapshot retained the original
`CONTROL_STALE` at phase `control_pump` before cancellation, followed by the
coordinator's `SESSION_PREFLIGHT_FAILED`. The full exchange includes server
work and transport; it is not a measurement of network latency alone. The
snapshot's 774.7465 ms effective age describes the previously accepted probe,
not the rejected reset payload. No worker timeout or queue overflow is inferred.

The run closed with exit code 0, successful cleanup/export and `complete=false`.
Independent reconciliation reports intact evidence and an incomplete run. No
Resume followed the Pause, and there was no audio request, completed menu or
completed visit. The exact closed journal, result, observer and manifest hashes
are retained in the validation record. Issues #148 and #81 remain open; no
timing, acoustic or participant qualification is established.
