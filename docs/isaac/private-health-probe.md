# Correlated private health probes

The existing peer-checked private `/commands` WebSocket also accepts a read-only
health message. This avoids repeated HTTP connections while preserving the
current health source, timestamps and conservative freshness calculation. It is
an engineering fix for #148 and supports #81; it does not qualify timing or a
native visit. `/health` HTTP diagnostics and ordinary commands remain available.

The request has exactly these four keys. Both IDs are lowercase 32-digit hex;
the session is independently pinned and each client transaction uses a fresh ID.

```json
{"version":1,"kind":"private_health_probe","control_session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","request_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
```

The response has exactly `version`, `kind`, `control_session_id`, `request_id`,
`accepted`, `reason` and `health`. Version is integer 1 and kind is
`private_health_reply`. A successful probe has `accepted:true`, `reason:"HEALTH"`
and the unchanged `CommandQueue.health()` object. A successful probe can still
report stale or unhealthy state: it grants no exposure permission.

A well-formed probe with invalid fields returns `MALFORMED_PROBE`; a wrong pin
returns `CONTROL_SESSION_MISMATCH`; a provider with a changed session returns
`HEALTH_SESSION_CHANGED`. These replies have `accepted:false` and `health:null`.
The reply carries the server session pin and echoes only a valid request ID
(otherwise null). Provider exceptions close the connection without a fallback
status. Undecodable JSON and binary messages retain the existing malformed
private-message refusal/audit behavior; duplicate keys are never accepted.

These probes never enter the simulation-thread queue, command idempotency cache
or durable command journal, and never read USD or perform a reset. Polling the
older `private_command` named `health` would consume command IDs and is therefore
unsuitable for a heartbeat. Probe IDs need no server replay cache: each response
reads the current cached status and advances the original verification/publication
ages. Repeating an ID does not reset those ages.

The transport retains its UID or explicit loopback-peer admission, 16KiB message
limit and four-message receive queue. It processes each connection serially and
awaits each response send. Clients must serialize command/probe transactions,
match the exact session and request ID, refuse unsolicited/unknown replies, and
retain the existing 200ms probe deadline and 250ms conservative freshness bound.
Neither endpoint availability nor the reply's new sample time can erase the
reported verification/publication ages. The native client owns those unchanged
validation and interruption rules.

Focused tests exercise strict envelopes, provider failures, detached health,
aging without owner drain, and actual approved-library sockets. The socket test
sends more than 1,024 probes with a two-entry command cache, verifies no queue,
cache or command-log mutation, checks ordinary reset interleaving and HTTP
diagnostics, and covers disconnect, duplicate JSON keys, binary/oversize input,
session refusal and Unix peer credentials. Socket tests require the existing
approved `websockets` runtime; a skip is not a transport pass.

[The validation record](private-health-probe.validation.json) retains the exact
source/image hashes, the successful 26-case Linux socket/helper run and 90-case
Windows regression run. An initial expanded Linux invocation had 94 passes and
one missing `jsonschema` dependency failure; the existing Windows runtime passed
that schema test. No dependency was installed. This evidence uses synthetic
dispatcher state, not a measured live-source or native-client timing pass.
