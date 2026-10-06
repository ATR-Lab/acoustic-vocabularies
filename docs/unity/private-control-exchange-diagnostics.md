# Failed private control exchange diagnostics

Refs #148, #81 and #82. This adds evidence for an existing refusal; it does not
change transport, admission or recovery policy.

Native A012 and B012 recorded durable reset completion and later refused a
current-health exchange. The first failure was `CONTROL_OPERATION_CANCELLED`
in `health_exchange`, about 216 ms after the phase began. That evidence did not
identify the failed probe or whether its send, receive or validation had
completed. Both runs remain incomplete, with zero audio requests; exact closed
results are retained in `historical-control-ack.validation.json`.

The new `control_health.exchange_failure` object binds the failed command or
probe request ID to fixed diagnostic fields:

- Stage (`send`, `receive`, `decode` or `validate`) and the unchanged deadline.
- Original send start, send completion, most recent receive-await start,
  first/last returned fragment, full-message and failure-observation times.
- Received fragment/byte counts and the linked timeout/lifetime token states.

The worker retains only the first failed exchange. It constructs the failure
record after an exception and serializes it through the existing failure
diagnostic. No request/response payload text or exception message is copied.
Successful exchanges perform bounded monotonic clock reads but do not call the
failure observer or add durable writes. The original send and full-receipt
timestamps still drive validation; diagnostic stamps cannot refresh them.

These are managed-observation timestamps, not kernel packet-arrival times or
qualified remote-clock measurements. A receive-stage failure with no fragment
does not distinguish server delay, transport delay and local scheduling. The
timeout token is linked to lifetime: both flags true cannot establish which
fired first. A failed diagnostic observer cannot replace the original exception
or resume the exchange.

The existing 3000 ms command deadline, 200 ms probe deadline, 250 ms queue and
exposure bounds, message size/type/UTF-8 checks and single socket owner are
unchanged. No keepalive, SSH route or retry settings change in this patch.

The focused software suite exercises blocked send/receive, partial replies,
explicit lifetime cancellation, full late replies, observer failure and a
successful fragmented exchange with unchanged receipt stamps. Exact test/build
pins are in the adjacent validation record.

Actual B013 active V1 subsequently recorded a safe unconsumed Start/Pause,
fresh preparation and explicit Resume. It entered the instruction phase but
refused the first scheduled play with `MENU_EXPOSURE_GATE`, before any audio
request or callback. The new trace retained a pending health probe in `receive`
with zero returned fragments/bytes: send-to-failure was 76.0829 ms, with both
linked timeout and lifetime token flags set. The first failure record identifies
the owner's `CONTROL_EXPLICIT_INTERRUPT` before that canceled receive. This is
owner cancellation during cleanup, not evidence of a 200 ms transport timeout.
The original exposure refusal remains primary; the trace does not identify its
failed conjunct.

The closed native process exited 0, cleanup/export succeeded and independently
reconciled byte integrity passed. The visit is incomplete with zero audio;
secondary `OPERATOR_ADAPTER_FAILED` and frame interruption records remain in the
raw evidence. Matching source/foundation/panel logs were retained in the run
manifest. This diagnostic capture does not establish a completed visit,
readability, acoustic timing or participant qualification.
