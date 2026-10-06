# Cue refusal and first-fault diagnostics

Refs #148 and #82. This change adds failure evidence; it does not qualify the
transport or complete a native visit.

The retained A build 008 run completed grammar and five lessons, then refused
the sixth lesson before requesting its cue. DataJournal sequence 161 records
`SESSION_CUE_GATE_REFUSED`; later `CONTROL_UNAVAILABLE` follows interruption.
The adjacent frame summary has `cancelled_before_window:true`, zero frames and
null maximum interval. It is not a measured freeze. The failed readiness
conjunct was not recorded by build 008, so neither a worker timeout nor a source
stall can be inferred. The scheduled onset was about 736 ms later, outside the
engine's 150 ms minimum-lead deadline.

On readiness/deadline refusal the engine now emits one immutable snapshot of
the eight gates it already evaluated, the exact decision time, onset and
minimum lead. The joined audit writes this as module `slot_gate_refused` before
interruption can invalidate reset/focus state. Successful checks have no new
diagnostic writes or extra readiness reads. A diagnostic sink exception still
interrupts the attempt, preserves the original cause, and prevents Resume.

The private-control snapshot does not pump, freshen or grant health. It adds
the worker phase, last completed exchange's actual send/receive timestamps and
first bounded failure category/time. Queue overflow, schema/queued-age failure,
transport cancellation/socket/deadline failure and explicit owner interruption
remain distinguishable. Cancellation is not automatically called a timeout.
No exception text, private path or endpoint is serialized. An unavailable exact
reset query is null, not an invented false observation.

The joined terminal status preserves the first engine fault in that episode.
The existing strict fault envelope remains `{code}`; supplemental module
`terminal_fault_diagnostic` retains the reported secondary code and private
control snapshot. Existing session fault rows retain subsequent errors.
Explicit safe Resume begins a new episode. The 200 ms probe deadline, 250 ms
conservative freshness bound, 75 ms polling period, queue capacity, exact reset
acknowledgment and cue timing are unchanged.

## Verification

- Engine/control tests:150 passed after an explicit JSON-null correction.
- Actual host closure/audit tests:7 passed after closing the test-owned audit
  before reading it on Windows. Initial failed runs are retained separately.
- Engine, teaching and menu PlayMode lifecycle tests:9 passed, zero skips.
- The related contrast change was checked separately:4 construction/contrast
  cases and 2 placement cases passed. It is owned by PR 156.

Counts above combine the corrected focused runs; no clean 157-case aggregate
run is claimed. Exact source and result hashes are in the adjacent validation
record. Native build 009 must use the merged source and requires a new actual
run; build 008 and all its incomplete evidence remain unchanged.
