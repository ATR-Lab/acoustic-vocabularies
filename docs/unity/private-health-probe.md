# Private health probe transport

The Unity client sends health probes through its existing provisioned loopback
`/commands` WebSocket. A probe is a transport observation, separate from the
durable mode/reset command protocol. It does not request a reset, change mode,
enter the command cache, or create an admission authority.

Requests contain exactly `version` (integer 1), `kind` (`private_health_probe`),
`control_session_id` (the independently provisioned session), and `request_id`
(a fresh lowercase 32-hex GUID). Responses contain exactly `version` (integer 1),
`kind` (`private_health_reply`), `control_session_id`, `request_id`, `accepted`,
`reason`, and `health`.

Only the current request ID, pinned session, `accepted:true`, `reason:HEALTH`,
and an object health payload can reach the existing strict `ControlHealthGate`.
Rejection reasons are `MALFORMED_PROBE`, `CONTROL_SESSION_MISMATCH`, or
`HEALTH_SESSION_CHANGED`, with null health. Every rejection fails the client;
an unknown or malformed response also fails. A backend may return an accepted
observation whose health is stale or unhealthy. Acceptance of the transaction
does not imply readiness.

The worker performs at most one command, then one probe, sequentially. A command
retains its 3-second transport deadline and exact durable acknowledgment checks.
Each probe has a 200 ms deadline spanning send and all response fragments; even
a delayed cancellation cannot admit an observed elapsed time over that deadline.
Responses must be text, strict UTF-8, at most 16 KiB, with no duplicate JSON keys
or extra envelope fields. The receive queue remains bounded at eight entries.
Failure has no fallback to HTTP or retained stale state.

Both send and complete-receive timestamps are measured in the worker. Pumping
does not renew either timestamp. The existing gate requires progressing source
health and computes receipt age plus full request RTT plus the larger source
age, bounded by 250 ms. The sequential target period remains 75 ms including
transaction duration, with at least 1 ms yield. Reset acknowledgment history,
continuous source confirmation, and all participant qualification gates remain
unchanged.

Standalone installed-Mono diagnostics reproduced slow HTTP response headers:
20 requests had median 123.692 ms RTT, while median headers-to-body completion
was 0.041 ms and final data-to-EOF was 0.009 ms. This was the installed 32-bit
Mono runtime with the player HTTP assembly, without Unity frame or journal
workload; an earlier first-request timeout is retained separately. It is a
transport diagnostic, not native timing qualification. Prior native build007
completed one reserved chime and then refused the health bound during the second
request. A new native run must independently validate this WebSocket transport.
