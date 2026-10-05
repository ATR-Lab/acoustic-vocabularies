# Actual simulator support for a native engineering visit

The optional scene-runner service is explicitly `SIMULATION_TEST`, with
`participant=false` and `qualification=false` in its ready record and summary.
It combines the existing real command queue/dispatcher with the actual state
publisher on the simulation thread. Teaching mode retains the neutral robot;
no unqualified demo motion is installed. Native prerecorded content is a
separate application responsibility.

The loop services private commands at safe boundaries before and after each
actual physics step. Every existing robot hold, full neutral verification and
protected publication check remains active. It does not optimize duplicate
reads, actuator flushing, GC or deadlines. Existing throughput failures and
native health/reset timeout limits remain visible.

The caller must provide the real reset snapshot, station ID, a fresh pinned
32-hex control session ID, host relay UID, two distinct private Unix endpoints,
and a bounded 5–3600-second maximum duration. The ready record is written only
after an actual neutral frame and truthful ready health. Source/control session
IDs, scene/snapshot hashes and fresh samples are retained. Reset replies and
command logs come from the existing dispatcher; no acknowledgement is invented.

Example arguments added to the existing approved isolated scene command:

```text
--headless --capture --reset-check --reset-cycles 1 --skip-reach
--e2e-seconds 1800 --e2e-station-id station-01
--e2e-control-session-id <fresh independently pinned 32hex>
--e2e-host-uid <actual host relay UID>
--e2e-public-socket /e2e-sockets/state.sock
--e2e-private-socket /e2e-sockets/commands.sock
```

Use a fresh output directory and a host-owned permission0700 socket directory
mounted at `/e2e-sockets`. The simulator container uses `--network none` and the
existing approved image/assets. Both sockets are permission0600 and assigned to
the explicitly supplied host UID. The private listener checks actual Unix peer
credentials against that UID before HTTP or WebSocket upgrade.

After `joined-e2e/ready.json` is verified, the host's existing Python may run two
bounded `python -m isaac.e2e.relay` processes. Each accepts only a literal host
loopback listener and a permission0600 Unix socket owned by its UID. It checks
the socket identity before each connection, forwards bytes without changing
timestamps or replies, limits concurrent connections, and exits at its duration
or termination signal. It never changes host networking/firewall settings.

For the Windows diagnostic, the intended SSH-local forwards are
`127.0.0.1:18765` to remote loopback18766 for `/state`, and `127.0.0.1:18767` to
remote loopback18768 for `/commands`. `/health` stays on each corresponding
listener. Record owned process IDs and stop only those relays/forwards after the
native client closes. Unrelated containers and GPU processes must be preserved.

Leave independent source-clock evidence null when it is unavailable. The normal
Unity live source can display actual progressing frames but must continue to
refuse participant exposure/reset qualification. Any separately authorized
simulation-only native gate must remain explicitly distinct from source-clock,
headset, acoustic and study qualification. Local arrival freshness and observed
neutral parity cannot establish remote clock drift or physical timing.

Evidence includes actual first/last public frames, publisher CSV, durable reset
and private-command journals, actual physics-step measurements, and final
cleanup outcomes. The control-session identity is independently pinned before
listener startup; the command journal has its own random log-session identity,
which is recorded separately. Summary counters report frames with observed
public clients and accepted private reset/mode terminal events. They can include
diagnostic clients and cannot identify a completed native visit. The completion
field is explicitly `service_completed`; `native_visit_completed` stays false.
A clean service exit is not a completed study visit or rate
qualification. The native journal and joined application evidence must be
evaluated separately.

The [2026-10-05 startup derivative](e2e/2026-10-05-startup-evidence.json)
records the actual isolated simulator, matching canonical scene/snapshot,
three real private mode/reset acknowledgements and a five-second Windows
loopback state check. It preserves the hashes of the private raw evidence.
The native application attempt was blocked by Windows Application Control
before process creation; no native visit is demonstrated by this derivative.
The backend remains below the target publication rate. The running service
window and its eventual cleanup must be evaluated separately from this startup
evidence. A later visit uses a fresh output directory and independently pinned
control session, after the previous client and owned service are closed.
