# Durable control completion and current exposure health

Refs #148, #81 and #82. This follow-up separates historical command completion
from current exposure admission. It does not widen the 250 ms exposure limit
or enable participant admission in the simulation player.

Native B011 completed an initial post-reset preflight and deliberately paused
before its first menu cue. The shared ledger remained header-only and a fresh
lease began. Its next reset transaction took 536.8959 ms. The earlier client
rejected that reply in the 250 ms health parser before retaining completion;
the failure and incomplete native result remain preserved in
`post-reset-health.validation.json`.

Backend inspection separately found that actual reset verification preceded
reply construction and durable writes. Disk synchronization can delay a valid
command reply. This does not make its embedded health snapshot current, and
the measured transaction is not network-only latency.

The updated client uses the same strict closed health parser for both paths:

- A command reply retains its original 3000 ms transaction deadline, exact
  pending request ID, required mode, reason, reset result, pinned session,
  scalar types and nonregressing server sample. Unexpected `duplicate=true`
  replies are refused because this client never retries. The original reply
  must be durably written before its completion enters history.
- Recording completion advances the server sample floor and invalidates the
  prior grant. It never supplies fresh exposure ages. Request persistence and
  all pending commands also block admission, including when a real probe is
  already queued. A failed request or reply write latches failure.
- Admission requires an actual correlated probe sent after the latest command
  receipt and sampled strictly later on the server. Each reset additionally
  retains its own exact completion identity. The probe keeps its original
  send/receipt timestamps, 200 ms transport deadline, 250 ms queue limit and
  250 ms combined RTT/source/receipt-age bound. No reset is automatically
  retried, and no stale timestamp is renewed by a Unity read or durable write.

`ModeAcknowledged` remains historical completion for construction and issuing
the next reset. Exposure consumers also require current hold health and their
exact reset/renderer gates. A mode acknowledgment alone cannot authorize audio.

The focused queue regression uses B011's measured transaction length and
reported ages with controlled synthetic host sample anchors. It is software
evidence, not a replay of an actual backend transaction. Tests also cover
multiple pending resets, probes arriving during persistence, expired queues,
replay, unexpected duplicates, malformed payloads, host regression, sink
failure and exact command/probe deadline boundaries.

Exact test and build pins are in the adjacent validation record. Build012's
native affected-segment and complete-visit results are not inferred from unit
tests or compilation. Previous native failures remain unchanged.
