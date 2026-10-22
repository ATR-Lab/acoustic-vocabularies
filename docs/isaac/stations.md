# Station isolation development (#57)

This change provides a strict private station manifest, namespace checks, a
supervised one-station service entrypoint, a per-station Unix gateway,
reviewable start/stop/restart unit text and a continuous cross-talk trace. It
does **not** claim that four or six participant stations have been provisioned,
run for an hour, or reviewed for network isolation. The
single-process-per-station model is a development proposal while ADR-003 and
hardware capacity remain unresolved. No host firewall, switch/AP, route, user
account, systemd unit or physical-robot network was changed.

## Status

| Item | State |
| --- | --- |
| Strict config, fleet uniqueness, CycloneDDS template, namespace route check | Implemented; pure tests |
| Supervised entrypoint `isaac/stations/service.py` | Implemented, **unvalidated**: ordering, refusal and journal logic tested with fakes; never run in Isaac |
| Per-station Unix gateway `isaac/stations/gateway.py` | Implemented, **unvalidated** on a station host: relay, mode and `SO_PEERCRED` refusal tested on a WSL Ubuntu Python, not the approved image |
| Start/stop/restart units `isaac/stations/units.py` | Generated review text only; **not installed** anywhere |
| Continuous cross-talk trace `isaac/stations/trace.py`, live CLI `isaac/stations/live.py` | Implemented, **unvalidated**: synthetic threaded stations only; live adapters tested with fake connections |
| Station accounts/UIDs, fixed IPs, firewall/switch review | Open: operator and network owner |
| One-hour N-station run, VRAM/step-time CSV, packet capture, restart-to-neutral, network sign-off | Open: remote procedure below, not performed |

## Private configuration and restart identity

`isaac.stations.config` requires closed versioned fields for logical station and
host, GPU index, distinct public/private ports, DDS/ROS domain, allowed loopback
client and provisioned Unix UID, scene/layout/neutral hashes, a full source
revision, a pinned image digest, publisher rate and namespace policy. Configs
use canonical UTF-8 JSON and LF; JSON is also the supported YAML 1.2 subset if an
operator chooses a `.yaml` suffix. Loading checks the configured raw SHA-256.
Do not silently rebuild a config after a mismatch or recapture a snapshot.

A fleet rejects duplicate identities, host ports, domains or gateway UIDs.
Separate ports alone are insufficient authentication: a same-UID process can
otherwise access multiple mode 0600 sockets. Each station therefore needs its
own operator-created account and UID. **This code never creates accounts.** A
headset/Unity registry must still require its configured station, scene and
snapshot identity; a private control-session ID is an idempotency epoch and must
not be described as a secret credential.

Validate a private manifest containing basenames and expected SHA-256 values:

```text
python -m isaac.stations --manifest <private-directory>/fleet.json
```

The command reads files and reports count/hashes only. It starts no services.
The example under `apparatus/stations/` uses deliberately synthetic hashes and
logical identifiers; it is a fixture, not a deployment manifest.

## Supervised station entrypoint

`isaac/stations/service.py` is the long-running main for exactly one station.
Before Isaac, a socket or the journal is touched, `preflight` loads the config
against `STATION_CONFIG_SHA256`, requires the derived `CYCLONEDDS_URI`,
`ROS_LOCALHOST_ONLY=1` and `ROS_DOMAIN_ID` (absent when ROS is unused), the
provisioned station UID as effective UID, a loopback-only namespace, the pinned
clean source revision (`git -c safe.directory=... rev-parse`, no git config
change), the pinned layout hash and the raw neutral-snapshot hash. Any mismatch
exits with code 2 and starts nothing.

Components start in order `isaac_app`, `scene` (built scene hash must equal the
pin; initial reset must pass), `publisher`, `commands`, `gateway`, and close in
exact reverse order of those actually started. A failed start closes only its
predecessors. SIGTERM/SIGINT only set a flag; the owner thread finishes the
current 60 Hz step and then shuts down. A private `stop` command also ends the
service (exit 0). Faults and cleanup errors exit 1. The service never restarts
itself: a latched fault needs an explicit operator restart.

`isaac.stations.lifecycle` keeps one append-only, fsynced, hash-chained
`lifecycle.jsonl` per station output directory with `starting`,
`component_started`, `inventory` (container PID, Isaac build, read-only
`nvidia-smi` GPU/driver/VRAM capacity), `started`, `ready` (control/public
session IDs, registry hash, sockets), `gateway_refused`, `stop_requested`,
`component_stopped`, `stopped` and `start_refused` records. Each start reads and
verifies the whole chain first. A journal for another station, a changed config
hash, a broken link or a partial final line refuses the start; a changed config
needs a new output directory (a new apparatus version). A previous run without
`stopped` is reported as `previous_stop_recorded=false`. Removing complete
trailing records is not detectable from the file alone, so copy the journal
SHA-256 into the private evidence manifest after each stop. Per-run evidence
(`workcell.usda`, `initial-reset.json`, `registry.json`, `publish.csv`,
`commands.jsonl`, `reset-events.jsonl`, `physics-steps.jsonl`) is created
exclusively under `runs/<run_id>/`.

The Isaac wiring in `isaac/stations/runtime.py` mirrors the validated bounded
paths (`run_scene` scene construction and `run_joined_service` service loop). It
has not been executed in Isaac by this change.

## Per-station Unix gateway

The host runtime directory `/run/acoustic-vocab/<station_id>` (mode 0700, owned
by the station UID) is bind-mounted at `/run/station`. The publisher and command
transports bind `internal/state.sock` and `internal/commands.sock` inside a 0700
`internal/` directory. The gateway exposes `state.sock` and `commands.sock`
(mode 0600, owned by the station UID) and relays bytes unmodified only when the
Linux `SO_PEERCRED` UID equals the station UID. Root and every other UID are
refused and journaled as `gateway_refused`; mode 0600 alone does not stop root.
Peer credentials fail closed where unavailable. Any pre-existing endpoint
refuses the start; stale sockets are removed only by the unit's `ExecStopPost`.
The command transport keeps its own peer-UID check behind the gateway.

The gateway authenticates local Unix peers. Remote headset traffic still needs
the reviewed relay and network policy; a loopback TCP relay
(`isaac.e2e.relay`, run as the station account) is reachable by any local user
and must stay on an approved, single-purpose host.

## Containment, launch plans and service units

`container_plan` returns an argv array for review. It pins the image, GPU and
configuration hash, mounts source/config read-only, and selects `--network none`,
`--ipc private`, dropped capabilities, no new privileges and an explicit
non-root station UID. It never executes Docker or publishes a port.

`isaac.stations.units.service_plan` extends that plan for the supervised
entrypoint with `--init`, `--stop-signal SIGTERM`, `--stop-timeout`, the
read-only neutral snapshot, the per-station runtime mount and exactly the
reviewed read-only `/assets`, `/lab` and `/unitree` mounts. Host paths must be
absolute without whitespace, commas, quotes or traversal. `render` and
`python -m isaac.stations.units` generate, into a fresh directory:

- `av-station-<id>.service`: `ExecStartPre` checks the pinned hashes with
  `sha256sum --check --strict` and creates the runtime directory with the
  station owner; `ExecStart` runs the exact plan (systemd-quoted, including
  `%`/`$` escaping); `ExecStop` runs `docker stop --time <timeout>`;
  `ExecStopPost` removes only the four known socket paths; `Restart=no`.
- `<id>.sha256`, `<id>.plan.json` (with `plan_sha256`) and
  `units-manifest.json` (file hashes, `installed=false`, `executed=false`).

`systemctl start`, `stop` and `restart` then map to a new run with a journaled
restart identity. Nothing in this repository installs, enables or starts a
unit. Whether SIGTERM reaches the Python process through `/isaac-sim/python.sh`
under `--init` is unverified; a missing `stopped` record after `systemctl stop`
means it does not, and the units must not be used until that is fixed.

Docker's [none network driver](https://docs.docker.com/engine/network/drivers/none/)
isolates the container network stack. Each station also has private IPC, so a
ROS sidecar may share only its own station's namespace, never another station's
or the host's. Unitree DDS stays disabled in this development path. A generated
CycloneDDS XML template selects `lo`, disables multicast and limits discovery
to 127.0.0.1; it is passed as `CYCLONEDDS_URI`. This follows the
[CycloneDDS 0.10.5 interface configuration](https://cyclonedds.io/docs/cyclonedds/0.10.5/config/cyclonedds_specifics.html).
The deployed middleware's exact version and XML acceptance still require a
runtime check; no DDS packet measurement is inferred from a generated file.

`check_namespace` reads interface/route state and rejects non-loopback interfaces
or usable routes. A read-only check in the approved network-none container found
Linux's two unreachable IPv6 default entries (`RTF_REJECT`) on `lo`; the validator
records those separately instead of mistaking them for usable default routes.
The actual allowed IPv6 host route is `::1/128`; IPv4 has no routes. This check is
namespace evidence, not switch/AP firewall sign-off or a packet capture.

Before any station-network deployment, the network owner must provide fixed IPs,
gateway mappings and a reviewed firewall/switch policy. Do not infer zero
physical robot traffic from an unobserved interface or from a port-range
assumption.

## Cross-talk and wrong-station harness

`run_cross_talk(configurations, clients, durable_event_sink)` accepts explicit
provisioned client adapters. Each has a strict `PublicRegistry`,
`public_state()` and synchronous `command(name,args)` returning the terminal
reply. The test resets every station, binds every received frame to the expected
station/scene/neutral identity, and executes every command type plus all 32 demos
on one station at a time. Before and after each command it hashes every other
station's complete public joints/objects. Any change stops the test immediately
and preserves the failed event without a cleanup reset that could conceal it.
These endpoint snapshots cannot see an excursion that returns before the reply.

### Continuous trace

With `trace_samplers` and `trace_policy`, every station also gets its own
sampler thread and its own public connection, separate from the command client.
At the declared `sample_hz` each fresh frame (new publisher `seq`; stale repeats
do not count) is validated and compared with that station's post-reset baseline.
Each command window is `[send, reply + settle_s]`; for every non-target station
the window is evaluated once a sample after its end has arrived.

The policy is exact and hashed (`trace_policy_sha256`); there are no defaults:

| Field | Meaning |
| --- | --- |
| `sample_hz`, `max_gap_s` | Declared rate; an interval between consecutive fresh samples longer than `max_gap_s` is uncovered time |
| `min_coverage` | Required covered fraction of **every** window/station pair, with bracketing samples on both sides |
| `settle_s` | Post-reply time included in the window; must exceed publication latency plus one publisher period |
| `joint_tolerance_rad`, `position_tolerance_m`, `rotation_tolerance_rad`, `visual_scalar_tolerance` | Declared physical tolerance (maximum absolute joint, Euclidean position, quaternion angle, lid/arrow scalar) |
| `rationale` | Written source of the numbers, e.g. an idle-station jitter measurement made before the audit |

Visibility, enabled, card face, tag and location changes are never tolerated.
An excursion shorter than `max_gap_s` can be missed; the report states this as
`undetectable_excursion_max_s`. A tolerance exceedance stops the audit and keeps
the failed event. Sampler errors, a publisher session change (restart) or any
window below `min_coverage` make `trace_passed=false`. With a trace,
`passed = snapshot_contract_passed and trace_passed` for synthetic and live
sources alike; without one, live reports keep `passed=false`. Packet capture,
connection authentication and the one-hour run stay separate flags that this
harness never sets.

The wrong-station test intentionally validates another station's full frame
against the intended registry. It proves application-level identity refusal,
not socket-level authentication. The report makes that distinction explicit.
Gateway refusal, namespace/process separation and physical routing need their
own deployment evidence.

## Remote-host validation procedure (not performed)

None of the following has been done. Run it on the approved station host(s)
with N per ADR-003, keep raw records private and publish only counts, hashes and
derived numbers.

1. **Accounts and directories (operator).** Create one system account per
   station with the UID recorded in its config, e.g.
   `useradd --system --uid <uid> --no-create-home --shell /usr/sbin/nologin av-<id>`,
   and a private evidence directory per station owned by that UID, mode 0700.
2. **Pinned configs.** Write each canonical config, record raw SHA-256 values in
   a private fleet manifest and run `python -m isaac.stations --manifest ...`.
3. **Units.** For each station run `python -m isaac.stations.units` (see module
   docstring) into a fresh directory. An independent reviewer compares the
   `.service`, `.sha256` and `.plan.json` files and the manifest hashes. The
   operator copies the `.sha256` file to `/etc/acoustic-vocab/stations/`, the
   unit to `/etc/systemd/system/`, then `systemctl daemon-reload`.
4. **Start N stations.** `systemctl start av-station-<id>.service` for each.
   Every journal must show `inventory` (GPU name, UUID, driver, VRAM capacity)
   and `ready`. Record config/plan/unit hashes with GPU and driver per station.
5. **Signal, restart and neutral check.** `systemctl restart` one station. The
   previous run must end with `stopped` (`signal=SIGTERM`, `clean=true`,
   `shutdown_order` gateway→commands→publisher→scene→isaac_app). The new run must
   show `restart=true` with the previous run ID, `initial-reset.json`
   `reset_ok=true` and `ready`. A missing `stopped` record fails this step.
6. **Gateway refusal.** As another station's account, connecting to
   `/run/acoustic-vocab/<id>/commands.sock` must fail with permission denied.
   As root, a connection must be closed and journaled as `gateway_refused` with
   `peer_uid=0`. Record both outcomes.
7. **Resources (one hour, full load).** With all N stations running, for each
   station run the read-only sampler inside its container so PIDs match the step
   trace's PID namespace:
   `docker exec --user <uid> av-<id> /isaac-sim/python.sh -m isaac.soak.host --pid <container_pid from inventory> --gpu-index 0 --steps /results/runs/<run_id>/physics-steps.jsonl --output /results/resources-<run_id> --seconds 3600 --source-kind isaac_runtime`.
   The `resources.csv` per station is the VRAM and step-time CSV. Compare step
   intervals with the budget approved under ADR-003; this document sets none.
8. **Packet capture (authorized by the network owner).** For the whole hour,
   capture on every relevant egress interface of each station host and on the
   switch/AP mirror port, e.g. `tcpdump -i <iface> -w <private>.pcap`. Report per
   interface the total packet count and the RTPS/DDS count, e.g.
   `tshark -r <file> -Y rtps | wc -l`, plus any packet to a physical-robot
   network. An unobserved interface is reported as not captured, never as zero.
9. **Cross-talk with continuous trace (during the hour).** As each station
   account, start bounded loopback relays (`python -m isaac.e2e.relay --socket
   /run/acoustic-vocab/<id>/state.sock --port <publisher_port> --seconds 3600`
   and the same for `commands.sock`/`command_port`). Declare the trace policy
   with its rationale before the run (for example from a separate idle-jitter
   trace), compute `policy_sha256`, then run
   `python -m isaac.stations.live --clients <private>/clients.json --policy <policy> --policy-sha256 <hash> --output <fresh>`.
   The client manifest lists per station the config file and hash, the run's
   `registry.json` and hash, the `ready` control session ID and both endpoints.
   Retain `events.jsonl`, `trace-samples.csv` and `report.json` privately.
   Do not tune the tolerance after a failure; a new policy needs a new run.
10. **Stop and seal.** `systemctl stop` every station; each journal must end
    with a clean `stopped`. Record every journal SHA-256 and the summary counts.
11. **Review.** Route check and firewall/switch review by the network owner; the
    acceptance criteria in #57 remain unchecked until this evidence is reviewed.

## Test evidence

`tests/isaac/test_stations.py` (26), `test_station_supervision.py` and
`test_station_trace.py` cover config refusal, fleet uniqueness, namespace route
shapes, the synthetic four-station matrix, reverse-order shutdown on signal,
failed start and cleanup errors, signal during startup, restart identity and
journal tamper/foreign/partial refusal, every preflight pin mismatch, gateway
ownership/mode/peer rules, systemd quoting round trips, unit text bound to the
exact plan, coverage and tolerance arithmetic, a transient excursion that
endpoint snapshots miss but the trace catches, coverage gaps, sampler failure
and the live adapters with fake connections. Windows skips the three Linux-only
tests (real SIGTERM, the gateway relay with `SO_PEERCRED`, unit CLI with POSIX
paths) with explicit reasons; they passed on a WSL Ubuntu Python 3.14 without
`websockets`, which is not the approved Isaac image or a station host. These are
not Isaac processes. GPU/driver inventory, peak VRAM and step-time CSV under
final N-station load, one-hour stability, live cross-talk, packet capture,
restart neutrality and independent network review remain pending.
