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
validation record. A fresh affected-segment native run remains necessary;
software tests do not establish acoustic onset, physical legibility, timing
qualification or a complete visit.
