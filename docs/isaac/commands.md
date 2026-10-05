# Private command API and protected-test lock

Issue #55 provides the engineering API. Phase 2 development using the G1 in
Isaac Sim is authorized; methodology templates, endpoint procedures and motion
content remain subject to their own validation. These commands never enter
the public state schema or public `/state` listener.

`CommandDispatcher` starts in `test` mode. A single recursive action/target
check runs before schema dispatch and before idempotency lookup. Every `demo`,
including malformed or previously accepted teaching requests, is rejected in
test mode. A target/action hidden in an unknown nested field is also rejected.
The lock applies continuously until an explicit accepted `set_mode` leaves
test mode. The separate `post_endpoint` mode is not an assessment mode.

## Wire contract

`command.schema.json` and `reply.schema.json` define exact envelopes. Private
`/health` supplies the fresh `control_session_id`; clients include it with a
unique 32-hex `request_id` in every request. Example with synthetic identities:

```json
{"version":1,"kind":"private_command","control_session_id":"00000000000000000000000000000000","request_id":"11111111111111111111111111111111","command":"set_mode","args":{"mode":"test"}}
```

| Command | Arguments | Behavior |
| --- | --- | --- |
| `reset` / `hold_neutral` | `{}` | Cancel a demo, restore and verify neutral, acknowledge `reset_ok` after durable logging. Available in every mode. |
| `demo` | `action`, `target` | Only the 32 legal pairs; teaching/post_endpoint only. Reply after the hook completes, never on mere admission. |
| `set_mode` | `mode` | `teaching`, `test`, or `post_endpoint`. Entering test locks first, cancels active motion and verifies reset before success. |
| `pause` | `{}` | Pause at the latest yielded safe point and hold that robot pose. |
| `resume` | `{}` | Explicitly resume a paused iterator. Cannot undo `stop`. |
| `stop` | `{}` | Interrupt the demo, restore neutral, latch stopped/paused. Service restart required to run motion again. |
| `health` | `{}` | Private mode, pause/stop/fault, active demo and exposure gate. |

Tray actions are `ADD_ONE`, `REMOVE_ONE`, `FLIP_CARD`, `ALIGN_ARROW` for
`tray_A`–`tray_D`. Container actions are `SCAN`, `TAG`, `CLOSE`, `QUARANTINE` for
`container_E`–`container_H`, from the public #56 contract. `resume` and `health`
are explicit engineering extensions needed for pause recovery and mode display.
No grasp or motion is implemented by this dispatcher.

## Ordering, acknowledgement and retries

The bounded queue admits network requests without touching USD or PhysX. The
simulation thread drains it before advancing motion. `stop` and test-mode
transitions have priority over queued demos; each queued demo still passes the
current lock when dispatched. A completed test-lock transition also supersedes
every queued demo, resume, or mode-unlock intent admitted before its
acknowledgement. Otherwise an older queued teaching-mode command could undo the
priority lock later in the same drain batch. Superseded request IDs are retained
as rejected; a new explicit intent must use a new ID after the acknowledgement.
One generator yield is one safe interrupt point.
Hooks must not perform long blocking work between yields. Closing a generator
is followed by reset when entering test or stopping, so a pending teaching
motion cannot execute after the lock acknowledgement.

A successful reply is sent only after the terminal event is flushed/fsynced.
A replay of the identical request ID/body never executes again; its reply has
`duplicate=true`, the original outcome, and current mode/health. In-progress
retries receive `REQUEST_IN_PROGRESS`; the caller may retry the same request
later. A changed body with an existing ID receives `REQUEST_ID_CONFLICT`.
The protected lock is checked before all cached outcomes.

Request IDs are scoped to a fresh private control session. A restarted process
rejects old session IDs. The cache never evicts an ID and then re-executes an
old retry: reaching its configured capacity faults/pauses admission until an
explicit service restart. Select the capacity for the visit length. Queue
saturation is rejected as `COMMAND_QUEUE_FULL` and logged without scene access.
Disconnected clients do not cancel already admitted commands or their logs.

`reset_ok` reports only that reset attempt. A duplicate old reset reply is not
a new verification; callers must also check current health/exposure state.
Reset never claims to recover a latched #54 publisher fault. Public stream
recovery currently requires an explicit service restart.
`health.exposure_ready` additionally requires a fresh, healthy attached publisher
and a successful full neutral verification completed within 250 ms. Full
verification belongs to the protected simulation loop/reset; health only reads
its timestamped result, avoiding repeated USD traversal. Cached network health
ages both verification and publication even if the simulation thread stops.
With no publisher attached the gate stays false. This is a backend gate;
client/source-clock and headset freshness checks remain necessary in the session
engine.

## Simulation loop and neutral holding

Construct `CommandDispatcher(reset_manager, durable_log, station_id=...,
allowed_client=..., demo_factory=..., hold_robot=make_robot_hold(adapter),
publisher=...)`, then `CommandQueue(dispatcher)`. The simulation loop order is:

1. `handoff.drain()` validates queued commands and advances one demo safe point.
2. Advance physics and refresh the articulation at the normal fixed step.
3. `dispatcher.after_physics_step()` explicitly holds only the robot root and
   joints when neutral/test/stopped or paused, with zero velocity and a forward
   kinematic refresh.
4. Acquire the complete state, verify protected neutral, and publish via #54.

This robot-only kinematic hold is a deliberate policy, not an automatic reset
retry. Physics/simulation time still advances. Object, visual, material and
light drift is not overwritten: it causes `NEUTRAL_DIVERGED`, closes the reset
exposure gate and latches the command fault. A logged reset can restore the
scene, but does not clear that service fault or the publisher fault. A later
explicit service restart is required. A missing hold callback fails closed.

## Private listener, logging and shutdown

`PrivateCommandTransport` uses the already approved `websockets` library. It
has a separate `/commands` WebSocket and private `/health` HTTP endpoint.
TCP diagnostics require an explicit loopback bind and configured exact client
IP; unknown peers are refused before upgrade and durably logged. Unix sockets
have mode `0600` and verify Linux peer UID against the configured client.
There is no wildcard bind, discovery or host-network modification. Deployment
on the isolated station network belongs to #57. Public `/state` is absent from
this service, and commands are absent from the public service.

`DurableCommandLog` exclusively creates an append-only JSONL with the ADR-007
common envelope and engineering schema extension 0.3.1. Each received text
command, including a rejected or duplicate request, gets exactly one terminal
event containing full raw text, arguments, mode, station, host monotonic and
simulation time, outcome/reason and `reset_ok` where relevant. Therefore a
logical request retried twice has one execution and two terminal log events.
Malformed decoded values are retained for diagnosis; raw text is authoritative.
Production sinks must be thread-safe because handshake/admission refusals are
logged by the network worker using cached health, without simulator access.
Private logs must remain outside public version control.

The owner simulation thread calls transport `close()`: pending commands are
rejected/logged, any active iterator is interrupted, the listener stops and only
its own Unix socket inode is removed. Preserve incomplete output on failure.

## Evidence and remaining work

`tests/isaac/test_command_lock.py` covers every command in every mode, all 32
legal pairs, malformed/unknown/nested commands, peer denial, lock-before-replay,
request conflicts and restart identity, pause/resume/stop, priority lock
transition, reset failure, robot-only hold and unhidden object drift.
`test_command_transport.py` exercises the actual approved WebSocket library on
Linux with a synthetic backend, including peer refusal and Unix permissions.

`run_command_check` in `isaac.commands.benchmark` tests the lock against the
actual scene, checks exact before/after state hashes for 32 protected rejects,
advances physics with neutral hold, and injects object drift. Its teaching hook
is explicitly synthetic and performs no motion. #56 must supply and validate
the real 32 execution iterators. Private methodology/log-template reconciliation,
independent lock review and full station deployment remain pending.

The [actual-state diagnostic](commands/actual-state-diagnostic.json) used the
loaded G1 and all 60 workcell objects. All 32 protected requests were rejected
with an identical full-state SHA before/after; 240 advancing physics steps
passed neutral verification under explicit robot-only hold. An injected card
change caused a fault and was not silently restored. The explicit recovery
reset passed while the service fault stayed latched. All 68 terminal events and
their raw-log SHA were independently checked. Teaching completions in this run
used a synthetic no-motion hook. The final corrected-label run used commands
`825e628`, reset `f319440` and scene `8802e2e`, including the queue-boundary and
cached-health implementation. Its 68 log events use schema 0.3.1; verified raw
log SHA-256 is `c14dacc3d292f770db78853dc71984fc2151cd4a05113bde0fa5d048f9236cdb`.
The test does not establish full publisher-bound exposure readiness: that needs
fresh attached publisher health, independently qualified source clocks and the
downstream client/session gate. Actual #56 motion content remains pending.

### Publication after protected rejection

`isaac.commands.published_benchmark.run_published_command_check` closes the
separate diagnostic gap between adapter readback and the actual public stream.
The optional scene-runner flag `--published-command-check` requires
`--reset-check`; use `--capture` to retain the same camera-bearing scene hash as
the canonical workcell. It creates the real protected `StatePublisher`, attaches
it to the dispatcher, and receives each payload through the actual Unix
WebSocket transport. No demo implementation hook is supplied.

The diagnostic publishes a baseline, rejects each of the 32 legal target-bearing
commands in test mode, checks the full state hash before/after each rejection,
advances two actual physics steps with the unchanged robot-only hold, and
independently compares the received 43 joints and every public pose, visibility,
enabled flag and discrete visual field of all 60 objects with pinned neutral.
Received sequence/payload hashes and complete private request/reply rows are
retained. A real card-state mutation must produce `NEUTRAL_DIVERGED`, suppress
publication and remain visibly changed until an explicit reset. A scheduler
skip alone cannot pass that suppression check.

This bounded diagnostic proves publication behavior after rejected commands.
It does not qualify rate, cross-host clocks, headset/session exposure readiness,
or #56 motion content. Its raw private files remain ignored; publish only a
reviewed numeric derivative and artifact hashes.

The [2026-10-05 canonical-scene result](commands/published-neutral-reverification-20261005.json)
passed all 32 protected rejections and received 33 real public frames (one
baseline plus one after each rejection), with zero sequence gaps and zero
measured joint/position/orientation deviation. All 60 public object records
matched neutral, and every immediate full-state before/after hash was identical.
The run advanced 67 physics steps. Actual card drift suppressed publication
with `NEUTRAL_DIVERGED`; the explicit reset recovered neutral. The 34 command
terminal records, 33 frames, per-rejection payload hashes and artifact hashes
were independently checked after download. Scene `3b6e8f9a…` and snapshot
`e2628102…` match the canonical camera-bearing workcell. The 5.919-second
diagnostic is not a throughput or exposure-readiness qualification.
