# Public state queue processing clock

Refs #148, #62 and #82.

The public-state receiver used one caller timestamp throughout a moving queue.
An arrival enqueued while an earlier frame was parsed could have a receipt time
later than that timestamp and be falsely rejected as `STATE_QUEUED_TOO_LONG`.
Conversely, a frame that became more than 250 ms old during parsing could be
accepted against the earlier timestamp. Two deterministic tests reproduced both
behaviors before the correction; their failed results are retained.

`LiveSocketClient.Pump` now reads the same monotonic clock after parsing and
before delivery to the state source. Original worker receipt times remain
unchanged. The pump processes at most eight arrivals, preserves FIFO/epoch,
echo-correlation, fault and overflow handling, and returns the actual final
processing time. `StateSourceHost` uses that returned time for rendering and
explicit reset confirmation. Regressing/nonfinite processing clocks are
refused; real future receipt stamps and queues older than 250 ms remain refused.
No timestamp is clamped to fit a gate.

There is no automatic renderer reconfirmation during an active menu, new reset,
queue-age extension, remote-clock qualification or change to the private
control deadlines. A genuine source invalidation still revokes the boundary
grant and requires the existing explicit recovery path.

Actual B013 had an initial local reset confirmation at 40.2557597 s, then
`STATE_QUEUED_TOO_LONG` at 41.0840095 s and several later times during the
instruction phase. The first scheduled play was refused with
`MENU_EXPOSURE_GATE`; zero audio was requested. Those source events can revoke
the retained renderer grant. The rejected arrival timestamp was not recorded,
so the native event cannot independently distinguish this race from genuine
queue expiry. The exact race and parse-expiry defects are demonstrated by the
controlled tests, not inferred as a proven native root cause.

Validation is 52/52 EditMode cases (14 queue cases and 38 existing source cases)
and 1/1 existing PlayMode host-lifecycle case, with no skips. Tests cover arrival
during parsing, a newer receipt already queued at pump entry, real expiry,
clock rejection, parser/observer delays, bounded draining, epochs, faults,
overflow and echo correlation. The clean Windows build014 inventory contains
205 independently rehashed files. Exact pins and the retained failing baseline
are in `public-state-processing-clock.validation.json`.

Actual B014 active V1 prepared its menu view at 15.2021289 s but failed preflight
at 37.9826174 s before any Start/Pause or audio request. The retained private
probe failed in receive after 216.3951 ms with zero fragments/bytes, timeout
token canceled and lifetime token not canceled. This is a distinct current
private-probe deadline failure; the queue-clock correction does not relax it.
The process exited 0 with cleanup/export and independent byte-integrity checks
passing; the visit remains incomplete.

Between preparation and that failure, the source journal contains no
`STATE_QUEUED_TOO_LONG` or `HOST_CLOCK_REGRESSED`. This is a bounded observation,
not a whole-run absence claim: six old-queue refusals and one overflow occurred
during initial startup, before preparation. Seven stale/recovered pairs remain
in the prepared window. There is no completed menu, visit, physical readability,
timing or participant qualification from this run.
